"""Paths, settings and the API key (kept in the system keychain via keyring)."""
import json
import os
import shutil
import sys
from pathlib import Path

if sys.platform == "win32":
    SUPPORT = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "SetBuilder"
else:
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
    try:
        import keyring
        return keyring.get_password(KEYCHAIN_SERVICE, "anthropic") or None
    except Exception:  # noqa: BLE001 - no usable keyring backend
        return None


def set_api_key(key):
    import keyring
    if key:
        keyring.set_password(KEYCHAIN_SERVICE, "anthropic", key)
    else:
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, "anthropic")
        except keyring.errors.PasswordDeleteError:
            pass


def which(cmd):
    return shutil.which(cmd) or next((p for p in (f"/opt/homebrew/bin/{cmd}", f"/usr/local/bin/{cmd}",
                                       *(os.path.join(os.environ.get(v, ""), *sub, cmd + ".exe")
                                         for v, sub in (("ProgramFiles", ("nodejs",)),
                                                        ("APPDATA", ("npm",)))
                                         if os.environ.get(v)))
                                      if os.path.exists(p)), None)
