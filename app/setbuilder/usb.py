"""Copy a set to a USB stick in folder mode, readable by Pioneer CDJ / XDJ players without rekordbox.

Players browse it through FOLDER: one folder per playlist, files numbered in set order. No beat grids,
hot cues or pre-analysed waveforms: that needs a rekordbox export (see the Export screen)."""
import plistlib
import shutil
import subprocess
from pathlib import Path

from .config import which
from .library import safe_filename

OLD_PLAYER_UNSUPPORTED = {".flac"}   # CDJ-2000NXS, CDJ-900NXS, XDJ-RX... (NXS2 / 3000 / XDJ-RX2+ read FLAC)


def list_volumes():
    """External, writable volumes."""
    out = []
    for v in sorted(Path("/Volumes").iterdir()):
        try:
            if v.resolve() == Path("/"):
                continue
            r = subprocess.run(["diskutil", "info", "-plist", str(v)], capture_output=True, timeout=10)
            info = plistlib.loads(r.stdout) if r.returncode == 0 else {}
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
            continue
        if not info or info.get("Internal", True) or not info.get("WritableVolume", False):
            continue
        fs = (info.get("FilesystemType") or info.get("FilesystemName") or "").lower()
        out.append({"path": str(v), "name": v.name, "fs": fs, "free": info.get("FreeSpace") or info.get("APFSContainerFree", 0),
                    "size": info.get("TotalSize", 0),
                    "pioneer_ok": fs in ("msdos", "exfat", "hfs"),   # FAT32 / exFAT (and HFS+ on recent players)
                    "has_rekordbox": (v / "PIONEER").exists()})
    return out


def plan(tracks_by_id, playlists, convert_flac):
    """[(folder, [(src, dest_name)])] and total bytes."""
    jobs, total = [], 0
    for n, (name, ids) in enumerate(playlists, 1):
        files = []
        for i, tid in enumerate(ids, 1):
            t = tracks_by_id[tid]
            src = Path(t["path"])
            ext = ".aiff" if convert_flac and src.suffix.lower() in OLD_PLAYER_UNSUPPORTED else src.suffix.lower()
            label = f"{t['artist']} - {t['title']}" if t["artist"] else t["title"]
            files.append((src, f"{i:03d} - {safe_filename(label)[:120]}{ext}"))
            total += src.stat().st_size * (2 if ext == ".aiff" and src.suffix.lower() == ".flac" else 1)
        jobs.append((f"{n:02d} - {safe_filename(name)}", files))
    return jobs, total


def export(volume, project_name, tracks_by_id, playlists, convert_flac=False, progress=lambda i, n, m: None):
    vol = Path(volume)
    info = next((v for v in list_volumes() if v["path"] == str(vol)), None)
    if not info:
        raise ValueError("Clé USB introuvable : branche-la et actualise.")
    jobs, total = plan(tracks_by_id, playlists, convert_flac)
    if total > info["free"]:
        raise ValueError(f"Pas assez de place sur la clé : il faut {total / 1e9:.1f} Go, "
                         f"il reste {info['free'] / 1e9:.1f} Go.")
    base = vol / "SetBuilder" / safe_filename(project_name)
    ffmpeg = which("ffmpeg")
    n_all = sum(len(f) for _, f in jobs)
    done = copied = 0
    for folder, files in jobs:
        d = base / folder
        d.mkdir(parents=True, exist_ok=True)
        keep = {name for _, name in files}
        for old in d.iterdir():  # a previous export of this playlist in another order
            if old.is_file() and old.name not in keep and old.name[:3].isdigit():
                old.unlink()
        for src, name in files:
            done += 1
            progress(done, n_all, name)
            dst = d / name
            if dst.suffix == ".aiff" and src.suffix.lower() != ".aiff":
                if dst.exists():
                    continue
                if not ffmpeg:
                    raise ValueError("ffmpeg est nécessaire pour convertir les FLAC (brew install ffmpeg).")
                subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(src), "-map", "0:a", "-c:a", "pcm_s16be",
                                "-write_id3v2", "1", "-map_metadata", "0", str(dst)], check=True)
            else:
                if dst.exists() and dst.stat().st_size == src.stat().st_size:
                    continue
                shutil.copyfile(src, dst)   # no macOS extended attributes -> no "._" files on FAT
            copied += 1
    (base / "Tracklist.txt").write_text("\n".join(
        f"[{folder}]\n" + "\n".join(name for _, name in files) + "\n" for folder, files in jobs), encoding="utf-8")
    # macOS leaves AppleDouble "._" files on FAT sticks; some players list them as broken tracks
    subprocess.run(["dot_clean", "-m", str(vol / "SetBuilder")], capture_output=True)
    for junk in [vol / "._SetBuilder", *(vol / "SetBuilder").rglob("._*")]:
        if junk.is_file():
            junk.unlink()
    return {"folder": str(base), "copied": copied, "total": n_all, "bytes": total}


def eject(volume):
    r = subprocess.run(["diskutil", "eject", volume], capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"Éjection impossible : {(r.stderr or r.stdout).strip()[:150]}")
