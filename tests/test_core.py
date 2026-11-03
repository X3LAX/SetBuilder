"""Quick checks for name cleaning and set planning. Run: python3 tests/test_core.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from setbuilder import library, planner  # noqa: E402


def test_split_name():
    assert library.split_name("01. Bad Bunny - DtMF (Official Video) [Free Download]") == ("Bad Bunny", "DtMF")
    assert library.split_name("03_karol_g_-_tusa_320kbps") == ("karol g", "tusa")
    assert library.nice_case("el farsante (dj x remix)") == "El Farsante (DJ x Remix)"
    assert library.nice_case("NUEVAYoL") == "NUEVAYoL"
    assert library.looks_junky("Track 03") and library.looks_junky("audio_7741".replace("_", " "))


def test_is_remix():
    assert library.is_remix("Dákiti (HÄWK VIP Edit)")
    assert library.is_remix("La Falda (Kowski Edit)")
    assert not library.is_remix("Pa Lante (Extended Mix)")
    assert not library.is_remix("Conga (Clean Radio Edit)")
    assert not library.is_remix("A Tu Vera (feat. Ruben Hernandez Trumpet) (Original Mix)")


def test_key_cost():
    assert planner.key_cost("8A", "8A") == 0
    assert planner.key_cost("8A", "9A") < planner.key_cost("8A", "10A") < planner.key_cost("8A", "3B")
    assert planner.key_cost("12A", "1A") == planner.key_cost("1A", "12A")


def _tracks(n=40):
    keys = [f"{(i % 12) + 1}A" for i in range(n)]
    return [{"id": str(i), "bpm": 122 + (i % 9), "camelot": keys[i], "energy": (i % 9) / 4 - 1, "score": i % 5,
             "play": 210.0, "family": str(i), "known": i % 10, "short": False} for i in range(n)]


def test_build_set_duration_and_tempo():
    ids = planner.build_set(_tracks(), duration_min=30, bpm_start=122, bpm_end=129)
    by = {t["id"]: t for t in _tracks()}
    minutes = sum(by[i]["play"] for i in ids) / 60
    assert 28 <= minutes <= 33, minutes
    assert len(set(ids)) == len(ids)
    assert by[ids[0]]["bpm"] <= by[ids[-1]]["bpm"]


def test_extended_contains_everything_once():
    ts = _tracks()
    set_ids = planner.build_set(ts, duration_min=30)
    blocks = planner.build_extended(ts, set_ids)
    flat = [i for b in blocks for i in b["ids"]]
    assert sorted(flat) == sorted(t["id"] for t in ts)
    assert blocks[0]["ids"] == set_ids


def test_rekordbox_pdb_roundtrip():
    from setbuilder import rekordbox_usb as R
    tracks = [R.track_row({"id": i, "title": f"Titre {i} é", "path": f"/Contents/x/{i}.mp3", "filename": f"{i}.mp3",
                           "anlz": f"/PIONEER/USBANLZ/P000/{i:08X}/ANLZ0000.DAT", "date": "2026-11-02",
                           "comment": "", "artist_id": 1, "album_id": 1, "genre_id": 1, "key_id": 1, "tempo": 12600,
                           "duration": 240, "sample_rate": 44100, "bitrate": 320, "size": 1, "year": 0,
                           "file_type": 1}) for i in range(1, 120)]
    entries = [R.entry_row(k, k, 1) for k in range(1, 120)]
    db = R.build_pdb({R.TRACKS: tracks, R.PL_ENTRIES: entries, R.ARTISTS: [R.artist_row(1, "HUGEL")],
                      R.PL_TREE: [R.playlist_row(1, "Set")]})
    back = R.read_pdb(db)
    assert len(back[R.TRACKS]) == 119 and len(back[R.PL_ENTRIES]) == 119
    assert R.track_paths(back[R.TRACKS][5])[0] == "/Contents/x/6.mp3"
    assert R.dstring("Bad Bunny")[0] == (9 + 1) * 2 + 1 and R.dstring("Dákiti")[0] == 0x90


def test_beat_grid_keeps_first_beat_on_one():
    import struct
    from setbuilder import rekordbox_usb as R
    sec = R._beat_grid(120.0, 1.25, 10.0)      # 0.5 s beats, detected beat at 1.25 s
    beats = [struct.unpack_from(">HHI", sec, 24 + 8 * i) for i in range((len(sec) - 24) // 8)]
    first = next(b for b in beats if b[2] == 1250)
    assert first[0] == 1 and beats[0][2] == 250 and beats[0][1] == 12000


def test_librosa_backend():
    """Forces the librosa backend on a real file (skipped when none is available) and compares with Essentia."""
    import glob, importlib
    files = sorted(glob.glob("/Users/borettialexander/Music/Latine/Tracks/**/*.mp3", recursive=True))
    if not files:
        return
    from setbuilder import analysis
    analysis.FORCE_LIBROSA = True
    lib = analysis.analyze_file(files[0])
    analysis.FORCE_LIBROSA = False
    assert 100 <= lib["bpm"] <= 150 and lib["camelot"] in analysis.CAMELOT.values(), lib
    try:
        import essentia  # noqa: F401
        ess = analysis.analyze_file(files[0])
        print("  essentia:", ess["bpm"], ess["camelot"], round(ess["key_strength"], 2), "| librosa:", lib["bpm"], lib["camelot"],
              round(lib["key_strength"], 2), "|", Path(files[0]).name)
    except ImportError:
        print("  librosa:", lib["bpm"], lib["camelot"])


def test_analysis_on_generated_track():
    """Decode + analyse a generated 124 BPM bass line with both backends (runs anywhere ffmpeg exists)."""
    import shutil, subprocess, tempfile
    from setbuilder import analysis
    if not shutil.which("ffmpeg"):
        return
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "beat.wav"
        expr = "0.7*sin(2*PI*110*t)*exp(-12*mod(t,60/124))+0.2*sin(2*PI*220*t)*exp(-12*mod(t,60/124))"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':d=40:s=44100",
                        str(f)], check=True)
        for force in (False, True):
            analysis.FORCE_LIBROSA = force
            try:
                r = analysis.analyze_file(f)
            finally:
                analysis.FORCE_LIBROSA = False
            assert 120 <= r["bpm"] <= 128, (force, r["bpm"])
            assert r["camelot"] in analysis.CAMELOT.values() and r["duration"] > 39


def test_discover_pure():
    from setbuilder import discover as d
    assert d.guess_kind("Bad Bunny - Una Velita (Jesus Fernandez Remix)") == "remix"
    assert d.guess_kind("Rompe (Juos Bootleg)") == "bootleg"
    assert d.guess_kind("Song (Kowski Edit)") == "edit" and d.guess_kind("Song (Radio Edit)") == "original"
    assert d.guess_kind("Song (Dj X Flip)") == "flip" and d.guess_kind("Song (X VIP)") == "edit"
    assert d.guess_kind("Latin House Mix 2026 live set") == "mix" and d.guess_kind("Song", 2000) == "mix"
    hot, cold = d.trend_score(50000, 3000, 200, 20), d.trend_score(500, 5, 0, 900)
    assert 0 <= cold < hot <= 100 and d.trend_score(0, 0, 0, None) >= 0
    assert d.trend_score(5000, 300, 20, 30) > d.trend_score(5000, 300, 20, 600)
    assert d.is_rising(10000, 10) and not d.is_rising(100, 400)
    seeds = [{"title": "Dákiti", "url": "https://soundcloud.com/a/seed"}]
    known = {(library.core_title("Tusa"), d.version_of("Tusa (Foo Edit)"))}
    mk = lambda **k: {"url": "https://soundcloud.com/x/y", "title": "Artist - Song (Z Remix)", "duration": 200} | k
    assert d.keep_candidate(mk(), seeds, known, set())
    assert not d.keep_candidate(mk(duration=60), seeds, known, set())
    assert not d.keep_candidate(mk(duration=2400), seeds, known, set())
    assert not d.keep_candidate(mk(title="Song (Z Remix) preview"), seeds, known, set())
    assert not d.keep_candidate(mk(), seeds, known, {"https://soundcloud.com/x/y"})
    assert not d.keep_candidate(mk(url="https://soundcloud.com/a/seed?si=1"), seeds, known, set())
    assert not d.keep_candidate(mk(title="Karol G - Tusa (Foo Edit)"), seeds, known, set())
    assert d.keep_candidate(mk(title="Karol G - Tusa (Bar Edit)"), seeds, known, set())
    assert len(d.dedupe([mk(), mk(url="https://soundcloud.com/x/y2")])) == 1
    c = {"kind": "edit", "unofficial": True, "free_download": True, "plays": 9000, "age_days": 10}
    assert d.make_badges(c) == ["Remix non officiel", "Edit", "Téléchargement gratuit", "En hausse", "Récent"]

def test_discover_filters_and_mashup():
    from setbuilder import discover as d
    assert d.guess_kind("A - B (X Mashup)") == "mashup" and d.guess_kind("A - B (X Remix)") == "remix"
    c = lambda **k: {"kind": "remix", "age_days": 10, "orig_year": 2005, "orig_pop": "hit"} | k
    cs = [c(), c(orig_year=1995), c(orig_year=0, orig_pop=None), c(kind="mashup", age_days=400), c(orig_pop="niche")]
    assert len(d.apply_filters(cs, {}, True)[0]) == 5
    assert len(d.apply_filters(cs, {"era": "2000s"}, True)[0]) == 3
    assert d.apply_filters(cs, {"era": "2000s", "original": "hits"}, True)[0] == [cs[0], cs[3]]
    assert d.apply_filters(cs, {"original": "niche"}, True)[0] == [cs[4]]
    assert len(d.apply_filters(cs, {"uploaded": 30}, True)[0]) == 4
    assert len(d.apply_filters(cs, {"kinds": ["mashup"]}, True)[0]) == 1
    assert len(d.apply_filters([c(orig_year=2025)], {"era": "recent"}, True, 2026)[0]) == 1
    out, skipped = d.apply_filters(cs, {"era": "2000s", "original": "hits"}, False)
    assert len(out) == 5 and skipped == ["era", "original"]
    assert "2000s remix" in d.det_queries([{"title": "Tusa", "artist": "Karol G"}], {"era": "2000s"})


def test_spotify_quick_official_and_cache(tmp_path=None):
    import tempfile
    from setbuilder import discover as d, spotify
    spotify.CACHE_FILE = Path(tempfile.mkdtemp()) / "spotify-cache.json"
    spotify._cache = None

    class R:
        status_code = 200
        def __init__(self, items): self.items = items
        def json(self): return {"tracks": {"items": self.items}}
    it = lambda name, art: {"name": name, "artists": [{"name": art}], "uri": name}
    calls = []
    def fake(method, path, wait=True, **kw):
        calls.append(kw["params"]["q"])
        return R([it("Tusa - Foo Remix", "Karol G"), it("Tusa", "Karol G")])
    spotify._call = fake
    assert spotify.quick_official("Karol G", "Tusa (Foo Remix)") is True
    assert spotify.quick_official("Karol G", "Tusa (Bar Remix)") is False   # another remix is not the same version
    assert spotify.quick_official("Karol G", "Tusa") is True and len(calls) == 3
    spotify._call = lambda *a, **k: R([it("Tusa - Foo Remix", "Someone Else")])
    assert spotify.quick_official("Karol G", "Tusa (Foo Remix)") is False
    # cache
    spotify.cache_get("a", "b")
    spotify.cache_put("A", "b", True)
    spotify.cache_save()
    spotify._cache = None
    assert spotify.cache_get("a", "B") is True and spotify.cache_get("a", "c") is None
    spotify._cache["a|old"] = [True, 0]
    assert spotify.cache_get("a", "old") is None
    # rate limit stops the loop: later tracks stay unchecked and nothing sleeps
    seen = []
    def limited(artist, title):
        seen.append(title)
        raise spotify.RateLimited("429")
    spotify.quick_official = limited
    cands = [{"title": f"X - Song{i} (Z Remix)", "uploader": "x", "on_spotify": False} for i in range(40)]
    import time
    t0 = time.time()
    d.spotify_check(cands, budget=5)
    assert len(seen) <= 4 and not any(c["on_spotify"] for c in cands) and time.time() - t0 < 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
