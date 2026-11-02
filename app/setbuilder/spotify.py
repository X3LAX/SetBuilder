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

from .library import core_title

AUTH = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = ("playlist-modify-private playlist-modify-public playlist-read-private playlist-read-collaborative "
          "user-read-private")
KEYCHAIN = "SetBuilder-Spotify"
_pending = {}


class SpotifyError(Exception):
    pass


# ------------------------------------------------------------ tokens (macOS keychain)

def _load_token():
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None


def _save_token(tok):
    subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN], capture_output=True)
    if tok:
        subprocess.run(["security", "add-generic-password", "-s", KEYCHAIN, "-a", "spotify", "-w", json.dumps(tok)],
                       check=True, capture_output=True)


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


def find_track(artist, title):
    """Best Spotify match for artist/title, or None. Prefers the same version (remix/extended) when it exists."""
    main = re.split(r",|&| x | feat\.?| ft\.?", artist, flags=re.I)[0].strip()
    base = re.sub(r"[\(\[].*?[\)\]]", "", title).strip()
    version = " ".join(re.findall(r"[\(\[](.*?)[\)\]]", title))
    for q in (f"track:{base} artist:{main} {version}".strip(), f"{main} {title}", f"{main} {base}"):
        r = _call("GET", "/search", params={"q": q, "type": "track", "limit": 10})
        if r.status_code != 200:
            continue
        items = r.json().get("tracks", {}).get("items", [])
        best, best_score = None, 0
        for it in items:
            names = _n(" ".join(a["name"] for a in it["artists"]))
            m = _n(main)
            if m and m.split()[0] not in names:
                continue
            if core_title(base) not in core_title(it["name"]) and core_title(it["name"]) not in core_title(base):
                continue
            score = 1 + (2 if version and _n(version) in _n(it["name"]) else 0) + it.get("popularity", 0) / 100
            if score > best_score:
                best, best_score = it, score
        if best:
            return {"uri": best["uri"], "name": best["name"], "artists": ", ".join(a["name"] for a in best["artists"]),
                    "url": best["external_urls"].get("spotify", "")}
    return None


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
