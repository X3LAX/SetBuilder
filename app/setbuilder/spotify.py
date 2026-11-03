"""Spotify: sign-in (PKCE, no secret), track search and playlist creation, plus similar-track discovery."""
import base64
import hashlib
import json
import re
import secrets
import subprocess
import time
import unicodedata
from urllib.parse import urlencode

import httpx

from .config import get_secret, set_secret

from .library import core_title

AUTH = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = ("playlist-modify-private playlist-modify-public playlist-read-private playlist-read-collaborative "
          "user-read-private")

_pending = {}


class SpotifyError(Exception):
    pass


# ------------------------------------------------------------ tokens

def _load_token():
    try:
        return json.loads(get_secret("spotify") or "null")
    except ValueError:
        return None


def _save_token(tok):
    set_secret("spotify", json.dumps(tok) if tok else None)


def logout():
    _save_token(None)


# ------------------------------------------------------------ sign-in

def login_url(client_id, redirect_uri):
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    _pending[state] = (verifier, client_id, redirect_uri)
    return AUTH + "?" + urlencode({"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
                                   "code_challenge_method": "S256", "code_challenge": challenge,
                                   "state": state, "scope": SCOPES})


def finish_login(code, state):
    if state not in _pending:
        raise SpotifyError("Connexion expirée, recommence depuis l'app.")
    verifier, client_id, redirect_uri = _pending.pop(state)
    r = httpx.post(TOKEN, data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                                "client_id": client_id, "code_verifier": verifier}, timeout=20)
    if r.status_code != 200:
        raise SpotifyError(f"Spotify a refusé la connexion : {r.text[:200]}")
    tok = r.json()
    tok["client_id"] = client_id
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    _save_token(tok)


def _access_token():
    tok = _load_token()
    if not tok:
        raise SpotifyError("Spotify n'est pas connecté (Réglages).")
    if time.time() > tok.get("expires_at", 0):
        r = httpx.post(TOKEN, data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                    "client_id": tok["client_id"]}, timeout=20)
        if r.status_code != 200:
            _save_token(None)
            raise SpotifyError("Session Spotify expirée : reconnecte-toi (Réglages).")
        new = r.json()
        tok.update(access_token=new["access_token"], expires_at=time.time() + new.get("expires_in", 3600) - 60)
        if new.get("refresh_token"):
            tok["refresh_token"] = new["refresh_token"]
        _save_token(tok)
    return tok["access_token"]


def _call(method, path, **kw):
    for attempt in range(4):
        r = httpx.request(method, API + path, headers={"Authorization": f"Bearer {_access_token()}"}, timeout=20, **kw)
        if r.status_code == 429:  # rate limited
            time.sleep(min(10, int(r.headers.get("Retry-After", "2"))))
            continue
        return r
    return r


def me():
    r = _call("GET", "/me")
    if r.status_code != 200:
        raise SpotifyError(f"Spotify : {r.status_code} {r.text[:150]}")
    return r.json()


def status():
    if not _load_token():
        return {"connected": False}
    try:
        u = me()
        return {"connected": True, "name": u.get("display_name") or u.get("id"), "id": u["id"]}
    except (SpotifyError, httpx.HTTPError) as e:
        return {"connected": False, "error": str(e)[:150]}


# ------------------------------------------------------------ search & playlists

def _n(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


PLAIN_WORDS = {"mix", "edit", "remix", "version", "extended", "original", "radio", "club", "clean", "dub", "vip",
               "rework", "bootleg", "mashup", "remaster", "remastered", "the", "and", "x", "feat", "ft", "explicit",
               "single", "album", "instrumental", "s", "n", "a"}


def _version_tokens(version):
    """Distinctive words of a version: '(Kriss Reeve Remix)' -> {'kriss', 'reeve'}; '(Extended Mix)' -> {}."""
    return {w for w in _n(version).split() if w not in PLAIN_WORDS and not w.isdigit()}


def _as_brackets(name):
    """Spotify writes versions as 'Title - X Remix': turn it into 'Title (X Remix)' like file names."""
    if " - " in name:
        base, ver = name.split(" - ", 1)
        return f"{base} ({ver})"
    return name


def _artist_ok(main, names):
    """The main artist must really be credited ('San Pacho' must not match 'Diego San Diego')."""
    m = _n(main)
    if not m:
        return True
    return f" {m} " in f" {names} "


def find_track(artist, title, prefer_extended=False, allow_other_remix=True):
    """Best Spotify match. Returns a dict with 'match':
    'exact'    same version (same remix, or the extended/original asked for),
    'original' the original song when that remix isn't on Spotify,
    'other'    another remix of the song (only if allow_other_remix)."""
    from .library import is_remix
    main = re.split(r",|&| x | feat\.?| ft\.?", artist, flags=re.I)[0].strip()
    base = re.sub(r"[\(\[].*?[\)\]]", "", title).strip()
    versions = [v for v in re.findall(r"[\(\[](.*?)[\)\]]", title) if not re.match(r"(feat|ft)\b", v, re.I)]
    version = " ".join(versions)
    wanted_tokens = _version_tokens(version)
    wants_remix = is_remix(title)
    wants_extended = "extended" in _n(version)
    queries = [f"track:{base} artist:{main} {version}".strip(), f"{main} {title}", f"{main} {base}"]
    if prefer_extended or wants_extended:
        queries.insert(1, f"{main} {base} extended")
    seen, best, best_score = set(), None, 0
    for q in queries:
        r = _call("GET", "/search", params={"q": q, "type": "track", "limit": 10})
        if r.status_code != 200:
            continue
        for it in r.json().get("tracks", {}).get("items", []):
            if it["uri"] in seen:
                continue
            seen.add(it["uri"])
            names = _n(" ".join(a["name"] for a in it["artists"]))
            if not _artist_ok(main, names):
                continue
            name_b = _as_brackets(it["name"])
            if core_title(base) not in core_title(name_b) and core_title(name_b) not in core_title(base):
                continue
            name_n = _n(name_b)
            ext = "extended" in name_n
            cand_versions = [v for v in re.findall(r"[\(\[](.*?)[\)\]]", name_b) if not re.match(r"(feat|ft|with)\b", v, re.I)]
            cand_tokens = _version_tokens(" ".join(cand_versions))
            # a version carrying a name ("Riprock 'n' Alex G. Extended Club") is someone's remix
            someone_elses = bool(cand_tokens - wanted_tokens) or (is_remix(name_b) and not wanted_tokens and not wants_remix)
            if wanted_tokens:
                kind = ("exact" if wanted_tokens <= set(name_n.split()) | set(names.split())
                        else "other" if someone_elses else "original")
            elif wants_remix:
                kind = "exact" if is_remix(name_b) else "original" if not cand_tokens else "other"
            else:
                kind = "other" if someone_elses else "exact"
            if kind == "other" and not allow_other_remix:
                continue
            score = {"exact": 10, "original": 5, "other": 2}[kind] + it.get("popularity", 0) / 100
            if ext and (prefer_extended or wants_extended):
                score += 2.5          # DJ-friendly: long intro / outro
            if score > best_score:
                best, best_score = (it, kind), score
        if best and best[1] == "exact" and (not prefer_extended or "extended" in _n(best[0]["name"])):
            break                     # good enough, save API calls
    if not best:
        return None
    it, kind = best
    return {"uri": it["uri"], "name": it["name"], "artists": ", ".join(a["name"] for a in it["artists"]),
            "url": it["external_urls"].get("spotify", ""), "match": kind}


def playlist_id(url_or_id):
    m = re.search(r"playlist[/:]([A-Za-z0-9]{10,})", url_or_id or "")
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{10,}", (url_or_id or "").strip()):
        return url_or_id.strip()
    raise SpotifyError("Lien de playlist Spotify invalide.")


def playlist_info(pid):
    r = _call("GET", f"/playlists/{pid}", params={"fields": "name,owner(id),external_urls"})
    if r.status_code != 200:
        raise SpotifyError(f"Playlist inaccessible ({r.status_code}). Si elle est privée, reconnecte Spotify "
                           "(Réglages) pour autoriser la lecture des playlists.")
    return r.json()


def playlist_tracks(pid):
    """[{uri, artist, title}] of a playlist, all pages."""
    out, path = [], f"/playlists/{pid}/items"
    params = {"limit": 100, "offset": 0}
    while True:
        r = _call("GET", path, params=params)
        if r.status_code in (404, 410) and path.endswith("/items"):
            path = f"/playlists/{pid}/tracks"
            continue
        if r.status_code != 200:
            raise SpotifyError(f"Lecture de la playlist refusée ({r.status_code}). Reconnecte Spotify (Réglages).")
        data = r.json()
        for it in data.get("items", []):
            tr = it.get("item") or it.get("track")
            if tr and tr.get("type", "track") == "track" and tr.get("uri", "").startswith("spotify:track:"):
                out.append({"uri": tr["uri"], "artist": ", ".join(a["name"] for a in tr["artists"]),
                            "title": tr["name"]})
        if not data.get("next"):
            return out
        params["offset"] += 100


def add_tracks(pid, uris):
    for i in range(0, len(uris), 100):
        chunk = {"uris": uris[i:i + 100]}
        a = _call("POST", f"/playlists/{pid}/items", json=chunk)
        if a.status_code not in (200, 201):
            a = _call("POST", f"/playlists/{pid}/tracks", json=chunk)
        if a.status_code not in (200, 201):
            raise SpotifyError(f"Ajout des morceaux refusé : {a.status_code} {a.text[:150]}")


def replace_tracks(pid, uris):
    """Replace a playlist's content (first 100 with PUT, the rest appended)."""
    body = {"uris": uris[:100]}
    r = _call("PUT", f"/playlists/{pid}/items", json=body)
    if r.status_code not in (200, 201):
        r = _call("PUT", f"/playlists/{pid}/tracks", json=body)
    if r.status_code not in (200, 201):
        raise SpotifyError(f"Mise à jour de la playlist refusée : {r.status_code} {r.text[:150]}")
    if len(uris) > 100:
        add_tracks(pid, uris[100:])


def create_playlist(name, description, uris, public=False):
    body = {"name": name, "description": description[:300], "public": public}
    r = _call("POST", "/me/playlists", json=body)
    if r.status_code not in (200, 201):
        r = _call("POST", f"/users/{me()['id']}/playlists", json=body)
    if r.status_code not in (200, 201):
        raise SpotifyError(f"Création de la playlist refusée : {r.status_code} {r.text[:150]}")
    pl = r.json()
    add_tracks(pl["id"], uris)
    return {"id": pl["id"], "url": pl.get("external_urls", {}).get("spotify", "")}


# ------------------------------------------------------------ similar tracks without AI (Deezer, no key needed)

def deezer_similar(set_tracks, per_artist=4, limit=60):
    """Tracks from artists Deezer considers related to the set's main artists."""
    counts = {}
    for t in set_tracks:
        for a in re.split(r",|&| x | feat\.?", t["artist"], flags=re.I):
            a = a.strip()
            if a:
                counts[a] = counts.get(a, 0) + 1 + t.get("known", 0) / 10
    seeds = sorted(counts, key=lambda a: -counts[a])[:10]
    out, seen = [], set()
    with httpx.Client(timeout=12) as c:
        for a in seeds:
            try:
                hits = c.get("https://api.deezer.com/search/artist", params={"q": a, "limit": 1}).json().get("data", [])
                if not hits:
                    continue
                rel = c.get(f"https://api.deezer.com/artist/{hits[0]['id']}/related", params={"limit": 6}).json()
                for ra in rel.get("data", []):
                    top = c.get(f"https://api.deezer.com/artist/{ra['id']}/top", params={"limit": per_artist}).json()
                    for tr in top.get("data", []):
                        key = core_title(tr["title"])
                        if key not in seen:
                            seen.add(key)
                            out.append({"artist": tr["artist"]["name"], "title": tr["title"]})
            except (httpx.HTTPError, ValueError, KeyError):
                continue
            if len(out) >= limit:
                break
    return out[:limit]
