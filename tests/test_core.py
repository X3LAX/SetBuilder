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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
