"""Projects, messy-folder scanning, clean naming, tags and file organisation."""
import json
import re
import shutil
import time
import unicodedata
import uuid
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TIT2, TPE1, TCON, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover

from .config import AUDIO_EXT, MUSIC

# ---------------------------------------------------------------- names

JUNK = [
    r"\[?\(?\s*free\s*(download|dl)\s*\)?\]?", r"\(\s*official\s*(music\s*)?(video|audio|visualizer)\s*\)",
    r"\[\s*official\s*(music\s*)?(video|audio)\s*\]", r"\(\s*(audio|lyrics?|letra|video\s*oficial|hq|hd)\s*\)",
    r"\[\s*(audio|lyrics?|hq|hd|out now|premiere)\s*\]", r"\b\d{3}\s*kbps\b", r"\bwww\.\S+", r"\bhttps?://\S+",
    r"\(\s*clip\s*officiel\s*\)", r"\[\s*[a-z0-9]{11}\s*\]",  # youtube ids
]
MIX_WORDS = r"remix|edit|mix|vip|mashup|bootleg|rework|flip|version|extended|dub|refix|cover|remake"


def clean_text(s):
    s = unicodedata.normalize("NFC", s or "")
    s = s.replace("_", " ")
    for pat in JUNK:
        s = re.sub(pat, " ", s, flags=re.I)
    s = re.sub(r"\s*\|\s*.*$", "", s)                 # "Title | Label promo"
    s = re.sub(r"\(\s*\)|\[\s*\]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—.")
    return s


def strip_track_number(s):
    return re.sub(r"^\s*(\d{1,3}|[A-D]\d)\s*[-._)]\s*", "", s)


def nice_case(s):
    """'karol g - tusa' -> 'Karol G - Tusa'; stylised names with capitals are kept as they are."""
    if s and s == s.lower():
        s = re.sub(r"\b([a-zà-ÿ])", lambda m: m.group(1).upper(), s)
        s = re.sub(r"\b(Dj|Vip|Ft|Feat|X)\b", lambda m: {"Dj": "DJ", "Vip": "VIP", "Ft": "ft", "Feat": "feat", "X": "x"}[m.group(1)], s)
    return s


def split_name(stem):
    """'01. Artist - Title (Remix) [Free DL]' -> (artist, title)."""
    s = clean_text(strip_track_number(stem))
    s = s.replace(" – ", " - ").replace(" — ", " - ")
    if " - " in s:
        a, t = s.split(" - ", 1)
        return a.strip(), t.strip()
    return "", s


def looks_junky(s):
    return (not s or len(s) < 2
            or re.fullmatch(r"(track|piste|audio|unknown|inconnu|untitled|sans titre|new recording|enregistrement)[\s_-]*\d*", s, re.I)
            or re.fullmatch(r"[\d\s_-]+", s)
            or s.lower() in {"unknown artist", "artiste inconnu", "various artists"})


def propose_name(path, tags):
    stem = Path(path).stem
    fa, ft = split_name(stem)
    ta, tt = clean_text(tags.get("artist", "")), clean_text(tags.get("title", ""))
    # title tags from SoundCloud/YouTube rips often hold "Artist - Title"
    if tt and " - " in tt and not ta:
        ta, tt = split_name(tt)
    artist = ta if not looks_junky(ta) else fa
    title = tt if not looks_junky(tt) else ft
    if artist and title.lower().startswith(artist.lower() + " - "):
        title = title[len(artist) + 3:]
    confident = bool(artist) and bool(title) and not looks_junky(title)
    return nice_case(artist), nice_case(title), confident


def identifiable(artist, title):
    """Enough information to look the track up online."""
    return bool(artist) and not looks_junky(artist) and not looks_junky(title)


def safe_filename(s):
    s = re.sub(r'[/\\:*?"<>|]', "-", s)
    return re.sub(r"\s+", " ", s).strip(" .")[:180]


PLAIN_VERSION = r"\s*(clean\s*)?(original|extended|radio|club|clean|main)?\s*(mix|edit|version)?\s*"


def is_remix(title):
    """'(Kowski Edit)', '(HÄWK VIP Edit)', '(Remix)' count; '(Original Mix)', '(Radio Edit)' don't."""
    for grp in re.findall(r"[\(\[]([^\)\]]+)[\)\]]", title):
        g = grp.lower()
        if re.fullmatch(PLAIN_VERSION, g) or g.startswith("feat"):
            continue
        if re.search(r"remix|edit|mashup|bootleg|vip|rework|flip|refix|remake|\bmix\b", g):
            return True
    return False


def core_title(title):
    t = re.sub(r"[\(\[].*?[\)\]]", "", title).lower()
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


# ---------------------------------------------------------------- tags

def read_tags(path):
    out = {"artist": "", "title": "", "comment": "", "has_cover": False, "bitrate": 0, "duration": 0.0,
           "sample_rate": 0, "kind": ""}
    try:
        f = mutagen.File(path)
    except Exception:
        return out
    if f is None:
        return out
    info = getattr(f, "info", None)
    if info:
        out["bitrate"] = int(getattr(info, "bitrate", 0) or 0) // 1000
        out["duration"] = float(getattr(info, "length", 0) or 0)
        out["sample_rate"] = int(getattr(info, "sample_rate", 0) or 0)
    tags = f.tags
    if isinstance(f, MP4):
        out["kind"] = "AAC audio file"
        t = tags or {}
        out["artist"] = (t.get("\xa9ART") or [""])[0]
        out["title"] = (t.get("\xa9nam") or [""])[0]
        out["comment"] = (t.get("\xa9cmt") or [""])[0]
        out["has_cover"] = bool(t.get("covr"))
    elif isinstance(f, FLAC):
        out["kind"] = "FLAC File"
        out["artist"] = (f.get("artist") or [""])[0]
        out["title"] = (f.get("title") or [""])[0]
        out["comment"] = (f.get("comment") or f.get("description") or [""])[0]
        out["has_cover"] = bool(f.pictures)
    elif tags is not None and hasattr(tags, "getall"):  # ID3 (mp3, wav, aiff)
        out["kind"] = {".mp3": "MP3 File", ".wav": "WAV File"}.get(Path(path).suffix.lower(), "AIFF File")
        out["artist"] = str(tags.get("TPE1", "") or "")
        out["title"] = str(tags.get("TIT2", "") or "")
        c = tags.getall("COMM")
        out["comment"] = str(c[0]) if c else ""
        out["has_cover"] = bool(tags.getall("APIC"))
    else:
        out["kind"] = Path(path).suffix.upper().strip(".") + " File"
        try:
            out["artist"] = (f.get("artist") or [""])[0]
            out["title"] = (f.get("title") or [""])[0]
        except Exception:
            pass
    return out


def read_cover(path):
    try:
        f = mutagen.File(path)
    except Exception:
        return None, None
    if isinstance(f, MP4):
        c = (f.tags or {}).get("covr")
        if c:
            return bytes(c[0]), "image/png" if c[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
    elif isinstance(f, FLAC):
        if f.pictures:
            return f.pictures[0].data, f.pictures[0].mime
    elif f is not None and f.tags is not None and hasattr(f.tags, "getall"):
        a = f.tags.getall("APIC")
        if a:
            return a[0].data, a[0].mime
    return None, None


def _id3_for(path):
    f = mutagen.File(path)
    if f.tags is None:
        f.add_tags()
    return f


def write_tags(path, artist=None, title=None, cover=None, cover_mime="image/jpeg", genre=None):
    f = mutagen.File(path)
    if isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        if artist is not None: f.tags["\xa9ART"] = [artist]
        if title is not None: f.tags["\xa9nam"] = [title]
        if genre: f.tags["\xa9gen"] = [genre]
        if cover:
            fmt = MP4Cover.FORMAT_PNG if cover_mime == "image/png" else MP4Cover.FORMAT_JPEG
            f.tags["covr"] = [MP4Cover(cover, imageformat=fmt)]
        f.save()
    elif isinstance(f, FLAC):
        if artist is not None: f["artist"] = artist
        if title is not None: f["title"] = title
        if genre: f["genre"] = genre
        if cover:
            p = Picture(); p.type = 3; p.mime = cover_mime; p.data = cover
            f.clear_pictures(); f.add_picture(p)
        f.save()
    else:
        f = _id3_for(path)
        t = f.tags
        if not hasattr(t, "setall"):
            try:
                if artist is not None: f["artist"] = artist
                if title is not None: f["title"] = title
                f.save()
            except Exception:
                pass
            return
        if artist is not None: t.setall("TPE1", [TPE1(encoding=3, text=artist)])
        if title is not None: t.setall("TIT2", [TIT2(encoding=3, text=title)])
        if genre: t.setall("TCON", [TCON(encoding=3, text=genre)])
        if cover:
            t.setall("APIC", [APIC(encoding=3, mime=cover_mime, type=3, desc="Cover", data=cover)])
        f.save()


# ---------------------------------------------------------------- scanning

def scan(sources):
    """Find every audio file under the given folders/files, however deeply nested."""
    found = []
    for s in sources:
        p = Path(s).expanduser()
        if p.is_file() and p.suffix.lower() in AUDIO_EXT:
            found.append(p)
        elif p.is_dir():
            found += [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXT
                      and not f.name.startswith("._")]
    seen, out = set(), []
    for f in found:
        r = f.resolve()
        if r not in seen:
            seen.add(r); out.append(r)
    return out


def preview_import(sources):
    rows = []
    for p in scan(sources):
        tags = read_tags(p)
        artist, title, confident = propose_name(p, tags)
        rows.append({"src": str(p), "rel": p.name, "folder": p.parent.name, "artist": artist, "title": title,
                     "confident": confident, "has_cover": tags["has_cover"], "duration": tags["duration"],
                     "bitrate": tags["bitrate"], "tag_artist": tags["artist"], "tag_title": tags["title"],
                     "comment": tags["comment"][:300]})
    mark_duplicates(rows)
    return rows


def mark_duplicates(rows, key=lambda r: (r["artist"], r["title"])):
    groups = {}
    for r in rows:
        a, t = key(r)
        groups.setdefault((core_title(a.split(",")[0].split("&")[0]), core_title(t)), []).append(r)
    for g in groups.values():
        for r in g:
            r["dup_of"] = len(g) - 1


# ---------------------------------------------------------------- projects

class Project:
    def __init__(self, root):
        self.root = Path(root)
        self.file = self.root / "setbuilder.json"
        self.data = {"name": self.root.name, "tracks": {}, "set": [], "extended": [], "blocks": [],
                     "params": {}, "created": time.time()}
        if self.file.exists():
            self.data.update(json.loads(self.file.read_text()))

    @property
    def tracks_dir(self):
        return self.root / "Tracks"

    def save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
        tmp.replace(self.file)

    def path_of(self, tid):
        return self.root / self.data["tracks"][tid]["file"]

    def refresh(self):
        """Pick up files already sitting in Tracks/ (e.g. an existing Rekordbox folder)."""
        self.tracks_dir.mkdir(parents=True, exist_ok=True)
        known = {t["file"] for t in self.data["tracks"].values()}
        for tid, t in list(self.data["tracks"].items()):
            if not (self.root / t["file"]).exists():
                del self.data["tracks"][tid]
        for p in scan([self.tracks_dir]):
            rel = str(p.relative_to(self.root.resolve()))
            if rel not in known:
                self.add_track(p)
        self.save()

    def add_track(self, p, artist=None, title=None):
        tags = read_tags(p)
        if artist is None:
            artist, title, _ = propose_name(p, tags)
        tid = uuid.uuid4().hex[:10]
        self.data["tracks"][tid] = {
            "id": tid, "file": str(Path(p).resolve().relative_to(self.root.resolve())), "artist": artist,
            "title": title, "has_cover": tags["has_cover"], "duration": tags["duration"],
            "bitrate": tags["bitrate"], "sample_rate": tags["sample_rate"], "kind": tags["kind"],
            "comment": tags["comment"][:300], "analysis": None, "known": None, "forced": False,
            "excluded": False, "cover_source": "fichier" if tags["has_cover"] else None,
        }
        return tid

    def rename_track(self, tid, artist, title):
        """Rename the file to 'Artist - Title.ext' and write the tags."""
        t = self.data["tracks"][tid]
        src = self.path_of(tid)
        base = safe_filename(f"{artist} - {title}" if artist else title)
        dst = self.tracks_dir / (base + src.suffix.lower())
        n = 2
        while dst.exists() and dst.resolve() != src.resolve():
            dst = self.tracks_dir / f"{base} ({n}){src.suffix.lower()}"; n += 1
        if dst != src:
            src.rename(dst)
        write_tags(dst, artist=artist, title=title)
        t.update(artist=artist, title=title, file=str(dst.resolve().relative_to(self.root.resolve())))


def import_files(project, rows, progress=lambda i, n, msg: None):
    """Copy the chosen files into the project with clean names. Originals are never touched."""
    project.tracks_dir.mkdir(parents=True, exist_ok=True)
    added = []
    for i, r in enumerate(rows):
        progress(i, len(rows), Path(r["src"]).name)
        src = Path(r["src"])
        artist, title = r["artist"].strip(), r["title"].strip()
        base = safe_filename(f"{artist} - {title}" if artist else title or src.stem)
        dst = project.tracks_dir / (base + src.suffix.lower())
        n = 2
        while dst.exists():
            dst = project.tracks_dir / f"{base} ({n}){src.suffix.lower()}"; n += 1
        shutil.copy2(src, dst)
        try:
            write_tags(dst, artist=artist, title=title)
        except Exception:
            pass
        added.append(project.add_track(dst, artist, title))
    project.save()
    return added


def list_projects(settings):
    out = []
    for root in settings.get("projects", []):
        p = Path(root)
        if (p / "setbuilder.json").exists():
            pr = Project(p)
            out.append({"root": str(p), "name": pr.data["name"], "tracks": len(pr.data["tracks"]),
                        "set": len(pr.data["set"])})
    return out


def default_project_root(name):
    return MUSIC / safe_filename(name)
