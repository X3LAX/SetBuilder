"""Find missing artwork online: SoundCloud link in the tags, then Deezer, then iTunes."""
import re
import unicodedata

import httpx

from .library import core_title, is_remix

UA = {"User-Agent": "SetBuilder/1.0 (DJ set preparation)"}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _match(want_artist, want_title, got_artist, got_title):
    a, t = _norm(want_artist.split(",")[0].split("&")[0]), core_title(want_title)
    ga, gt = _norm(got_artist), core_title(got_title)
    if not t or not (t in gt or gt in t):
        return False
    if not a:
        return True
    return bool(ga) and (a.split()[0] in ga or ga.split()[0] in a)


def _get(client, url, **kw):
    r = client.get(url, timeout=12, **kw)
    r.raise_for_status()
    return r


def from_soundcloud(client, comment):
    m = re.search(r"https?://(?:www\.|m\.)?soundcloud\.com/[^\s\"']+", comment or "")
    if not m:
        return None
    j = _get(client, "https://soundcloud.com/oembed", params={"format": "json", "url": m.group(0)}).json()
    thumb = j.get("thumbnail_url")
    if not thumb or "placeholder" in thumb:
        return None
    return re.sub(r"-t\d+x\d+\.", "-t500x500.", thumb), "SoundCloud"


def from_deezer(client, artist, title):
    for q in (f'artist:"{artist}" track:"{title}"', f"{artist} {title}",
              f"{artist} {re.sub(r'[\(\[].*?[\)\]]', '', title)}"):
        data = _get(client, "https://api.deezer.com/search", params={"q": q, "limit": 10}).json().get("data", [])
        for d in data:
            if _match(artist, title, d["artist"]["name"], d["title"]):
                exact = is_remix(title) == is_remix(d["title"])
                return d["album"]["cover_xl"], "Deezer" if exact else "Deezer (titre original)"
    return None


def from_itunes(client, artist, title):
    base = re.sub(r"[\(\[].*?[\)\]]", "", title).strip()
    for term in (f"{artist} {title}", f"{artist} {base}"):
        res = _get(client, "https://itunes.apple.com/search",
                   params={"term": term, "entity": "song", "limit": 10}).json().get("results", [])
        for r in res:
            if _match(artist, title, r.get("artistName", ""), r.get("trackName", "")):
                return r["artworkUrl100"].replace("100x100bb", "1000x1000bb"), "iTunes"
    return None


def find_cover(artist, title, comment=""):
    """Return (image bytes, mime, source) or (None, None, None)."""
    with httpx.Client(headers=UA, follow_redirects=True) as c:
        for finder in (lambda: from_soundcloud(c, comment), lambda: from_deezer(c, artist, title),
                       lambda: from_itunes(c, artist, title)):
            try:
                hit = finder()
            except (httpx.HTTPError, ValueError, KeyError):
                hit = None
            if hit:
                url, source = hit
                try:
                    r = _get(c, url)
                except httpx.HTTPError:
                    continue
                mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
                if mime.startswith("image/") and len(r.content) > 2000:
                    return r.content, ("image/png" if "png" in mime else "image/jpeg"), source
    return None, None, None
