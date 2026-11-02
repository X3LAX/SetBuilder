"""Local HTTP API used by the SetBuilder window."""
import re
import subprocess
import threading
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import ai, analysis, covers, export, library, planner, rekordbox_usb, soundcloud, spotify, usb
from .config import get_api_key, load_settings, save_settings, set_api_key

WEB = Path(__file__).resolve().parent.parent / "web"
app = FastAPI()
LOCK = threading.RLock()
PROJECTS = {}
JOBS = {}
PORT = {"value": 47615}   # set by main.py; Spotify's redirect URI must match it


# ------------------------------------------------------------ helpers

def project(root):
    root = str(Path(root).expanduser())
    with LOCK:
        if root not in PROJECTS:
            if not Path(root).exists():
                raise HTTPException(404, "Dossier introuvable")
            PROJECTS[root] = library.Project(root)
        return PROJECTS[root]


def register(root):
    s = load_settings()
    if root not in s["projects"]:
        s["projects"].insert(0, root)
        save_settings(s)


STRONG_SPLIT = re.compile(r"\s*(?:,|\+|/|\bx\b|\bvs\.?|\bfeat\.?|\bft\.?|\bfeaturing\b)\s*", re.I)


def _segments(t):
    segs = STRONG_SPLIT.split(t["artist"])
    segs += re.findall(r"\((?:feat\.?|ft\.?)\s*([^)]+)\)", t["title"], re.I)
    return [x.strip() for x in segs if x and x.strip()]


def artist_groups(tracks):
    """'A & B' pairs that come back in several tracks are duos (Tom & Collins), not two artists."""
    counts, solo = {}, set()
    for t in tracks:
        for seg in _segments(t):
            parts = [x.strip() for x in seg.split("&")]
            if len(parts) == 1:
                solo.add(parts[0].lower())
            for a, b in zip(parts, parts[1:]):
                k = (a.lower(), b.lower())
                counts[k] = counts.get(k, 0) + 1
    # a member who also releases alone (Bad Bunny) is an artist of their own, not half of a duo
    return {f"{a} & {b}" for (a, b), n in counts.items() if n >= 2 and a not in solo and b not in solo}


def split_artists(t, groups=frozenset()):
    out = []
    for seg in _segments(t):
        parts = [x.strip() for x in seg.split("&") if x.strip()]
        i = 0
        while i < len(parts):
            if i + 1 < len(parts) and f"{parts[i]} & {parts[i + 1]}".lower() in groups:
                out.append(f"{parts[i]} & {parts[i + 1]}"); i += 2
            else:
                out.append(parts[i]); i += 1
    return out


def pref_weights(p):
    """Preferred artists of this project, best first -> weight 10 down to 6."""
    chosen = p.data.get("artists", [])
    n = max(1, len(chosen) - 1)
    return {a.lower(): round(10 - 4 * i / n, 1) for i, a in enumerate(chosen)}


def pref_score(t, prefs, groups):
    names = {a.lower() for a in split_artists(t, groups)}
    return max((w for a, w in prefs.items() if a in names), default=0)


def enriched(p):
    """Track dicts with analysis, energy, priority score and flags, as the planner and UI need them."""
    s = load_settings()
    prefs = pref_weights(p)
    groups = artist_groups(p.data["tracks"].values())
    ts = [dict(t) for t in p.data["tracks"].values()]
    an = {t["id"]: t["analysis"] for t in ts if t.get("analysis")}
    en = analysis.energy_scores(an)
    rows = []
    for t in ts:
        a = t.get("analysis") or {}
        t["bpm"] = t.get("bpm_override") or a.get("bpm") or 0
        t["camelot"] = t.get("key_override") or a.get("camelot") or "?"
        t["first_beat"] = a.get("first_beat", 0.0)
        t["energy"] = en.get(t["id"], 0.0)
        t["duration"] = a.get("duration") or t.get("duration") or 0
        t["play"] = planner.play_minutes(t["duration"])
        t["remix"] = library.is_remix(t["title"])
        pref = pref_score(t, prefs, groups)
        t["known"] = max(t.get("known") or 0, pref)
        t["preferred"] = pref > 0
        t["known_source"] = "IA" if p.data["tracks"][t["id"]].get("known") is not None else "artistes préférés"
        t["score"] = (0.6 * t["known"] + 0.2 * pref   # chosen artists also beat equally "known" ones
                      + (s["remix_bonus"] * (1.0 if t["known"] >= 4 else 0.5) if t["remix"] else 0))
        t["family"] = library.core_title(t["artist"].split(",")[0].split("&")[0]) + "|" + library.core_title(t["title"])
        t["short"] = t["duration"] < 110
        t["quiet"] = bool(a) and a.get("loud_db", -9) < -13
        t["analysed"] = bool(a)
        t.pop("analysis", None)
        rows.append(t)
    fams = {}
    for t in rows:
        fams.setdefault(t["family"], []).append(t)
    for g in fams.values():
        g.sort(key=lambda t: (-t["score"], -t["duration"]))
        for i, t in enumerate(g):
            t["duplicate"] = len(g) > 1
            t["duplicate_in_set"] = i > 0
    return rows


def project_view(p):
    ts = enriched(p)
    by = {t["id"]: t for t in ts}
    set_ids = [i for i in p.data["set"] if i in by]
    trans = [planner.transition_level(by[a], by[b]) for a, b in zip(set_ids, set_ids[1:])]
    blocks = [{"name": b["name"], "ids": [i for i in b["ids"] if i in by]} for b in p.data.get("blocks", [])]
    return {"root": str(p.root), "name": p.data["name"], "tracks": ts, "set": set_ids, "transitions": trans,
            "blocks": blocks, "params": p.data.get("params", {}),
            "set_minutes": round(sum(by[i]["play"] for i in set_ids) / 60, 1)}


def start_job(fn, *args):
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "state": "running", "done": 0, "total": 0, "message": "", "result": None, "error": None}
    JOBS[jid] = job

    def progress(i, n, msg=""):
        job.update(done=i, total=n, message=msg)

    def run():
        try:
            job["result"] = fn(progress, *args)
            job["state"] = "done"
        except (ai.AIUnavailable, spotify.SpotifyError) as e:
            job.update(state="error", error=str(e))
        except Exception as e:  # noqa: BLE001 - surface any failure in the UI
            traceback.print_exc()
            job.update(state="error", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=run, daemon=True).start()
    return {"job": jid}


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    if jid not in JOBS:
        raise HTTPException(404)
    return JOBS[jid]


# ------------------------------------------------------------ settings & projects

@app.get("/api/state")
def state():
    s = load_settings()
    a = ai.status()
    return {"settings": s, "has_key": a["has_key"], "ai": a, "projects": library.list_projects(s),
            "default_root": str(library.MUSIC)}


def _terminal(command):
    """Run a command in a visible Terminal window (sign-ins need the user)."""
    script = f'tell application "Terminal" to do script "{command}"\ntell application "Terminal" to activate'
    subprocess.run(["osascript", "-e", script])


AI_TOOLS = {
    "claude": {"install": "brew install --cask claude-code", "login": "{exe} auth login --claudeai"},
    "codex": {"install": "brew install --cask codex", "login": "{exe} login"},
}


@app.post("/api/ai/{action}/{name}")
def ai_action(action: str, name: str):
    if action == "test":
        try:
            ai.test_provider(name)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:300]}
    if name not in AI_TOOLS or action not in ("install", "login"):
        raise HTTPException(404)
    from .config import which
    _terminal(AI_TOOLS[name][action].format(exe=which(name) or name))
    return {"ok": True}


@app.post("/api/settings")
def update_settings(body: dict):
    s = load_settings()
    for k in ("remix_bonus", "windows_root", "fetch_covers", "use_ai", "ai_provider",
              "codex_model", "spotify_client_id", "ai_level_names", "ai_level_similar"):
        if k in body:
            s[k] = body[k]
    save_settings(s)
    return state()


@app.post("/api/key")
def update_key(body: dict):
    set_api_key((body.get("key") or "").strip())
    return {"has_key": bool(get_api_key())}


@app.post("/api/key/test")
def check_key():
    try:
        ai.test_key()
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


@app.post("/api/projects/new")
def new_project(body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Donne un nom au projet")
    root = Path(body.get("root") or library.default_project_root(name)).expanduser()
    if (root / "setbuilder.json").exists():
        raise HTTPException(400, f"Un projet existe déjà dans {root}")
    root.mkdir(parents=True, exist_ok=True)
    p = library.Project(root)
    p.data["name"] = name
    p.refresh()
    with LOCK:
        PROJECTS[str(root)] = p
    register(str(root))
    return project_view(p)


@app.post("/api/projects/open")
def open_project(body: dict):
    root = Path(body["root"]).expanduser()
    if not (root / "setbuilder.json").exists() and not (root / "Tracks").is_dir():
        raise HTTPException(400, "Ce dossier n'a pas de sous-dossier « Tracks ». Crée plutôt un nouveau projet "
                                 "et importe ce dossier dedans.")
    p = project(root)
    p.refresh()
    register(str(root))
    return project_view(p)


@app.post("/api/projects/forget")
def forget_project(body: dict):
    s = load_settings()
    s["projects"] = [r for r in s["projects"] if r != body["root"]]
    save_settings(s)
    return state()


@app.get("/api/artists")
def artists(root: str):
    """Every artist of the project with its number of tracks, for the per-set preference picker."""
    p = project(root)
    counts = {}
    groups = artist_groups(p.data["tracks"].values())
    for t in p.data["tracks"].values():
        for a in dict.fromkeys(split_artists(t, groups)):
            key = a.lower()
            name, n = counts.get(key, (a, 0))
            counts[key] = (name, n + 1)
    items = sorted(counts.values(), key=lambda x: (-x[1], x[0].lower()))
    return {"all": [{"name": n, "count": c} for n, c in items], "chosen": p.data.get("artists", [])}


@app.post("/api/artists")
def save_artists(body: dict):
    p = project(body["root"])
    with LOCK:
        p.data["artists"] = [a for a in dict.fromkeys(body.get("chosen", [])) if a.strip()]
        p.save()
    return project_view(p)


@app.get("/api/project")
def get_project(root: str):
    return project_view(project(root))


# ------------------------------------------------------------ import

@app.post("/api/import/preview")
def import_preview(body: dict):
    return start_job(lambda progress: library.preview_import(body["sources"]))


@app.post("/api/import/ai")
def import_ai(body: dict):
    rows = body["rows"]

    def run(progress):
        # only the names the rules could not read: keeps AI usage (and cost) minimal
        items = [{"id": str(i), "file": Path(r["src"]).name, "folder": r["folder"], "tag_artist": r["tag_artist"],
                  "tag_title": r["tag_title"], "guess_artist": r["artist"], "guess_title": r["title"]}
                 for i, r in enumerate(rows) if body.get("all") or not r.get("confident")]
        if not items:
            return rows
        res = ai.clean_names(items, progress)
        for i, r in enumerate(rows):
            if str(i) in res:
                r.update(artist=res[str(i)]["artist"], title=res[str(i)]["title"],
                         confident=res[str(i)]["confident"], by_ai=True)
        library.mark_duplicates(rows)
        return rows

    return start_job(run)


def _fetch_covers(p, ids, progress):
    found = 0
    for n, tid in enumerate(ids):
        t = p.data["tracks"][tid]
        progress(n, len(ids), f"Pochette : {t['artist']} - {t['title']}")
        if not library.identifiable(t["artist"], t["title"]):
            continue  # a cover found for "Track 03" would be a random one
        data, mime, source = covers.find_cover(t["artist"], t["title"], t.get("comment", ""))
        if data:
            library.write_tags(p.path_of(tid), cover=data, cover_mime=mime)
            t.update(has_cover=True, cover_source=source)
            found += 1
    p.save()
    return found


@app.post("/api/import/run")
def import_run(body: dict):
    p = project(body["root"])

    def run(progress):
        rows = [r for r in body["rows"] if not r.get("skip")]
        with LOCK:
            added = library.import_files(p, rows, progress)
        found = 0
        if body.get("fetch_covers", True):
            missing = [i for i in added if not p.data["tracks"][i]["has_cover"]]
            found = _fetch_covers(p, missing, progress)
        return {"added": len(added), "covers": found}

    return start_job(run)


# ------------------------------------------------------------ tracks

@app.post("/api/tracks/update")
def update_track(body: dict):
    p = project(body["root"])
    with LOCK:
        t = p.data["tracks"][body["id"]]
        for k in ("forced", "excluded", "bpm_override", "key_override"):
            if k in body:
                t[k] = body[k]
        if "known" in body:
            t["known"] = None if body["known"] in ("", None) else int(body["known"])
        if "artist" in body or "title" in body:
            p.rename_track(body["id"], body.get("artist", t["artist"]).strip(), body.get("title", t["title"]).strip())
        p.save()
    return project_view(p)


@app.post("/api/tracks/fix-names")
def fix_names(body: dict):
    p = project(body["root"])

    def run(progress):
        ids = body.get("ids") or list(p.data["tracks"])
        proposals, unsure = {}, set()
        for tid in ids:
            path = p.path_of(tid)
            a, ti, ok = library.propose_name(path, library.read_tags(path))
            proposals[tid] = (a, ti)
            if not ok:
                unsure.add(tid)
        if body.get("ai") and unsure:
            items = [{"id": tid, "file": p.path_of(tid).name, "folder": "", "tag_artist": p.data["tracks"][tid]["artist"],
                      "tag_title": p.data["tracks"][tid]["title"], "guess_artist": a, "guess_title": ti}
                     for tid, (a, ti) in proposals.items() if tid in unsure]
            for tid, r in ai.clean_names(items, progress).items():
                if tid in proposals:
                    proposals[tid] = (r["artist"], r["title"])
        changed = 0
        with LOCK:
            for n, (tid, (a, ti)) in enumerate(proposals.items()):
                progress(n, len(proposals), "Renommage…")
                t = p.data["tracks"][tid]
                if (a, ti) != (t["artist"], t["title"]) or not p.path_of(tid).stem.startswith(library.safe_filename(a)):
                    p.rename_track(tid, a, ti)
                    changed += 1
            p.save()
        return {"changed": changed}

    return start_job(run)


@app.post("/api/covers/fetch")
def fetch_covers(body: dict):
    p = project(body["root"])
    ids = body.get("ids") or [i for i, t in p.data["tracks"].items() if not t["has_cover"]]
    return start_job(lambda progress: {"covers": _fetch_covers(p, ids, progress), "asked": len(ids)})


@app.post("/api/covers/set")
def set_cover(body: dict):
    p = project(body["root"])
    img = Path(body["image"]).expanduser()
    mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
    library.write_tags(p.path_of(body["id"]), cover=img.read_bytes(), cover_mime=mime)
    p.data["tracks"][body["id"]].update(has_cover=True, cover_source="manuel")
    p.save()
    return project_view(p)


@app.get("/api/cover")
def get_cover(root: str, id: str):
    data, mime = library.read_cover(project(root).path_of(id))
    if not data:
        raise HTTPException(404)
    return Response(data, media_type=mime or "image/jpeg", headers={"Cache-Control": "max-age=60"})


@app.get("/api/audio")
def get_audio(root: str, id: str):
    path = project(root).path_of(id)
    types = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac", ".wav": "audio/wav",
             ".aif": "audio/aiff", ".aiff": "audio/aiff"}
    return FileResponse(path, media_type=types.get(path.suffix.lower(), "application/octet-stream"))


@app.get("/api/preview-audio")
def preview_audio(path: str):
    """Play a file that is not imported yet (import screen)."""
    p = Path(path)
    if p.suffix.lower() not in library.AUDIO_EXT or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# ------------------------------------------------------------ analysis & AI

@app.post("/api/analyze")
def run_analysis(body: dict):
    p = project(body["root"])

    def run(progress):
        ids = list(p.data["tracks"])
        paths = {str(p.path_of(i)): i for i in ids}
        res, errors = analysis.analyze_many(list(paths), progress, force=body.get("force", False))
        with LOCK:
            for path, a in res.items():
                p.data["tracks"][paths[path]]["analysis"] = a
            p.save()
        return {"analysed": len(res), "errors": {Path(k).name: v for k, v in errors.items()}}

    return start_job(run)


@app.post("/api/known/ai")
def known_ai(body: dict):
    p = project(body["root"])

    def run(progress):
        items = [{"id": t["id"], "artist": t["artist"], "title": t["title"]} for t in p.data["tracks"].values()
                 if body.get("force") or t.get("known") is None]
        if not items:
            return {"rated": 0}
        res = ai.rate_known(items, progress)
        with LOCK:
            for tid, r in res.items():
                if tid in p.data["tracks"]:
                    p.data["tracks"][tid].update(known=int(r["known"]), original=r.get("original", ""))
            p.save()
        return {"rated": len(res)}

    return start_job(run)


# ------------------------------------------------------------ set

@app.post("/api/set/generate")
def generate(body: dict):
    p = project(body["root"])
    params = body.get("params", {})

    def run(progress):
        ts = [t for t in enriched(p) if t["analysed"]]
        if not ts:
            raise ai.AIUnavailable("Lance d'abord l'analyse des morceaux.")
        progress(0, 2, "Construction du set…")
        keep = int(body.get("keep_prefix", 0))
        prefix = p.data["set"][:keep] if keep else []
        candidates = [t for t in ts if not t["duplicate_in_set"] and not t["short"]] or ts
        set_ids = planner.build_set(candidates, float(params.get("duration", 90)), float(params.get("bpm_start", 122)),
                                    float(params.get("bpm_end", 129)), params.get("shape", "progressive"),
                                    params.get("strictness", "normale"), prefix=prefix)
        progress(1, 2, "Set étendu…")
        blocks = planner.build_extended(ts, set_ids, int(params.get("known_min", 4)), float(params.get("duration", 90)))
        with LOCK:
            p.data.update(set=set_ids, blocks=blocks, params=params)
            p.save()
        return project_view(p)

    return start_job(run)


@app.post("/api/set/save")
def save_set(body: dict):
    p = project(body["root"])
    with LOCK:
        p.data["set"] = body["set"]
        if p.data.get("blocks"):
            p.data["blocks"][0]["ids"] = body["set"]
        p.save()
    return project_view(p)


@app.post("/api/set/extend")
def rebuild_extended(body: dict):
    p = project(body["root"])

    def run(progress):
        ts = [t for t in enriched(p) if t["analysed"]]
        params = p.data.get("params", {})
        blocks = planner.build_extended(ts, p.data["set"], int(params.get("known_min", 4)),
                                        float(params.get("duration", 90)))
        with LOCK:
            p.data["blocks"] = blocks
            p.save()
        return project_view(p)

    return start_job(run)


@app.get("/api/set/suggest")
def suggest(root: str, pos: int):
    p = project(root)
    ts = [t for t in enriched(p) if t["analysed"]]
    return {"ids": planner.suggestions(ts, p.data["set"], pos)}


# ------------------------------------------------------------ SoundCloud

@app.post("/api/soundcloud")
def soundcloud_download(body: dict):
    """Download into the project (then the import screen) or into any folder."""
    p = project(body["root"]) if body.get("root") else None
    url = body.get("url", "").strip()
    if not soundcloud.is_soundcloud(url):
        raise HTTPException(400, "Colle un lien SoundCloud (morceau, playlist/set, profil ou likes).")
    dest = p.root / library.DOWNLOADS / "SoundCloud" if p else Path(body.get("dest") or library.MUSIC / "SoundCloud").expanduser()

    def run(progress):
        files, errors = soundcloud.download(url, dest, progress)
        progress(0, 1, "Lecture des fichiers…")
        rows = library.preview_import(files) if (files and p) else []
        return {"rows": rows, "files": files, "errors": errors[:20], "count": len(files)}

    return start_job(run)


# ------------------------------------------------------------ Spotify

SPOTIFY_REDIRECT = "http://127.0.0.1:{port}/api/spotify/callback"


@app.get("/api/spotify/status")
def spotify_status():
    st = spotify.status()
    st["client_id"] = bool(load_settings().get("spotify_client_id"))
    st["redirect_uri"] = SPOTIFY_REDIRECT.format(port=PORT["value"])
    return st


@app.post("/api/spotify/login")
def spotify_login():
    cid = load_settings().get("spotify_client_id", "").strip()
    if not cid:
        raise HTTPException(400, "Colle d'abord le Client ID de ton app Spotify (Réglages).")
    url = spotify.login_url(cid, SPOTIFY_REDIRECT.format(port=PORT["value"]))
    subprocess.run(["open", url])
    return {"url": url}


@app.get("/api/spotify/callback")
def spotify_callback(code: str = "", state: str = "", error: str = ""):
    from fastapi.responses import HTMLResponse
    try:
        if error:
            raise spotify.SpotifyError(error)
        spotify.finish_login(code, state)
        msg = "Spotify est connecté à SetBuilder. Tu peux fermer cet onglet."
    except spotify.SpotifyError as e:
        msg = f"Échec de la connexion Spotify : {e}"
    return HTMLResponse(f"<meta charset=utf-8><body style='font:16px -apple-system;padding:40px;background:#0e0f13;"
                        f"color:#e9ebf1'><h2>SetBuilder</h2><p>{msg}</p>")


@app.post("/api/spotify/logout")
def spotify_logout():
    spotify.logout()
    return spotify_status()


@app.get("/api/spotify/playlist")
def spotify_playlist(url: str):
    pid = spotify.playlist_id(url)
    try:
        info = spotify.playlist_info(pid)
        return {"id": pid, "name": info.get("name"), "tracks": spotify.playlist_tracks(pid)}
    except spotify.SpotifyError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/spotify/similar")
def spotify_similar(body: dict):
    """Find tracks similar to a set (or to a Spotify playlist) and add them to a new or the same playlist."""
    p = project(body["root"]) if body.get("root") else None

    def run(progress):
        count = int(body.get("count", 40))
        source = body.get("source", "auto")
        from_url = (body.get("playlist") or "").strip()
        exclude = set()
        if from_url:
            pid = spotify.playlist_id(from_url)
            info = spotify.playlist_info(pid)
            progress(0, 1, f"Lecture de « {info.get('name')} »…")
            base = spotify.playlist_tracks(pid)
            if not base:
                raise spotify.SpotifyError("La playlist est vide.")
            seen = {t["uri"] for t in base}
            exclude = {library.core_title(t["title"]) for t in base}
            label = info.get("name") or "playlist"
        else:
            ts = enriched(p)
            by = {t["id"]: t for t in ts}
            base = [by[i] for i in p.data["set"] if i in by] or sorted(ts, key=lambda t: -t["score"])[:40]
            seen = set()
            label = p.data["name"]
        if p:
            exclude |= {library.core_title(t["title"]) for t in p.data["tracks"].values()}
        exclude |= {library.core_title(t["title"]) for t in base}

        uris, added, missing = [], [], []
        if body.get("include_set") and not from_url:
            for n, t in enumerate(base):
                progress(n, len(base), f"Spotify : {t['artist']} - {t['title']}")
                hit = spotify.find_track(t["artist"], t["title"])
                if hit and hit["uri"] not in seen:
                    seen.add(hit["uri"]); uris.append(hit["uri"])
                elif not hit:
                    missing.append(f"{t['artist']} - {t['title']}")

        def take(cands):
            for n, c in enumerate(cands):
                if len(added) >= count:
                    return
                ct = library.core_title(c["title"])
                if ct in exclude:
                    continue
                progress(n, len(cands), f"Spotify : {c['artist']} - {c['title']}")
                hit = spotify.find_track(c["artist"], c["title"])
                if hit and hit["uri"] not in seen:
                    seen.add(hit["uri"]); uris.append(hit["uri"]); exclude.add(ct)
                    added.append(f"{hit['artists']} - {hit['name']}")

        if source in ("auto", "ia"):
            if ai.status()["ok"]:
                take(ai.suggest_similar(base[:60], int(count * 1.6), progress))
            elif source == "ia":
                raise ai.AIUnavailable("Aucune IA connectée : choisis la source « Deezer » ou connecte une IA.")
        if source == "deezer" or len(added) < count:   # top up with Deezer's related artists
            progress(0, 1, "Deezer : artistes similaires…")
            take(spotify.deezer_similar(base, limit=count * 3))
        if not uris:
            raise ai.AIUnavailable("Aucun nouveau morceau trouvé sur Spotify.")
        if from_url and body.get("target", "same") == "same":
            spotify.add_tracks(pid, uris)
            url = info.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/playlist/{pid}"
        else:
            name = body.get("name") or f"{label} · similaires"
            url = spotify.create_playlist(name, f"Créée par SetBuilder à partir de « {label} ».", uris,
                                          public=bool(body.get("public")))["url"]
        return {"url": url, "similar": added, "total": len(uris), "missing": missing}

    return start_job(run)


# ------------------------------------------------------------ USB (CDJ / XDJ folder mode)

@app.get("/api/usb/volumes")
def usb_volumes():
    return {"volumes": usb.list_volumes()}


@app.post("/api/usb/export")
def usb_export(body: dict):
    p = project(body["root"])

    def run(progress):
        ts = enriched(p)
        by = {t["id"]: dict(t, path=str(p.path_of(t["id"]))) for t in ts}
        set_ids = [i for i in p.data["set"] if i in by]
        if not set_ids:
            raise ai.AIUnavailable("Génère d'abord un set.")
        playlists = [(f"Set {round(sum(by[i]['play'] for i in set_ids) / 60)} min", set_ids)]
        if body.get("extended", True):
            rest = [i for b in p.data.get("blocks", [])[1:] if not b["name"].startswith("À éviter")
                    for i in b["ids"] if i in by]
            if rest:
                playlists.append(("Suite (set etendu)", rest))
        try:
            if body.get("mode") == "rekordbox":
                if not any(v["path"] == body["volume"] for v in usb.list_volumes()):
                    raise ValueError("Clé USB introuvable : branche-la et actualise.")
                r = rekordbox_usb.export(body["volume"], p.data["name"], by, playlists,
                                         replace=bool(body.get("replace")), progress=progress)
                return {"folder": str(Path(body["volume"]) / "PIONEER"), "copied": r["tracks"], "total": r["tracks"],
                        "mode": "rekordbox"}
            return usb.export(body["volume"], p.data["name"], by, playlists, bool(body.get("convert_flac")), progress)
        except (ValueError, AssertionError) as e:
            raise ai.AIUnavailable(f"Export interrompu : {e}") from e

    return start_job(run)


@app.post("/api/usb/eject")
def usb_eject(body: dict):
    try:
        usb.eject(body["volume"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


# ------------------------------------------------------------ export

@app.post("/api/export")
def do_export(body: dict):
    p = project(body["root"])
    s = load_settings()
    ts = enriched(p)
    by = {t["id"]: t for t in ts}
    name = library.safe_filename(p.data["name"])
    set_ids = [i for i in p.data["set"] if i in by]
    blocks = [{"name": b["name"], "ids": [i for i in b["ids"] if i in by]} for b in p.data.get("blocks", [])]
    extended = [i for b in blocks for i in b["ids"]]
    playlists = [(f"01 - SET {round(sum(by[i]['play'] for i in set_ids) / 60)} min", set_ids)]
    if extended:
        playlists.append(("02 - SET ETENDU (set + suite)", extended))
    out = p.root / "Export"
    out.mkdir(exist_ok=True)
    files = []
    if body.get("mac", True):
        f = out / f"{name} - Rekordbox Mac.xml"; export.rekordbox_xml(p, ts, playlists, f); files.append(str(f))
    if body.get("windows"):
        f = out / f"{name} - Rekordbox Windows.xml"
        export.rekordbox_xml(p, ts, playlists, f, windows_root=s["windows_root"]); files.append(str(f))
    if body.get("m3u", True):
        # m3u paths are relative to the project folder
        f = p.root / f"{name} - Set.m3u8"; export.m3u8(p, by, set_ids, f); files.append(str(f))
        if extended:
            f = p.root / f"{name} - Set etendu.m3u8"; export.m3u8(p, by, extended, f); files.append(str(f))
    if body.get("html", True):
        f = out / f"{name} - Tracklist.html"
        export.tracklist_html(p, by, blocks or [{"name": "Set", "ids": set_ids}], f); files.append(str(f))
    return {"files": files, "folder": str(out)}


@app.post("/api/reveal")
def reveal(body: dict):
    subprocess.run(["open", "-R", body["path"]])
    return {"ok": True}


@app.post("/api/open")
def open_file(body: dict):
    subprocess.run(["open", body["path"]])
    return {"ok": True}


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
