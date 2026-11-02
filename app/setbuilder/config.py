"""Paths, settings and the API key (kept in the macOS keychain)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

SUPPORT = Path.home() / "Library" / "Application Support" / "SetBuilder"
SUPPORT.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = SUPPORT / "settings.json"
CACHE_FILE = SUPPORT / "analysis-cache.json"
MUSIC = Path.home() / "Music"

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".mp4", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".opus", ".alac"}

DEFAULTS = {
    "projects": [],
    "remix_bonus": 1.0,
    "windows_root": "C:/DJ Music",
    "fetch_covers": True,
    "use_ai": True,
    "ai_provider": "auto",   # auto | subscription | api
}

KEYCHAIN_SERVICE = "SetBuilder-Anthropic"


def load_settings():
    s = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text()))
        except (OSError, ValueError):
            pass
    return s


def save_settings(s):
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1, ensure_ascii=False))
    tmp.replace(SETTINGS_FILE)


def get_api_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def set_api_key(key):
    subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE], capture_output=True)
    if key:
        subprocess.run(["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", "anthropic",
                        "-w", key], check=True, capture_output=True)


def which(cmd):
    return shutil.which(cmd) or next((p for p in (f"/opt/homebrew/bin/{cmd}", f"/usr/local/bin/{cmd}")
                                      if os.path.exists(p)), None)
