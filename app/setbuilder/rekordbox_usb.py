"""EXPERIMENTAL: write a rekordbox-style USB stick (Device Library) without rekordbox.

Produces what CDJ / XDJ players read on a stick exported by rekordbox:
  PIONEER/rekordbox/export.pdb          tracks, artists, albums, genres, keys, playlists
  PIONEER/USBANLZ/.../ANLZ0000.DAT/.EXT beat grid, waveform preview, scrolling waveform
  Contents/SetBuilder/<project>/...      the audio files

The export.pdb layout follows the format notes of fragmede/rekordbox-pdb (MIT,
https://github.com/fragmede/rekordbox-pdb, FORMAT.md) and the ANLZ layout follows Deep Symmetry's
crate-digger analysis notes. Nothing here has been validated on hardware by its authors: test a stick
at home before relying on it, and keep the folder-mode copy as a backup.
Not affiliated with AlphaTheta / Pioneer DJ.
"""
import datetime
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

from .library import safe_filename
from .pdb_static import STATIC_ROWS

PAGE = 4096
HEADER = 0x28
NUM_TABLES = 20
NO_PAGE = 0x03FFFFFF
TRACKS, GENRES, ARTISTS, ALBUMS, LABELS, KEYS, COLORS, PL_TREE, PL_ENTRIES = range(9)
FILE_TYPES = {".mp3": 0x01, ".m4a": 0x04, ".aac": 0x04, ".flac": 0x05, ".wav": 0x0B, ".aiff": 0x0C, ".aif": 0x0C}
MAX_SLOTS = 255   # stay below the >255 slot-count encoding


def align4(n):
    return (n + 3) & ~3


def dstring(text):
    """DeviceSQL string, encoded the way rekordbox does."""
    try:
        raw = text.encode("ascii")
    except UnicodeEncodeError:
        payload = text.encode("utf-16-le")
        return struct.pack("<BHB", 0x90, len(payload) + 4, 0) + payload
    if len(raw) <= 126:
        return bytes([(len(raw) + 1) * 2 + 1]) + raw
    return struct.pack("<BHB", 0x40, len(raw) + 4, 0) + raw


# ------------------------------------------------------------ rows (bytes, allocation, index_shift offset)

def track_row(t):
    strings = ["", "", "2", "2", "", "", "ON", "ON", "", "", t["date"], "", "", "",
               t["anlz"], t["date"], t["comment"], t["title"], "", t["filename"], t["path"]]
    enc = [dstring(s) for s in strings]
    fixed = struct.pack(
        "<HHIIIIIHH12IHHHHHHBBHH",
        0x0024, 0, 0x000C0700, t["sample_rate"], 0, t["size"], 0x00100000 + t["id"], 0xAE49, 0x03DD,
        0, t["key_id"], 0, 0, 0, t["bitrate"], 0, t["tempo"], t["genre_id"], t["album_id"], t["artist_id"],
        t["id"], 0, 0, t["year"], 16, t["duration"], 0x0029, 0, 0, t["file_type"], 0x0003)
    offsets, blob, pos = [], bytearray(), 0x88
    for e in enc:
        if e[0] & 1 == 0 and pos % 4:  # long strings start 4-byte aligned
            blob += bytes(4 - pos % 4)
            pos = align4(pos)
        offsets.append(pos)
        blob += e
        pos += len(e)
    row = fixed + struct.pack("<21H", *offsets) + bytes(blob)
    return row, max(len(row), 0x88 + sum(align4(len(e)) for e in enc)) + 4, 2


def artist_row(i, name):
    s = dstring(name)
    return struct.pack("<HHIBB", 0x60, 0, i, 0x03, 0x0A) + s, align4(10 + align4(len(s))) + 4, 2


def album_row(i, name):
    s = dstring(name)
    return struct.pack("<HHIIIIBB", 0x80, 0, 0, 0, i, 0, 0x03, 0x16) + s, align4(22 + align4(len(s))) + 4, 2


def named_row(i, name):          # genres, labels
    s = dstring(name)
    return struct.pack("<I", i) + s, align4(4 + len(s)), None


def key_row(i, name):
    s = dstring(name)
    return struct.pack("<II", i, i) + s, align4(8 + len(s)), None


def playlist_row(i, name, parent=0, order=0, folder=False):
    s = dstring(name)
    return struct.pack("<IIIII", parent, 0, order, i, 1 if folder else 0) + s, align4(20 + len(s)), None


def entry_row(index, track_id, playlist_id):
    return struct.pack("<III", index, track_id, playlist_id), 12, None


# ------------------------------------------------------------ pages

def _index_page(page, table, nxt, first_data):
    b = bytearray(PAGE)
    struct.pack_into("<IIIIII", b, 0, 0, page, table, nxt, 1, 0)
    b[0x1B] = 0x64
    struct.pack_into("<HHHHHH", b, 0x1C, 0, 0, 0x1FFF, 0x1FFF, 1004, 0)
    struct.pack_into("<IIII", b, HEADER, page, first_data, NO_PAGE, 0)
    struct.pack_into("<H", b, HEADER + 18, 0x1FFF)   # matches every fresh index page observed
    for i in range(1004):
        struct.pack_into("<I", b, HEADER + 20 + 4 * i, 0x1FFFFFF8)
    return b


def _dir_bytes(n):
    return 2 * n + 4 * ((n + 15) // 16) if n else 0


def _data_pages(rows):
    """Split rows into page-sized groups."""
    pages, cur, used = [], [], 0
    for r in rows:
        if cur and (HEADER + used + r[1] + _dir_bytes(len(cur) + 1) > PAGE or len(cur) >= MAX_SLOTS):
            pages.append(cur)
            cur, used = [], 0
        cur.append(r)
        used += r[1]
    if cur:
        pages.append(cur)
    return pages


def _data_page(page, table, nxt, rows):
    b = bytearray(PAGE)
    n = len(rows)
    used = 0
    for k, (row, alloc, shift_at) in enumerate(rows):
        start = HEADER + used
        b[start:start + len(row)] = row
        if shift_at is not None:
            struct.pack_into("<H", b, start + shift_at, 0x20 * k)
        g, bit = divmod(k, 16)
        base = PAGE - g * 0x24
        struct.pack_into("<H", b, base - 6 - 2 * bit, used)
        struct.pack_into("<H", b, base - 4, struct.unpack_from("<H", b, base - 4)[0] | (1 << bit))
        struct.pack_into("<H", b, base - 2, struct.unpack_from("<H", b, base - 2)[0] | (1 << bit))
        used += alloc
    struct.pack_into("<IIIIII", b, 0, 0, page, table, nxt, 1, 0)
    b[0x18] = n & 0xFF
    struct.pack_into("<H", b, 0x19, 0x20 * n | (1 if n > 255 else 0))
    b[0x1B] = 0x24
    struct.pack_into("<HHHH", b, 0x1C, PAGE - HEADER - used - _dir_bytes(n), used, n, 0)
    return b


def build_pdb(tables):
    """tables: {table type: [(row bytes, alloc, index_shift_at)]} -> export.pdb bytes."""
    pages = {}
    extra = 1 + 2 * NUM_TABLES          # pages after the index/first-data pairs
    directory = []
    plan = {}
    for t in range(NUM_TABLES):
        groups = _data_pages(tables.get(t, []))
        ids = [2 + 2 * t] + list(range(extra, extra + len(groups) - 1)) if groups else []
        extra += max(0, len(groups) - 1)
        plan[t] = (groups, ids)
    for t in range(NUM_TABLES):
        groups, ids = plan[t]
        index = 1 + 2 * t
        if not groups:   # empty table: the index page is also the last page
            cand = 2 + 2 * t
            pages[index] = _index_page(index, t, cand, NO_PAGE)
            directory.append((t, cand, index, index))
            continue
        cand = extra
        extra += 1
        pages[index] = _index_page(index, t, ids[0], ids[0])
        for k, (pid, rows) in enumerate(zip(ids, groups)):
            pages[pid] = _data_page(pid, t, ids[k + 1] if k + 1 < len(ids) else cand, rows)
        directory.append((t, cand, index, ids[-1]))
    last_written = max(pages)
    out = bytearray(PAGE * (last_written + 1))
    struct.pack_into("<IIIIII", out, 0, 0, PAGE, NUM_TABLES, extra, 1, 1)
    for i, entry in enumerate(directory):
        struct.pack_into("<IIII", out, 0x1C + 16 * i, *entry)
    for pid, b in pages.items():
        out[pid * PAGE:(pid + 1) * PAGE] = b
    return bytes(out)


def read_pdb(data):
    """Re-read an export.pdb and check the format's invariants. Returns {table: [row bytes]}."""
    u16 = lambda o: struct.unpack_from("<H", data, o)[0]
    u32 = lambda o: struct.unpack_from("<I", data, o)[0]
    assert u32(4) == PAGE and u32(8) == NUM_TABLES, "en-tête invalide"
    tables = {}
    for i in range(NUM_TABLES):
        t, cand, first, last = struct.unpack_from("<IIII", data, 0x1C + 16 * i)
        rows, page, seen = [], first, set()
        while True:
            assert page not in seen, f"boucle dans la table {t}"
            seen.add(page)
            o = page * PAGE
            assert u32(o + 4) == page and u32(o + 8) == t, f"page {page} mal étiquetée"
            if data[o + 0x1B] == 0x24:
                n = data[o + 0x18] + 0x100 * (data[o + 0x19] & 1)
                used, free = u16(o + 0x1E), u16(o + 0x1C)
                assert free == PAGE - HEADER - used - _dir_bytes(n), f"taille libre fausse page {page}"
                offs = [u16(o + PAGE - (k // 16) * 0x24 - 6 - 2 * (k % 16)) for k in range(n)]
                assert offs == sorted(offs) and all(x % 4 == 0 for x in offs), f"offsets page {page}"
                live = 0
                for k in range(n):
                    if u16(o + PAGE - (k // 16) * 0x24 - 4) >> (k % 16) & 1:
                        end = offs[k + 1] if k + 1 < n else used
                        rows.append(data[o + HEADER + offs[k]:o + HEADER + end])
                        live += 1
                assert u16(o + 0x19) >> 5 == live, f"compte de lignes page {page}"
            else:
                assert data[o + 0x1B] == 0x64, f"type de page inconnu {page}"
            if page == last:
                assert u32(o + 0x0C) == cand, f"table {t} : page suivante != candidate"
                break
            page = u32(o + 0x0C)
        tables[t] = rows
    return tables


def track_paths(row):
    """(file path, analysis path) of a track row."""
    offs = struct.unpack_from("<21H", row, 0x5E)

    def dec(p):
        b = row[p]
        if b & 1:
            return row[p + 1:p + 1 + (b >> 1) - 1].decode("ascii")
        ln = struct.unpack_from("<H", row, p + 1)[0]
        return row[p + 4:p + ln].decode("utf-16-le" if b == 0x90 else "ascii")
    return dec(offs[20]), dec(offs[14])


# ------------------------------------------------------------ ANLZ (big-endian)

def _section(tag, header_extra, body):
    len_header = 12 + len(header_extra)
    return tag + struct.pack(">II", len_header, len_header + len(body)) + header_extra + body


def _anlz_file(sections):
    body = b"".join(sections)
    return b"PMAI" + struct.pack(">II", 0x1C, 0x1C + len(body)) + struct.pack(">IIII", 1, 0x10000, 0x10000, 0) + body


def _ppth(path):
    p = path.encode("utf-16-be") + b"\x00\x00"
    return _section(b"PPTH", struct.pack(">I", len(p)), p)


def _beat_grid(bpm, first_beat, duration):
    """Constant-tempo grid from the first downbeat; beats before it are skipped."""
    if bpm <= 0:
        return _section(b"PQTZ", struct.pack(">III", 0, 0x80000, 0), b"")
    period = 60.0 / bpm
    k = int(first_beat // period)            # beats that fit before the detected first beat
    t = first_beat - k * period
    n = (-k) % 4                             # keep the detected beat on "1" of its bar
    beats = bytearray()
    count = 0
    while t < duration:
        beats += struct.pack(">HHI", n % 4 + 1, int(round(bpm * 100)), int(round(t * 1000)))
        t += period
        n += 1
        count += 1
    return _section(b"PQTZ", struct.pack(">III", 0, 0x80000, count), bytes(beats))


def _waveforms(audio, sr=44100):
    """Monochrome preview (400 columns), tiny preview (100) and scrolling detail (150 per second).
    Heights follow loudness (RMS) so intros and breaks stand out even on heavily compressed masters."""
    a = audio.astype(np.float32)

    def rms_columns(n):
        edges = np.linspace(0, len(a), n + 1).astype(int)
        v = np.array([np.sqrt(np.mean(a[edges[i]:max(edges[i] + 1, edges[i + 1])] ** 2)) for i in range(n)])
        return v / (v.max() or 1.0)

    prev = np.clip(rms_columns(400) ** 1.5 * 31, 1, 31).astype(int)
    pwav = bytes(int(h) | (5 << 5) for h in prev)                # height | whiteness
    tiny = bytes(int(h) for h in np.clip(rms_columns(100) ** 1.5 * 15, 1, 15).astype(int))
    hop = sr // 150
    n = len(a) // hop
    blocks = np.abs(a[:n * hop]).reshape(n, hop).max(axis=1) if n else np.zeros(0)
    top = float(np.percentile(blocks, 99)) if n else 1.0
    pwv3 = bytes(int(h) | (5 << 5) for h in np.clip(blocks / (top or 1.0) * 28, 0, 31).astype(int))
    return (_section(b"PWAV", struct.pack(">II", len(pwav), 0x10000), pwav),
            _section(b"PWV2", struct.pack(">II", len(tiny), 0x10000), tiny),
            _section(b"PWV3", struct.pack(">III", 1, len(pwv3), 0x960000), pwv3))


def write_anlz(dat_path, audio_path_on_stick, bpm, first_beat, duration, audio_file):
    from .audio import load_mono
    audio = load_mono(audio_file, 44100)
    pwav, pwv2, pwv3 = _waveforms(audio)
    grid = _beat_grid(bpm, first_beat, duration)
    dat_path.parent.mkdir(parents=True, exist_ok=True)
    dat_path.write_bytes(_anlz_file([_ppth(audio_path_on_stick), grid, pwav, pwv2]))
    dat_path.with_suffix(".EXT").write_bytes(_anlz_file([_ppth(audio_path_on_stick), pwv3]))


# ------------------------------------------------------------ export

KEY_NAMES = {}
for _cam, (_root, _mode) in {
    "1A": ("G#", "m"), "2A": ("D#", "m"), "3A": ("A#", "m"), "4A": ("F", "m"), "5A": ("C", "m"), "6A": ("G", "m"),
    "7A": ("D", "m"), "8A": ("A", "m"), "9A": ("E", "m"), "10A": ("B", "m"), "11A": ("F#", "m"), "12A": ("C#", "m"),
    "1B": ("B", ""), "2B": ("F#", ""), "3B": ("C#", ""), "4B": ("G#", ""), "5B": ("D#", ""), "6B": ("A#", ""),
    "7B": ("F", ""), "8B": ("C", ""), "9B": ("G", ""), "10B": ("D", ""), "11B": ("A", ""), "12B": ("E", ""),
}.items():
    KEY_NAMES[_cam] = _root + _mode     # rekordbox stores keys as "Am", "F#", ...


def export(volume, project_name, tracks_by_id, playlists, replace=False, progress=lambda i, n, m: None):
    """tracks_by_id: enriched tracks with 'path', 'bpm', 'camelot', 'first_beat', 'duration', 'genre'...
    playlists: [(name, [track ids])]."""
    vol = Path(volume)
    pioneer = vol / "PIONEER"
    if pioneer.exists():
        if not replace:
            raise ValueError("Cette clé contient déjà une bibliothèque Rekordbox. Coche « Remplacer » pour la "
                             "sauvegarder puis la remplacer.")
        backup = vol / f"PIONEER-sauvegarde-{datetime.datetime.now():%Y%m%d-%H%M%S}"
        pioneer.rename(backup)
    used = list(dict.fromkeys(i for _, ids in playlists for i in ids))
    today = datetime.date.today().isoformat()
    folder = f"/Contents/SetBuilder/{safe_filename(project_name)}"

    artists, albums, genres, keys = {}, {}, {}, {}
    def lookup(table, name):
        if not name:
            return 0
        return table.setdefault(name, len(table) + 1)

    track_rows, tid_of = [], {}
    total = len(used)
    for n, sid in enumerate(used, 1):
        t = tracks_by_id[sid]
        src = Path(t["path"])
        progress(n, total, f"{t['artist']} - {t['title']}")
        name = safe_filename(f"{t['artist']} - {t['title']}" if t["artist"] else t["title"])[:150] + src.suffix.lower()
        stick_path = f"{folder}/{name}"
        dst = vol / stick_path.lstrip("/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not (dst.exists() and dst.stat().st_size == src.stat().st_size):
            shutil.copyfile(src, dst)
        tid = n
        tid_of[sid] = tid
        anlz = f"/PIONEER/USBANLZ/P{tid // 256:03X}/{tid:08X}/ANLZ0000.DAT"
        write_anlz(vol / anlz.lstrip("/"), stick_path, t["bpm"], t.get("first_beat", 0.0), t["duration"], src)
        row = track_row({
            "id": tid, "title": t["title"] or src.stem, "path": stick_path, "filename": name, "anlz": anlz,
            "date": today, "comment": f"SetBuilder · {t['camelot']}",
            "artist_id": lookup(artists, t["artist"]), "album_id": lookup(albums, project_name),
            "genre_id": lookup(genres, t.get("genre") or "Latin House"),
            "key_id": lookup(keys, KEY_NAMES.get(t["camelot"], "")),
            "tempo": int(round(t["bpm"] * 100)), "duration": int(t["duration"]),
            "sample_rate": int(t.get("sample_rate") or 44100), "bitrate": int(t.get("bitrate") or 0),
            "size": src.stat().st_size, "year": 0, "file_type": FILE_TYPES.get(src.suffix.lower(), 0x01),
        })
        track_rows.append(row)

    tables = {
        TRACKS: track_rows,
        ARTISTS: [artist_row(i, n) for n, i in artists.items()],
        ALBUMS: [album_row(i, n) for n, i in albums.items()],
        GENRES: [named_row(i, n) for n, i in genres.items()],
        KEYS: [key_row(i, n) for n, i in keys.items()],
        PL_TREE: [playlist_row(k + 1, name, order=k) for k, (name, _) in enumerate(playlists)],
        PL_ENTRIES: [entry_row(pos, tid_of[sid], k + 1)
                     for k, (_, ids) in enumerate(playlists) for pos, sid in enumerate(ids, 1)],
    }
    for t, rows in STATIC_ROWS.items():
        tables[t] = [(r, a, None) for r, a in rows]
    db = build_pdb(tables)
    check = read_pdb(db)    # never leave a stick with a database we can't read back
    assert len(check[TRACKS]) == len(used) and len(check[PL_ENTRIES]) == len(tables[PL_ENTRIES])
    for row in check[TRACKS]:
        audio, anlz = track_paths(row)
        assert (vol / audio.lstrip("/")).exists() and (vol / anlz.lstrip("/")).exists(), f"fichier manquant : {audio}"
    out = vol / "PIONEER" / "rekordbox" / "export.pdb"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(db)
    # macOS leaves AppleDouble "._" files on FAT/exFAT sticks; players can list them as broken tracks
    for top in ("PIONEER", "Contents") if sys.platform == "darwin" else ():
        subprocess.run(["dot_clean", "-m", str(vol / top)], capture_output=True)
        for junk in [vol / f"._{top}", *(vol / top).rglob("._*")]:
            if junk.is_file():
                junk.unlink()
    return {"tracks": len(used), "playlists": len(playlists), "pdb": str(out)}
