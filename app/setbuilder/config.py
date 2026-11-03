"""Paths, settings and secrets (API key, Spotify token) in a private file of the support folder."""
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


SECRETS_FILE = SUPPORT / "secrets.json"
_SECRETS = None


def _secrets():
    # a private file instead of the keychain: the unsigned venv Python made macOS ask the keychain
    # password at every launch; secrets saved there before are moved here once
    global _SECRETS
    if _SECRETS is None:
        try:
            _SECRETS = json.loads(SECRETS_FILE.read_text())
        except (OSError, ValueError):
            _SECRETS = {}
        if not _SECRETS.get("_migrated"):
            try:
                import keyring
                for name, service in (("anthropic", KEYCHAIN_SERVICE), ("spotify", "SetBuilder-Spotify")):
                    v = keyring.get_password(service, name)
                    if v and name not in _SECRETS:
                        _SECRETS[name] = v
                _SECRETS["_migrated"] = True
                _write_secrets()
            except Exception:  # noqa: BLE001 - no keyring backend or access refused: try again next launch
                pass
    return _SECRETS


def _write_secrets():
    tmp = SECRETS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_SECRETS))
    os.chmod(tmp, 0o600)
    tmp.replace(SECRETS_FILE)


def get_secret(name):
    return _secrets().get(name) or None


def set_secret(name, value):
    s = _secrets()
    if value:
        s[name] = value
    else:
        s.pop(name, None)
    _write_secrets()


def get_api_key():
    return os.environ.get("ANTHROPIC_API_KEY") or get_secret("anthropic")


def set_api_key(key):
    set_secret("anthropic", key)


def which(cmd):
    return shutil.which(cmd) or next((p for p in (f"/opt/homebrew/bin/{cmd}", f"/usr/local/bin/{cmd}",
                                       *(os.path.join(os.environ.get(v, ""), *sub, cmd + ".exe")
                                         for v, sub in (("ProgramFiles", ("nodejs",)),
                                                        ("APPDATA", ("npm",)))
                                         if os.environ.get(v)))
                                      if os.path.exists(p)), None)
