"""SoundCloud downloads (single tracks, playlists/sets, a user's tracks or likes) with yt-dlp."""
import re
import subprocess
from pathlib import Path

from .config import which

ROCKBOX_UNSUPPORTED = {".opus", ".ogg", ".webm"}   # Rekordbox can't read these


def is_soundcloud(url):
    return bool(re.match(r"https?://(www\.|m\.|on\.)?soundcloud\.(com|app\.goo\.gl)/", url.strip()))


def _to_m4a(path, ffmpeg):
    out = path.with_suffix(".m4a")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(path), "-map", "0:a", "-c:a", "aac", "-b:a", "256k",
                    "-map_metadata", "0", str(out)], check=True)
    path.unlink()
    return out


def download(url, dest, progress=lambda i, n, msg: None):
    """Download into `dest`; returns (files, errors). Already downloaded tracks are skipped."""
    import yt_dlp

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    ffmpeg = which("ffmpeg")
    state = {"i": 0, "n": 1, "files": [], "errors": []}

    def hook(d):
        info = d.get("info_dict") or {}
        state["n"] = info.get("n_entries") or state["n"]
        state["i"] = (info.get("playlist_index") or 1) - 1
        title = info.get("title", "")
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "").strip()
            progress(state["i"], state["n"], f"{title} {pct}")
        elif d["status"] == "finished":
            progress(state["i"] + 1, state["n"], f"{title} ✓")

    def post_hook(d):
        if d.get("status") == "finished":
            fp = d.get("info_dict", {}).get("filepath")
            if fp:
                state["files"].append(fp)

    class Log:
        def debug(self, msg): pass
        def info(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): state["errors"].append(re.sub(r"\x1b\[[0-9;]*m", "", msg)[:200])

    opts = {
        # the artist's original upload when downloads are enabled, else the best stream
        "format": "download/bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio",
        "outtmpl": str(dest / "%(uploader)s - %(title)s.%(ext)s"),
        "restrictfilenames": False,
        "windowsfilenames": True,
        "ignoreerrors": True,
        "download_archive": str(dest / ".downloaded.txt"),
        "writethumbnail": True,
        "postprocessors": [{"key": "FFmpegMetadata", "add_metadata": True},
                           {"key": "EmbedThumbnail", "already_have_thumbnail": False}],
        "progress_hooks": [hook],
        "postprocessor_hooks": [post_hook],
        "logger": Log(),
        "noprogress": True,
        "concurrent_fragment_downloads": 4,
    }
    if ffmpeg:
        opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
    progress(0, 1, "Lecture du lien SoundCloud…")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url.strip()])

    files = []
    for f in dict.fromkeys(state["files"]):
        p = Path(f)
        if not p.exists():
            continue
        if p.suffix.lower() in ROCKBOX_UNSUPPORTED and ffmpeg:
            p = _to_m4a(p, ffmpeg)
        files.append(str(p))
    return files, state["errors"]
