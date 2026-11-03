"""Official-release check through Deezer's public search (no login, about 50 requests / 5 s per IP).

Same matching rules as the Spotify one: main artist credited, core title equal, and the remix words of the
SoundCloud title must be in the Deezer title for the version to count as official.
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

import httpx

from .config import SUPPORT
from .library import core_title, is_remix
from .spotify import _artist_ok, _as_brackets, _n, _version_tokens

URL = "https://api.deezer.com/search"
CACHE_FILE = SUPPORT / "official-cache.json"
CACHE_DAYS = 30
_cache, _cache_lock = None, threading.Lock()


class Quota(Exception):
    """Deezer says stop (HTTP 429 or error code 4)."""


def _cache_key(artist, title):
    return f"{_n(artist)}|{_n(title)}"


def cache_get(artist, title):
    """True / False if checked in the last 30 days, else None."""
    global _cache
    with _cache_lock:
        if _cache is None:
            try:
                _cache = json.loads(CACHE_FILE.read_text())
            except (OSError, ValueError):
                _cache = {}
        v = _cache.get(_cache_key(artist, title))
    return v[0] if v and time.time() - v[1] < CACHE_DAYS * 86400 else None


def cache_put(artist, title, official):
    with _cache_lock:
        if _cache is None:
            return
        _cache[_cache_key(artist, title)] = [bool(official), int(time.time())]


def cache_save():
    with _cache_lock:
        if _cache is None:
            return
        old = time.time() - CACHE_DAYS * 86400
        for k in [k for k, v in _cache.items() if v[1] < old]:
            del _cache[k]
        try:
            tmp = CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_cache))
            tmp.replace(CACHE_FILE)
        except OSError:
            pass


def _search(client, q):
    """Deezer results for a query; raises Quota on 429 / quota error, returns None on other failures."""
    r = client.get(URL, params={"q": q, "limit": 25})
    if r.status_code == 429:
        raise Quota()
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    err = data.get("error") if isinstance(data, dict) else None
    if err:
        if isinstance(err, dict) and err.get("code") == 4:
            raise Quota()
        return None
    return data.get("data", [])


def official(artist, title, client=None, search=None):
    """One search request: True when this exact version is on Deezer, False when not, None if unknown.
    Raises Quota instead of waiting."""
    main = re.split(r",|&| x | feat\.?| ft\.?", artist, flags=re.I)[0].strip()
    base = re.sub(r"[\(\[].*?[\)\]]", "", title).strip()
    version = " ".join(v for v in re.findall(r"[\(\[](.*?)[\)\]]", title) if not re.match(r"(feat|ft)\b", v, re.I))
    wanted, wants_remix = _version_tokens(version), is_remix(title)
    q = f"{main} {base}".strip()   # field syntax (artist:"x" track:"y") returned nothing in tests; the plain query works
    if search:
        items = search(q)
    elif client:
        items = _search(client, q)
    else:
        with httpx.Client(timeout=8) as c:
            items = _search(c, q)
    if items is None:
        return None
    for it in items:
        names = _n(it.get("artist", {}).get("name", ""))
        name_b = _as_brackets(it.get("title", ""))
        if not _artist_ok(main, names) or (core_title(base) not in core_title(name_b) and core_title(name_b) not in core_title(base)):
            continue
        name_n = _n(name_b)
        cand = _version_tokens(" ".join(v for v in re.findall(r"[\(\[](.*?)[\)\]]", name_b) if not re.match(r"(feat|ft|with)\b", v, re.I)))
        if wanted:
            ok = wanted <= set(name_n.split()) | set(names.split())
        elif wants_remix:
            ok = is_remix(name_b)
        else:
            ok = not (cand or (is_remix(name_b) and not wants_remix))
        if ok:
            return True
    return False


def check_many(pairs, budget=15, workers=5, search=None, progress=lambda i, n, m: None):
    """pairs: [(artist, title)]. Returns ({index: True/False}, answered). Cached 30 days. On a quota answer,
    waits <= 1 s once, retries, then gives up: the rest stays unknown."""
    deadline, stop, backed = time.time() + budget, threading.Event(), [False]
    lock = threading.Lock()
    out = {}
    todo = []
    for i, (artist, song) in enumerate(pairs):
        hit = cache_get(artist, song)
        if hit is None:
            todo.append(i)
        else:
            out[i] = hit

    def run(client):
        def check(i):
            artist, song = pairs[i]
            for attempt in (0, 1):
                if stop.is_set() or time.time() > deadline:
                    return
                try:
                    res = official(artist, song, client, search)
                    break
                except Quota:
                    with lock:
                        if backed[0] or attempt:
                            stop.set()
                            return
                        backed[0] = True
                    time.sleep(0.8)
                except Exception:  # noqa: BLE001 - unknown stays shown
                    return
            else:
                return
            if res is not None:
                out[i] = res
                cache_put(artist, song, res)
        ex = ThreadPoolExecutor(workers)
        futs = [ex.submit(check, i) for i in todo]
        try:
            for n in range(len(futs)):
                left = deadline - time.time()
                if left <= 0:
                    break
                wait(futs[:n + 1], timeout=left)
                progress(n, len(futs), "Deezer : vérification…")
        finally:
            stop.set()
            ex.shutdown(wait=False, cancel_futures=True)
    if search:
        run(None)
    else:
        with httpx.Client(timeout=8) as c:
            run(c)
    cache_save()
    return dict(out), len(out)
