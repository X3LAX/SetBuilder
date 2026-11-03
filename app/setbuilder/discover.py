"""Découverte: niche / unofficial SoundCloud remixes, edits, bootlegs and flips that look like a few seed tracks.

Candidates come from SoundCloud's related tracks, keyword searches (plus AI-written ones) and the other uploads of
the best remixers. Each one gets a trend score from its public stats, then an AI (or heuristic) style fit.
"""
import html
import math
import re
import subprocess
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone

from . import ai, library, spotify
from .config import which

MIN_SEC, MAX_SEC = 120, 540
MAX_CANDIDATES = 150
WORKERS = 16
KINDS_WANTED = {"remix", "edit", "bootleg", "flip", "mashup"}
SPOTIFY_BUDGET = 15   # seconds for the whole Spotify check
PREVIEW_WORDS = r"\b(preview|snippet|teaser|sample|clip|promo|short version)\b"
MIX_WORDS = r"\b(dj set|live set|podcast|mixtape|warm.?up|radio show|b2b|mix ?\d+|vol\.? ?\d+|episode|session)\b"
PLAIN_EDIT = r"\b(radio|clean|extended|original|club|dirty|intro|short)\s+edit\b"


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def clean_url(u):
    return (u or "").split("?")[0].rstrip("/")


# ------------------------------------------------------------ pure helpers

def parse_title(title, uploader=""):
    """'Bad Bunny - Una Velita (X Remix)' -> ('Bad Bunny', 'Una Velita (X Remix)')."""
    m = re.match(r"\s*(.+?)\s+[-–—]\s+(.+)$", title or "")
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return uploader, (title or "").strip()


def version_of(title):
    return _norm(" ".join(re.findall(r"[\(\[]([^\)\]]*)[\)\]]", title or "")))


def guess_kind(title, duration=0):
    t = (title or "").lower()
    if duration > MAX_SEC * 2 or re.search(MIX_WORDS, t) and not re.search(r"remix|edit|bootleg|flip|mashup", t):
        return "mix"
    if re.search(r"\bbootleg\b", t):
        return "bootleg"
    if re.search(r"\bflip\b", t):
        return "flip"
    if re.search(r"\bmash-?ups?\b", t):
        return "mashup"
    if re.search(r"\b(remix|rework|refix|remake|re-?edit)\b", t):
        return "remix"
    if re.search(r"\b(edit|vip)\b", t) and not re.search(PLAIN_EDIT, t):
        return "edit"
    return "original"


def age_days(ts=None, upload_date=None, now=None):
    now = now or time.time()
    if not ts and upload_date:
        try:
            ts = datetime.strptime(str(upload_date), "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    return max(0, int((now - ts) / 86400)) if ts else None


FREE_DL = re.compile(r"free\s*(download|dl)|descarga\s+gratis|t[ée]l[ée]chargement\s+gratuit", re.I)


def trend_score(plays, likes, reposts, age):
    """0-100 from plays per day since upload, like ratio, reposts and recency."""
    plays, likes, reposts = plays or 0, likes or 0, reposts or 0
    age = 365 if age is None else age
    ppd = plays / max(age, 7)
    velocity = min(1.0, math.log10(1 + ppd) / 2.5)
    like_ratio = min(1.0, (likes / plays if plays else 0) / 0.08)
    recency = 1.0 if age <= 60 else max(0.0, 1 - (age - 60) / 300)
    rep = min(1.0, math.log10(1 + reposts) / 2)
    return round(100 * (0.4 * velocity + 0.15 * like_ratio + 0.3 * recency + 0.15 * rep), 1)


def is_rising(plays, age):
    ppd = (plays or 0) / max(age if age is not None else 365, 7)
    return ppd >= 200 or (age is not None and age <= 45 and ppd >= 80)


def final_score(trend, fit):
    return round(0.55 * trend + 0.45 * fit * 10, 1)


def make_badges(c):
    b = []
    if c["kind"] in KINDS_WANTED and c.get("unofficial", True):
        b.append("Remix non officiel")
    if c["kind"] in ("edit", "bootleg", "mashup"):
        b.append(c["kind"].capitalize())
    if c.get("free_download"):
        b.append("Téléchargement gratuit")
    if is_rising(c.get("plays"), c.get("age_days")):
        b.append("En hausse")
    if c.get("age_days") is not None and c["age_days"] <= 30:
        b.append("Récent")
    return b


def heuristic_fit(c, seed_words):
    """0-10 without AI: from related tracks of a seed, and genre / tag / title words shared with the seeds."""
    words = set(_norm(" ".join([c.get("genre") or "", *c.get("tags", []), c["title"]])).split())
    common = len(words & seed_words)
    return min(10, (4 if c.get("src") == "related" else 0) + min(4, common) + (2 if c.get("src") == "uploader" else 0))


def keep_candidate(c, seeds, known, ignored):
    """Duration, previews, seeds themselves, project tracks, ignored urls."""
    if not MIN_SEC <= (c.get("duration") or 0) <= MAX_SEC:
        return False
    if re.search(PREVIEW_WORDS, c["title"].lower()) or clean_url(c["url"]) in ignored:
        return False
    _, song = parse_title(c["title"], c.get("uploader", ""))
    sig = (library.core_title(song), version_of(song))
    if clean_url(c["url"]) in {clean_url(s.get("url")) for s in seeds if s.get("url")}:
        return False
    return sig not in known and sig not in {(library.core_title(s["title"]), version_of(s["title"])) for s in seeds}


def dedupe(cands):
    seen, out = set(), []
    for c in cands:
        k = (_norm(c["title"]), _norm(c.get("uploader", "")))
        if clean_url(c["url"]) in seen or k in seen:
            continue
        seen |= {clean_url(c["url"]), k}
        out.append(c)
    return out


# ------------------------------------------------------------ yt-dlp

def _ydl(flat=True, **kw):
    import yt_dlp
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True,
                             "extract_flat": flat, "socket_timeout": 20, **kw})


_LOCAL = threading.local()


def _entries(url, flat=True):
    # one YoutubeDL per thread and mode: a fresh one looks up SoundCloud's client id again (~7 s each time)
    key = f"ydl{int(flat)}"
    if not hasattr(_LOCAL, key):
        setattr(_LOCAL, key, _ydl(flat))
    try:
        r = getattr(_LOCAL, key).extract_info(url, download=False)
    except Exception:  # noqa: BLE001 - a failed search or private track just yields nothing
        return []
    if not r:
        return []
    return [e for e in (r.get("entries") or [r]) if e]


def _cover(e):
    th = e.get("thumbnails") or []
    pick = next((t for t in th if t.get("id") == "t300x300"), th[-1] if th else None)
    return (pick or {}).get("url") or e.get("thumbnail") or ""


def to_candidate(e, src):
    url = clean_url(e.get("webpage_url") or e.get("url"))
    if "soundcloud.com/" not in url or "api.soundcloud" in url:
        return None
    ts = e.get("timestamp")
    return {"url": url, "title": html.unescape(e.get("title") or ""), "uploader": html.unescape(e.get("uploader") or ""), "cover": _cover(e),
            "duration": e.get("duration") or 0, "plays": e.get("view_count") or e.get("play_count") or 0,
            "likes": e.get("like_count") or 0, "reposts": e.get("repost_count") or 0,
            "age_days": age_days(ts, e.get("upload_date")), "genre": e.get("genre") or (e.get("genres") or [""])[0],
            "tags": e.get("tags") or [], "free_download": any(f.get("format_id") == "download"
                                                              for f in (e.get("formats") or []))
            or bool(FREE_DL.search(" ".join([e.get("title") or "", e.get("description") or "", e.get("purchase_title") or ""]))),
            "uploader_url": e.get("uploader_url") or "", "src": src, "_full": bool(e.get("formats"))}


def _full(url, src):
    es = _entries(url, flat=False)
    return to_candidate(es[0], src) if es else None


def find_on_soundcloud(artist, title):
    """The SoundCloud page of a library track (first plausible search hit), or ''."""
    want = library.core_title(title)
    for e in _entries(f"scsearch5:{artist} {title}"):
        c = to_candidate(e, "search")
        if c and want and want in library.core_title(parse_title(c["title"], c["uploader"])[1]):
            return c["url"]
    return ""


def resolve_seeds(seeds, project=None):
    """[{url}|{id}] -> [{artist, title, url, genre, tags}]."""
    out = []
    for s in seeds:
        if s.get("id") and project and s["id"] in project.data["tracks"]:
            t = project.data["tracks"][s["id"]]
            out.append({"artist": t["artist"], "title": t["title"], "url": "", "genre": "", "tags": []})
        elif s.get("url"):
            c = _full(s["url"].strip(), "seed")
            if c:
                artist, title = parse_title(c["title"], c["uploader"])
                out.append({"artist": artist, "title": title, "url": c["url"], "genre": c["genre"], "tags": c["tags"]})
    return out


# ------------------------------------------------------------ queries

ERA_WORDS = {"90s": ["90s"], "2000s": ["2000s"], "2010s": ["2010s"], "recent": []}


def det_queries(seeds, filters=None):
    qs, era = [], (filters or {}).get("era")
    if era in ERA_WORDS and era != "all":
        qs += [f"{w} {k}" for w in ERA_WORDS[era] for k in ("remix", "edit", "bootleg")]
        qs += ["throwback edit"] if era != "recent" else []
    for s in seeds:
        core, artist = library.core_title(s["title"]), library.clean_text(s["artist"]).split(",")[0].split("&")[0].strip()
        qs += [f"{core} remix", f"{core} edit", f"{core} bootleg", f"{core} flip", f"{artist} {core} remix",
               f"{artist} bootleg", f"{artist} edit"]
        if s.get("genre"):
            qs += [f"{s['genre']} edit", f"{s['genre']} bootleg"]
            qs += [f"{s['genre']} {w} edit" for w in ERA_WORDS.get(era, [])]
    return list(dict.fromkeys(q.strip() for q in qs if q.strip()))


# ------------------------------------------------------------ filters

ALL_KINDS = ["remix", "edit", "bootleg", "flip", "mashup"]


def norm_filters(f):
    f = f or {}
    kinds = [k for k in (f.get("kinds") or ALL_KINDS) if k in ALL_KINDS] or ALL_KINDS
    return {"era": f.get("era") if f.get("era") in ("90s", "2000s", "2010s", "recent") else "all",
            "original": f.get("original") if f.get("original") in ("hits", "niche") else "all",
            "uploaded": int(f.get("uploaded") or 0), "kinds": kinds}


def apply_filters(cands, f, ai_ok, now_year=None):
    """-> (kept, skipped). Era / original filters need the AI's verdict: without it they are skipped."""
    f, skipped = norm_filters(f), []
    year = now_year or datetime.now().year
    ranges = {"90s": (1990, 1999), "2000s": (2000, 2009), "2010s": (2010, 2019), "recent": (year - 3, year)}
    out = [c for c in cands if c["kind"] in f["kinds"]]
    if f["uploaded"]:
        out = [c for c in out if c.get("age_days") is not None and c["age_days"] <= f["uploaded"]]
    if f["era"] != "all":
        if ai_ok:
            lo, hi = ranges[f["era"]]
            out = [c for c in out if lo <= (c.get("orig_year") or 0) <= hi]
        else:
            skipped.append("era")
    if f["original"] != "all":
        if ai_ok:
            want = {"hits": ("hit", "known"), "niche": ("niche",)}[f["original"]]
            out = [c for c in out if c.get("orig_pop") in want]
        else:
            skipped.append("original")
    return out, skipped


def spotify_check(cands, budget=SPOTIFY_BUDGET, progress=lambda i, n, m: None):
    """Sets c['on_spotify'] with one search per track (cached 30 days), 4 workers. Stops at the first rate limit or
    after `budget` seconds; unchecked ones stay False (shown). Returns the number answered."""
    deadline, stop, answered = time.time() + budget, threading.Event(), [0]
    todo = []
    for c in cands:
        artist, song = parse_title(c["title"], c["uploader"])
        hit = spotify.cache_get(artist, song)
        if hit is None:
            todo.append((c, artist, song))
        else:
            c["on_spotify"] = hit
            answered[0] += 1

    def check(item):
        c, artist, song = item
        if stop.is_set() or time.time() > deadline:
            return
        try:
            res = spotify.quick_official(artist, song)
        except spotify.RateLimited:
            stop.set()
            return
        except Exception:  # noqa: BLE001 - unknown stays shown
            return
        if res is not None:
            c["on_spotify"] = res
            spotify.cache_put(artist, song, res)
            answered[0] += 1
    ex = ThreadPoolExecutor(4)
    futs = [ex.submit(check, it) for it in todo]
    try:
        for n in range(len(futs)):
            left = deadline - time.time()
            if left <= 0:
                break
            wait(futs[:n + 1], timeout=left)
            progress(n, len(futs), "Spotify : vérification…")
    finally:
        stop.set()
        ex.shutdown(wait=False, cancel_futures=True)
    spotify.cache_save()
    return answered[0]


# ------------------------------------------------------------ main

def discover(seeds, project=None, count=40, hide_spotify=True, use_ai=True, progress=lambda i, n, m: None, filters=None):
    t0 = time.time()
    filters = norm_filters(filters)
    progress(0, 1, "Lecture des morceaux de départ…")
    rs = resolve_seeds(seeds, project)
    if not rs:
        raise ai.AIUnavailable("Impossible de lire les morceaux de départ.")
    for s in rs:   # library seeds: find them on SoundCloud for the related tracks
        if not s["url"]:
            s["url"] = find_on_soundcloud(s["artist"], s["title"])
    ignored = {clean_url(u) for u in (project.data.get("discover_ignored", []) if project else [])}
    known = {(library.core_title(parse_title(t["title"])[1]), version_of(t["title"]))
             for t in (project.data["tracks"].values() if project else [])}

    # deterministic searches and related tracks start right away, the AI's ideas join when they arrive
    queries = det_queries(rs, filters)
    ai_ok = False
    progress(0, 1, "SoundCloud : recherche des remixes…")
    with ThreadPoolExecutor(WORKERS) as ex:
        rel = [ex.submit(_entries, s["url"] + "/recommended") for s in rs if s["url"]]
        srch = [ex.submit(_entries, f"scsearch20:{q}") for q in queries]
        if use_ai:
            try:
                extra = [q for q in ai.discover_queries(rs, progress, filters) if q not in queries][:max(8, 30 - len(queries))]
                srch += [ex.submit(_entries, f"scsearch20:{q}") for q in extra]
                queries += extra
                ai_ok = True
            except ai.AIUnavailable:
                pass
        progress(0, 1, "SoundCloud : recherche des remixes…")
        related = [to_candidate(e, "related") for f in rel for e in f.result()[:35]]
        found = [to_candidate(e, "search") for f in srch for e in f.result()]
    pool = [c for c in related + found if c and guess_kind(c["title"], c["duration"]) in KINDS_WANTED]
    pool = dedupe(pool)

    # c. snowball: other uploads of the most frequent remixers
    top = {}
    for c in pool:
        if c["uploader_url"]:
            top[c["uploader_url"]] = top.get(c["uploader_url"], 0) + 1
    best = sorted(top, key=top.get, reverse=True)[:4]
    with ThreadPoolExecutor(WORKERS) as ex:
        more = [to_candidate(e, "uploader") for f in [ex.submit(_entries, u.rstrip("/") + "/tracks") for u in best]
                for e in f.result()[:25]]
    pool = dedupe(pool + [c for c in more if c and guess_kind(c["title"], c["duration"]) in KINDS_WANTED])

    # related entries have no stats yet: cap, then look them up
    def pre(c):
        return trend_score(c["plays"], c["likes"], c["reposts"], c["age_days"]) + (30 if c["src"] == "related" else 0)
    pool = [c for c in pool if c["src"] == "related" or keep_candidate(c, rs, known, ignored)]
    pool = sorted(pool, key=pre, reverse=True)[:MAX_CANDIDATES]
    todo = [c for c in pool if not c["_full"] and not c["duration"]]
    done = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        for n, (c, full) in enumerate(zip(todo, ex.map(lambda c: _full(c["url"], c["src"]), todo))):
            progress(n, len(todo), "SoundCloud : détails des pistes…")
            if full:
                done[c["url"]] = full
    pool = [done.get(c["url"], c) for c in pool]
    pool = [c for c in pool if keep_candidate(c, rs, known, ignored)]
    n_cands = len(pool)

    # kind / unofficial / fit
    seed_words = set(_norm(" ".join(f"{s['title']} {s.get('genre', '')} {' '.join(s.get('tags', []))}"
                                  for s in rs)).split()) - {"remix", "edit", "bootleg", "the", "feat", "original", "mix"}
    verdict = {}
    if ai_ok:
        try:
            verdict = ai.classify_candidates([{k: c[k] for k in ("url", "title", "uploader", "genre", "duration")}
                                              for c in sorted(pool, key=pre, reverse=True)[:75]], rs, progress)
        except ai.AIUnavailable:
            ai_ok = False
    kept = []
    for c in pool:
        v = verdict.get(c["url"])
        c["kind"] = v["kind"] if v else guess_kind(c["title"], c["duration"])
        artist, _ = parse_title(c["title"], c["uploader"])
        c["unofficial"] = v["unofficial"] if v else _norm(c["uploader"]) not in _norm(artist)
        if c["kind"] not in KINDS_WANTED or not c["unofficial"]:
            continue
        c["fit"] = v["fit"] if v else heuristic_fit(c, seed_words)
        if v and c["fit"] < 5:   # the AI says another style (dubstep, hard techno…): not for this set
            continue
        c["reason"] = v["reason"] if v else ""
        c["orig_year"], c["orig_pop"] = (v.get("orig_year") or None, v.get("orig_pop")) if v else (None, None)
        c["trend"] = trend_score(c["plays"], c["likes"], c["reposts"], c["age_days"])
        c["score"] = final_score(c["trend"], c["fit"])
        c["on_spotify"] = False
        kept.append(c)
    kept.sort(key=lambda c: c["score"], reverse=True)
    if ai_ok:   # unclassified leftovers were not seen by the AI: keep them but behind
        kept = [c for c in kept if c["url"] in verdict] + [c for c in kept if c["url"] not in verdict]

    kept, skipped = apply_filters(kept, filters, ai_ok)

    # Spotify: an exact match means the remix is official (one cached search per track, short budget)
    try:
        spotify_on = spotify.status().get("connected")
    except Exception:  # noqa: BLE001
        spotify_on = False
    checked = 0
    if spotify_on:
        checked = spotify_check(kept[:max(count, 30)], progress=progress)
        kept = [c for c in kept if not (c["on_spotify"] and hide_spotify)]
        kept.sort(key=lambda c: c["on_spotify"])   # stable: official ones last

    kept = kept[:count]
    need = [c for c in kept if not c["_full"]]   # free download needs the formats list
    with ThreadPoolExecutor(WORKERS) as ex:
        for n, (c, full) in enumerate(zip(need, ex.map(lambda c: _full(c["url"], c["src"]), need))):
            progress(n, len(need), "SoundCloud : téléchargements gratuits…")
            c["free_download"] = bool(full and full["free_download"])
    results = []
    for c in kept:
        c["badges"] = make_badges(c)
        results.append({k: c[k] for k in ("url", "title", "duration", "plays", "likes", "reposts", "age_days", "trend",
                                          "fit", "score", "kind", "reason", "on_spotify", "free_download", "genre", "orig_year", "orig_pop")}
                       | {"artist": c["uploader"], "cover": c["cover"], "badges": c["badges"]})
    progress(1, 1, f"{len(results)} résultats en {time.time() - t0:.0f} s")
    return {"results": results, "queries": queries,
            "stats": {"candidates": n_cands, "kept": len(results), "ai": ai_ok, "filters_skipped": skipped,
                      "spotify_checked": checked}}


# ------------------------------------------------------------ preview

def preview_stream(url, seconds=60):
    """Yield mp3 chunks of ~60 s starting around 30% of the track."""
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg introuvable")
    with _ydl(False, format="bestaudio/best") as y:
        info = y.extract_info(url, download=False)
    if not info:
        raise RuntimeError("Piste introuvable")
    start = int((info.get("duration") or 0) * 0.3)
    headers = "".join(f"{k}: {v}\r\n" for k, v in (info.get("http_headers") or {}).items())
    cmd = [ffmpeg, "-v", "error", "-headers", headers, "-ss", str(start), "-i", info["url"], "-t", str(seconds),
           "-vn", "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while chunk := proc.stdout.read(32768):
            yield chunk
    finally:
        proc.kill()
        proc.wait()
