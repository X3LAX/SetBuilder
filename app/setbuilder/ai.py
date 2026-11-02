"""Optional Claude help: fix unreadable track names and rate how well-known each song is."""
import json
import os
import subprocess

import anthropic

from .config import get_api_key, load_settings, which

MODEL = "claude-opus-5-5"
BATCH = 40

NAMES_SCHEMA = {
    "type": "object",
    "properties": {"tracks": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "artist": {"type": "string"},
            "title": {"type": "string"},
            "confident": {"type": "boolean"},
        },
        "required": ["id", "artist", "title", "confident"],
        "additionalProperties": False,
    }}},
    "required": ["tracks"],
    "additionalProperties": False,
}

KNOWN_SCHEMA = {
    "type": "object",
    "properties": {"tracks": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "known": {"type": "integer", "minimum": 0, "maximum": 10},
            "original": {"type": "string"},
        },
        "required": ["id", "known", "original"],
        "additionalProperties": False,
    }}},
    "required": ["tracks"],
    "additionalProperties": False,
}

NAMES_PROMPT = """You clean up DJ music libraries. Each item below is an audio file from a messy download folder: \
its file name, parent folder, embedded tags and a guess from simple rules.

Return the real artist and title for each, formatted the way a DJ store would list it:
- artist: main artists joined with ", " and " & " (featured artists go in the title as "(feat. X)").
- title: song name, then the version in parentheses, e.g. "Dákiti (HÄWK VIP Edit)", "Pa Lante (Extended Mix)".
- Remove junk: track numbers, "Free Download", "Official Video", bitrates, URLs, uploader names, emojis.
- Keep accents and the original capitalisation of the artist's name.
- confident = false when the file gives too little to be sure.
Keep every id exactly as given."""

KNOWN_PROMPT = """You help a DJ prepare a Latin / tech-house party set. For each track, rate from 0 to 10 how \
well a general party crowd knows the ORIGINAL song it is built on (10 = global hit everyone sings, \
5 = known to Latin music fans, 0 = underground club track nobody would recognise). A remix of a big hit scores \
like the hit. Put the original song as "Artist - Title" in `original` (empty if it is an original club track). \
Keep every id exactly as given."""


class AIUnavailable(Exception):
    pass


# ------------------------------------------------------------ Claude subscription (through Claude Code)

def _claude_bin():
    return which("claude")


def _clean_env():
    # never inherit another Claude session's settings (e.g. when started from a terminal running Claude Code)
    return {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}


def subscription_status():
    exe = _claude_bin()
    if not exe:
        return {"installed": False, "logged_in": False}
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=20,
                           env=_clean_env(), stdin=subprocess.DEVNULL)
        st = json.loads(r.stdout or "{}")
    except (subprocess.SubprocessError, ValueError, OSError):
        st = {}
    return {"installed": True, "logged_in": bool(st.get("loggedIn")), "method": st.get("authMethod", "")}


def _ask_subscription(system, items, schema):
    exe = _claude_bin()
    if not exe:
        raise AIUnavailable("Claude Code n'est pas installé (Réglages › Abonnement).")
    cmd = [exe, "-p", json.dumps(items, ensure_ascii=False), "--output-format", "json",
           "--json-schema", json.dumps(schema), "--system-prompt", system, "--tools", "",
           "--no-session-persistence", "--safe-mode", "--effort", "low"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=_clean_env(),
                           stdin=subprocess.DEVNULL, cwd=os.path.expanduser("~"))
    except subprocess.TimeoutExpired as e:
        raise AIUnavailable("Claude Code n'a pas répondu à temps.") from e
    try:
        out = json.loads(r.stdout)
    except ValueError as e:
        raise AIUnavailable(f"Claude Code : {(r.stderr or r.stdout).strip()[:200]}") from e
    if out.get("is_error"):
        msg = str(out.get("result") or out.get("subtype") or "erreur")
        if "login" in msg.lower() or "auth" in msg.lower():
            msg = "abonnement non connecté ou session expirée. Réglages › Connecter mon abonnement."
        raise AIUnavailable(f"Claude Code : {msg[:200]}")
    data = out.get("structured_output")
    if data is None:  # fall back to the text answer
        text = out.get("result", "")
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    return data["tracks"]


# ------------------------------------------------------------ Claude API (key)

def _client():
    key = get_api_key()
    if not key:
        raise AIUnavailable("Aucune clé API Claude. Ajoute-la dans Réglages.")
    return anthropic.Anthropic(api_key=key)


def _ask_api(client, system, items, schema):
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
    )
    if resp.stop_reason == "refusal":
        return []
    if resp.stop_reason == "max_tokens":
        raise AIUnavailable("Réponse de l'IA tronquée, réessaie avec moins de morceaux.")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return json.loads(text)["tracks"]


def provider():
    """'subscription' or 'api' according to the setting; 'auto' prefers the subscription."""
    choice = load_settings().get("ai_provider", "auto")
    if choice == "auto":
        if subscription_status()["logged_in"]:
            return "subscription"
        return "api" if get_api_key() else None
    return choice


def available():
    p = provider()
    if p == "subscription":
        return subscription_status()["logged_in"]
    return p == "api" and bool(get_api_key())


def _run(system, items, schema, progress):
    prov = provider()
    if prov is None:
        raise AIUnavailable("IA non configurée : connecte ton abonnement Claude ou ajoute une clé API dans Réglages.")
    ask = (lambda chunk: _ask_subscription(system, chunk, schema)) if prov == "subscription" else None
    if ask is None:
        client = _client()
        ask = lambda chunk: _ask_api(client, system, chunk, schema)  # noqa: E731
    out = {}
    for i in range(0, len(items), BATCH):
        progress(i, len(items), "IA en cours…")
        try:
            for r in ask(items[i:i + BATCH]):
                out[r["id"]] = r
        except anthropic.AuthenticationError as e:
            raise AIUnavailable("Clé API Claude refusée.") from e
        except anthropic.RateLimitError as e:
            raise AIUnavailable("Limite de l'API atteinte, réessaie dans une minute.") from e
        except anthropic.APIConnectionError as e:
            raise AIUnavailable("Pas de connexion à l'API Claude.") from e
        except anthropic.APIStatusError as e:
            raise AIUnavailable(f"Erreur de l'API Claude ({e.status_code}).") from e
    progress(len(items), len(items), "IA terminée")
    return out


def clean_names(items, progress=lambda i, n, m: None):
    """items: [{id, file, folder, tag_artist, tag_title, guess_artist, guess_title}] -> {id: {artist, title, confident}}"""
    return _run(NAMES_PROMPT, items, NAMES_SCHEMA, progress)


def rate_known(items, progress=lambda i, n, m: None):
    """items: [{id, artist, title}] -> {id: {known, original}}"""
    return _run(KNOWN_PROMPT, items, KNOWN_SCHEMA, progress)


def test_key():
    _client().models.retrieve(MODEL)
    return True


def test_subscription():
    return _ask_subscription("Reply with the given id and known=0, original=''.",
                             [{"id": "t", "artist": "test", "title": "test"}], KNOWN_SCHEMA)
