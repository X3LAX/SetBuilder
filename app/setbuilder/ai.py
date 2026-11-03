"""Optional AI help, kept small and cheap.

Used for exactly three things, always from a button:
  - fixing track names the rules could not read,
  - rating how well-known each song is (set priority),
  - suggesting similar tracks for a Spotify playlist.

Providers: Claude subscription (through Claude Code), ChatGPT subscription (through Codex),
or an Anthropic API key. Answers are cached so the same question is never paid twice.
"""
import json
import os
import subprocess
import tempfile
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import anthropic

from .config import SUPPORT, get_api_key, load_settings, which

# Model per quality level. Names / "known" ratings are simple lookups: the light model is enough.
# Similar-track suggestions need real music knowledge (fewer invented tracks): balanced by default.
LEVELS = {
    "eco": {"api": "claude-haiku-5-5", "claude": "haiku", "effort": "low"},
    "equilibre": {"api": "claude-sonnet-5-5", "claude": "sonnet", "effort": "low"},
    "max": {"api": "claude-opus-5-5", "claude": "opus", "effort": "medium"},
}
TASK_DEFAULT = {"names": "eco", "similar": "equilibre", "discover": "equilibre", "discover_sort": "eco"}
API_MODEL = LEVELS["eco"]["api"]
BATCH = {"api": 80, "claude": 80, "codex": 150}   # Codex has a large fixed cost per call
CACHE_FILE = SUPPORT / "ai-cache.json"
_cache_lock = threading.Lock()


class AIUnavailable(Exception):
    pass


def _schema(item_props):
    return {
        "type": "object",
        "properties": {"tracks": {"type": "array", "items": {
            "type": "object", "properties": item_props, "required": list(item_props), "additionalProperties": False,
        }}},
        "required": ["tracks"],
        "additionalProperties": False,
    }


NAMES_SCHEMA = _schema({"id": {"type": "string"}, "artist": {"type": "string"}, "title": {"type": "string"},
                        "confident": {"type": "boolean"}})
KNOWN_SCHEMA = _schema({"id": {"type": "string"}, "known": {"type": "integer"}, "original": {"type": "string"}})
SIMILAR_SCHEMA = _schema({"artist": {"type": "string"}, "title": {"type": "string"}})
QUERIES_SCHEMA = _schema({"q": {"type": "string"}})
KINDS = ["remix", "edit", "bootleg", "flip", "mashup", "original", "mix", "other"]
CLASSIFY_SCHEMA = _schema({"id": {"type": "string"}, "kind": {"type": "string", "enum": KINDS},
                           "unofficial": {"type": "boolean"}, "fit": {"type": "integer"}, "reason": {"type": "string"},
                           "orig_year": {"type": "integer"}, "orig_pop": {"type": "string", "enum": ["hit", "known", "niche"]}})

NAMES_PROMPT = """You clean up DJ music libraries. Each item is a messy audio file: file name (f), folder (d), \
embedded tags (ta/tt) and a rule-based guess (ga/gt). Return the real artist and title as a DJ store lists them: \
artists joined with ", " and " & "; title = song name then the version in parentheses, e.g. "Dákiti (HÄWK VIP Edit)". \
Drop junk (track numbers, Free Download, Official Video, bitrates, URLs, uploader names). Keep accents. \
confident=false when the file gives too little to be sure; never invent a song. Keep every id."""

KNOWN_PROMPT = """For a Latin / tech-house party DJ: rate 0-10 how well a general party crowd knows the ORIGINAL \
song each track is built on (10 = global hit everyone sings, 5 = known to Latin music fans, 0 = underground club \
track). A remix of a hit scores like the hit. original = "Artist - Title" of the original song, or "" for an \
original club track. Keep every id."""

SIMILAR_PROMPT = """You are a DJ's crate digger. Given a DJ set or playlist (artist - title, with BPM and Camelot key \
when known), suggest real, existing tracks that would fit in it: same genre, sub-genre and energy, similar tempo \
(±3 BPM), compatible keys when you know them. Work out the style from the tracks themselves (do not assume a \
genre). Strongly prefer DJ versions: when an extended mix exists, give its exact title with the version, e.g. \
"Song (Extended Mix)"; club remixes are welcome too. Mix well-known and fresh tracks. \
Never repeat a track from the list. Only suggest tracks you are confident exist on Spotify; exact artist and title."""

QUERIES_PROMPT = """You help a club DJ dig SoundCloud for niche, unofficial remixes, edits, bootlegs and flips made by \
independent producers. Given a few seed tracks, work out their scene and style, then write SoundCloud search queries \
that surface similar unofficial reworks. Mix: (1) songs that remixers in this scene love to rework, as \
"<artist> <song> remix|edit|bootleg" (real songs only), (2) genre / vibe keywords such as "afro house edit", \
"latin tech house bootleg", "guaracha flip". Short queries, no quotes, no duplicates, no DJ mixes or sets."""

GROUP_HINT = (" There are many seeds: FIRST group them by style / vibe (2-5 groups), then spread your queries across the "
              "groups, a few per group, each query faithful to one group. Never average the groups into vague queries.")

ERA_TEXT = {"90s": "the 1990s", "2000s": "the 2000s", "2010s": "the 2010s", "recent": "the last 3 years"}


def _filter_hint(filters):
    f, out = filters or {}, []
    if f.get("era") in ERA_TEXT:
        out.append(f"The ORIGINAL songs must date from {ERA_TEXT[f['era']]}, but the remixes / edits must be current, in the "
                   f"seeds' style: e.g. \"2000s reggaeton tech house edit\", or specific famous songs of that era + \"remix\".")
    if f.get("original") == "hits":
        out.append("Target famous, mainstream original songs.")
    elif f.get("original") == "niche":
        out.append("Target lesser-known, niche original songs.")
    return ("\nFilters:\n" + "\n".join(out)) if out else ""

CLASSIFY_PROMPT = """You sort SoundCloud uploads for a club DJ. Each item: title (t), uploader (u), genre (g), \
duration in seconds (d). For each, give: kind = remix | edit | bootleg | flip | mashup | original | mix (DJ mix / set / \
podcast) | other; unofficial = true when it is a rework made by someone other than the original artist and not an \
official label release; fit = 0-10, how well it matches the style of the CLOSEST seed track below (genre, energy, vibe), not the average of all seeds; \
reason = at most 8 words, in French; \
orig_year = approximate release year of the ORIGINAL song being reworked (0 if you do not know); orig_pop = how famous \
that original song is: hit (mainstream hit), known (well known in its scene), niche. Keep every id."""


# ------------------------------------------------------------ cache

def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.split())


def _load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(c):
    with _cache_lock:
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(c, ensure_ascii=False))
        tmp.replace(CACHE_FILE)


# ------------------------------------------------------------ providers

def _clean_env():
    # never inherit another assistant session's settings (e.g. when started from a terminal running one)
    return {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC", "CODEX_"))}


def _run_cli(cmd, stdin_text=None, timeout=900, cwd=None):
    try:
        return subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, timeout=timeout,
                              env=_clean_env(), cwd=cwd or os.path.expanduser("~"),
                              stdin=None if stdin_text is not None else subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        raise AIUnavailable("L'IA n'a pas répondu à temps.") from e


def claude_status():
    exe = which("claude")
    if not exe:
        return {"installed": False, "logged_in": False}
    try:
        st = json.loads(_run_cli([exe, "auth", "status"], timeout=20).stdout or "{}")
    except (ValueError, AIUnavailable):
        st = {}
    return {"installed": True, "logged_in": bool(st.get("loggedIn")),
            "method": st.get("subscriptionType") or st.get("authMethod", "")}


def codex_status():
    exe = which("codex")
    if not exe:
        return {"installed": False, "logged_in": False}
    try:
        r = _run_cli([exe, "login", "status"], timeout=20)
        out = (r.stdout + r.stderr).strip()
    except AIUnavailable:
        out = ""
    logged = "logged in" in out.lower() and "not logged" not in out.lower()
    return {"installed": True, "logged_in": logged, "method": "ChatGPT" if "chatgpt" in out.lower() else out[:40]}


def _level(task):
    lv = load_settings().get(f"ai_level_{task}", TASK_DEFAULT[task])
    return LEVELS.get(lv, LEVELS[TASK_DEFAULT[task]])


def _ask_claude(system, items, schema, level):
    exe = which("claude")
    if not exe:
        raise AIUnavailable("Claude Code n'est pas installé (Réglages).")
    r = _run_cli([exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
                  "--system-prompt", system, "--tools", "", "--model", level["claude"],
                  "--no-session-persistence", "--safe-mode", "--effort", level["effort"]],
                 stdin_text=json.dumps(items, ensure_ascii=False, separators=(",", ":")))
    try:
        out = json.loads(r.stdout)
    except ValueError as e:
        raise AIUnavailable(f"Claude Code : {(r.stderr or r.stdout).strip()[:200]}") from e
    if out.get("is_error"):
        msg = str(out.get("result") or out.get("subtype") or "erreur")
        if any(w in msg.lower() for w in ("login", "auth", "oauth")):
            msg = "abonnement non connecté ou session expirée. Réglages › Connecter."
        raise AIUnavailable(f"Claude Code : {msg[:200]}")
    data = out.get("structured_output")
    if data is None:
        text = out.get("result", "")
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    return data["tracks"]


def _ask_codex(system, items, schema, level):
    exe = which("codex")
    if not exe:
        raise AIUnavailable("Codex n'est pas installé (Réglages).")
    model = load_settings().get("codex_model", "").strip()
    with tempfile.TemporaryDirectory(prefix="setbuilder-codex-") as d:
        sp, op = os.path.join(d, "schema.json"), os.path.join(d, "out.json")
        with open(sp, "w") as f:
            json.dump(schema, f)
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-rules", "-s", "read-only",
               "-c", f'model_reasoning_effort="{level["effort"]}"', "--output-schema", sp, "-o", op, "--color", "never", "-C", d]
        if model:
            cmd += ["-m", model]
        prompt = system + "\n\nAnswer only with the JSON. Do not run any command.\n\n" + json.dumps(
            items, ensure_ascii=False, separators=(",", ":"))
        r = _run_cli(cmd + ["-"], stdin_text=prompt, cwd=d)
        text = open(op).read() if os.path.exists(op) else ""
        if not text.strip():
            err = (r.stderr or r.stdout).strip().splitlines()
            msg = err[-1] if err else "pas de réponse"
            if "login" in msg.lower() or "auth" in msg.lower():
                msg = "compte ChatGPT non connecté. Réglages › Connecter Codex."
            raise AIUnavailable(f"Codex : {msg[:200]}")
    return json.loads(text[text.find("{"):text.rfind("}") + 1])["tracks"]


def _ask_api(system, items, schema, level):
    key = get_api_key()
    if not key:
        raise AIUnavailable("Aucune clé API Claude (Réglages).")
    client = anthropic.Anthropic(api_key=key)
    req = dict(model=level["api"], max_tokens=16000, system=system,
               output_config={"effort": level["effort"], "format": {"type": "json_schema", "schema": schema}},
               messages=[{"role": "user", "content": json.dumps(items, ensure_ascii=False, separators=(",", ":"))}])
    try:
        if level["api"] == "claude-haiku-5-5":   # Haiku has no server-side refusal fallback
            resp = client.messages.create(**req)
        else:
            resp = client.beta.messages.create(**req, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    except anthropic.AuthenticationError as e:
        raise AIUnavailable("Clé API Claude refusée.") from e
    except anthropic.RateLimitError as e:
        raise AIUnavailable("Limite de l'API atteinte, réessaie dans une minute.") from e
    except anthropic.APIConnectionError as e:
        raise AIUnavailable("Pas de connexion à l'API Claude.") from e
    except anthropic.APIStatusError as e:
        raise AIUnavailable(f"Erreur de l'API Claude ({e.status_code}).") from e
    if resp.stop_reason == "refusal":
        return []
    if resp.stop_reason == "max_tokens":
        raise AIUnavailable("Réponse de l'IA tronquée, réessaie avec moins de morceaux.")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return json.loads(text)["tracks"]


ASK = {"claude": _ask_claude, "codex": _ask_codex, "api": _ask_api}
LABEL = {"claude": "ton abonnement Claude", "codex": "ton abonnement ChatGPT (Codex)", "api": "la clé API Claude"}


def provider():
    """The provider to use: the chosen one, or in 'auto' the first one that is ready."""
    choice = load_settings().get("ai_provider", "auto")
    if choice == "subscription":  # older setting name
        choice = "claude"
    if choice != "auto":
        return choice
    if claude_status()["logged_in"]:
        return "claude"
    if codex_status()["logged_in"]:
        return "codex"
    return "api" if get_api_key() else None


def status():
    c, x, k = claude_status(), codex_status(), bool(get_api_key())
    prov = provider()
    ok = {"claude": c["logged_in"], "codex": x["logged_in"], "api": k}.get(prov, False)
    return {"claude": c, "codex": x, "has_key": k, "provider": prov, "label": LABEL.get(prov, ""), "ok": ok}


def _run(system, items, schema, progress, task="names"):
    prov = provider()
    if prov is None:
        raise AIUnavailable("IA non configurée : connecte Claude, Codex ou une clé API dans Réglages.")
    out = []
    size = BATCH[prov]
    for i in range(0, len(items), size):
        progress(i, len(items), f"IA ({LABEL[prov]})…")
        out += ASK[prov](system, items[i:i + size], schema, _level(task))
    progress(len(items), len(items), "IA terminée")
    return out


# ------------------------------------------------------------ tasks

def clean_names(items, progress=lambda i, n, m: None):
    """items: [{id, file, folder, tag_artist, tag_title, guess_artist, guess_title}] -> {id: {artist, title, confident}}

    Only what the cache does not know yet is sent, with short keys to keep the prompt small."""
    cache = _load_cache()
    names = cache.setdefault("names", {})
    result, todo = {}, []
    for it in items:
        k = _norm(f"{it['file']}|{it.get('tag_artist', '')}|{it.get('tag_title', '')}")
        if k in names:
            result[it["id"]] = names[k]
        else:
            todo.append((k, {"id": it["id"], "f": it["file"], "d": it.get("folder", ""),
                             "ta": it.get("tag_artist", ""), "tt": it.get("tag_title", ""),
                             "ga": it.get("guess_artist", ""), "gt": it.get("guess_title", "")}))
    if todo:
        keys = {small["id"]: k for k, small in todo}
        for r in _run(NAMES_PROMPT, [s for _, s in todo], NAMES_SCHEMA, progress):
            if r["id"] in keys:
                entry = {"artist": r["artist"], "title": r["title"], "confident": r["confident"]}
                result[r["id"]] = names[keys[r["id"]]] = entry
        _save_cache(cache)
    return result


def rate_known(items, progress=lambda i, n, m: None):
    """items: [{id, artist, title}] -> {id: {known, original}}; cached by artist + title across projects."""
    cache = _load_cache()
    known = cache.setdefault("known", {})
    result, todo = {}, []
    for it in items:
        k = _norm(f"{it['artist']}|{it['title']}")
        if k in known:
            result[it["id"]] = known[k]
        else:
            todo.append((k, {"id": it["id"], "a": it["artist"], "t": it["title"]}))
    if todo:
        keys = {small["id"]: k for k, small in todo}
        for r in _run(KNOWN_PROMPT, [s for _, s in todo], KNOWN_SCHEMA, progress):
            if r["id"] in keys:
                entry = {"known": max(0, min(10, int(r["known"]))), "original": r["original"]}
                result[r["id"]] = known[keys[r["id"]]] = entry
        _save_cache(cache)
    return result


def suggest_similar(set_tracks, count=40, progress=lambda i, n, m: None):
    """set_tracks: [{artist, title, bpm?, camelot?}] -> [{artist, title}] in one call."""
    prov = provider()
    if prov is None:
        raise AIUnavailable("IA non configurée (Réglages).")
    lines = [f"{t['artist']} - {t['title']}" + (f" | {t['bpm']:g} BPM | {t['camelot']}" if t.get("bpm") else "")
             for t in set_tracks]
    progress(0, 1, f"IA ({LABEL[prov]}) : morceaux similaires…")
    res = ASK[prov](SIMILAR_PROMPT + f"\nSuggest {count} tracks.", [{"set": lines}], SIMILAR_SCHEMA, _level("similar"))
    progress(1, 1, "IA terminée")
    return res


def test_provider(name):
    return ASK[name](KNOWN_PROMPT, [{"id": "t", "a": "Bad Bunny", "t": "Dákiti"}], KNOWN_SCHEMA, LEVELS["eco"])


def test_key():
    anthropic.Anthropic(api_key=get_api_key()).models.retrieve(API_MODEL)
    return True


def _seed_lines(seeds):
    return [f"{x.get('artist', '')} - {x.get('title', '')}" + (f" | {x['genre']}" if x.get("genre") else "")
            for x in seeds]


def discover_queries(seeds, progress=lambda i, n, m: None, filters=None):
    """seeds: [{artist, title, genre?}] -> 12-15 SoundCloud search queries; cached by seeds."""
    lines = _seed_lines(seeds)
    cache = _load_cache()
    qs = cache.setdefault("discover_q2", {})
    hint = _filter_hint(filters)
    k = _norm("|".join(lines)) + hint
    if k in qs:
        return qs[k]
    prov = provider()
    if prov is None:
        raise AIUnavailable("IA non configurée (Réglages).")
    progress(0, 1, f"IA ({LABEL[prov]}) : idées de recherche…")
    group = GROUP_HINT if len(lines) > 3 else ""
    res = ASK[prov](QUERIES_PROMPT + group + hint + "\nWrite 14 queries.", [{"seeds": lines}], QUERIES_SCHEMA, _level("discover"))
    out = list(dict.fromkeys(r["q"].strip() for r in res if r.get("q", "").strip()))[:15]
    qs[k] = out
    _save_cache(cache)
    return out


def classify_candidates(items, seeds, progress=lambda i, n, m: None):
    """items: [{url, title, uploader, genre, duration}] -> {url: {kind, unofficial, fit, reason, orig_year, orig_pop}}; cached per url + seeds."""
    sig = _norm("|".join(_seed_lines(seeds)))
    cache = _load_cache()
    store = cache.setdefault("discover_sort3", {})
    result, todo = {}, []
    for it in items:
        k = f"{it['url']}|{sig}"
        if k in store:
            result[it["url"]] = store[k]
        else:
            todo.append(it)
    if todo:
        small = [{"id": str(n), "t": it["title"], "u": it["uploader"], "g": it.get("genre") or "",
                  "d": int(it.get("duration") or 0)} for n, it in enumerate(todo)]
        system = CLASSIFY_PROMPT + "\nSeed tracks (several styles possible: judge fit against the closest seed or group of seeds):\n" + "\n".join(_seed_lines(seeds))
        prov = provider()
        if prov is None:
            raise AIUnavailable("IA non configurée (Réglages).")
        # small batches in parallel: one big call is several times slower than 4 short ones at once
        chunks = [small[i:i + 25] for i in range(0, len(small), 25)]
        progress(0, len(small), f"IA ({LABEL[prov]}) : tri des résultats…")
        rows, level = [], _level("discover_sort")
        with ThreadPoolExecutor(4) as ex:
            for n, part in enumerate(ex.map(lambda c: ASK[prov](system, c, CLASSIFY_SCHEMA, level), chunks), 1):
                rows += part
                progress(min(len(small), 25 * n), len(small), f"IA ({LABEL[prov]}) : tri des résultats…")
        for r in rows:
            try:
                it = todo[int(r["id"])]
            except (ValueError, IndexError):
                continue
            entry = {"kind": r["kind"] if r["kind"] in KINDS else "other", "unofficial": bool(r["unofficial"]),
                     "fit": max(0, min(10, int(r["fit"]))), "reason": r["reason"][:80],
                     "orig_year": max(0, int(r.get("orig_year") or 0)),
                     "orig_pop": r.get("orig_pop") if r.get("orig_pop") in ("hit", "known", "niche") else None}
            result[it["url"]] = store[f"{it['url']}|{sig}"] = entry
        _save_cache(cache)
    return result
