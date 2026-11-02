"""BPM, Camelot key and energy features (Essentia, or librosa where Essentia is unavailable), cached per file."""
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed

from .config import CACHE_FILE

CAMELOT = {
    ("B", "major"): "1B", ("F#", "major"): "2B", ("Gb", "major"): "2B", ("C#", "major"): "3B", ("Db", "major"): "3B",
    ("G#", "major"): "4B", ("Ab", "major"): "4B", ("D#", "major"): "5B", ("Eb", "major"): "5B", ("A#", "major"): "6B",
    ("Bb", "major"): "6B", ("F", "major"): "7B", ("C", "major"): "8B", ("G", "major"): "9B", ("D", "major"): "10B",
    ("A", "major"): "11B", ("E", "major"): "12B",
    ("G#", "minor"): "1A", ("Ab", "minor"): "1A", ("D#", "minor"): "2A", ("Eb", "minor"): "2A", ("A#", "minor"): "3A",
    ("Bb", "minor"): "3A", ("F", "minor"): "4A", ("C", "minor"): "5A", ("G", "minor"): "6A", ("D", "minor"): "7A",
    ("A", "minor"): "8A", ("E", "minor"): "9A", ("B", "minor"): "10A", ("F#", "minor"): "11A", ("Gb", "minor"): "11A",
    ("C#", "minor"): "12A", ("Db", "minor"): "12A",
}
VERSION = 1
FORCE_LIBROSA = os.environ.get("SETBUILDER_BACKEND") == "librosa"
_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def _have_essentia():
    if FORCE_LIBROSA:
        return False
    try:
        import essentia  # noqa: F401
        return True
    except ImportError:
        return False


def _cache_key(path):
    st = os.stat(path)
    return f"{VERSION}:{path}:{st.st_size}:{int(st.st_mtime)}"


def load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_cache(c):
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c))
    tmp.replace(CACHE_FILE)


def _envelope(audio, np):
    # loudness curve in 4 s windows -> where the track gets busy (drop) and the intro length
    win = 44100 * 4
    env = [float(np.sqrt(np.mean(audio[i:i + win] ** 2))) for i in range(0, len(audio) - win, win)]
    peak = max(env) if env else 1
    intro = next((i * 4 for i, e in enumerate(env) if e > 0.7 * peak), 0)
    return intro, [round(e / peak, 3) for e in env]


def _analyze_librosa(audio, bpm_min, bpm_max):
    import numpy as np
    import librosa
    sr = 44100
    y = audio
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    raw = bpm
    while 0 < bpm < bpm_min: bpm *= 2
    while bpm > bpm_max: bpm /= 2
    if abs(bpm - round(bpm)) < 0.25:
        bpm = float(round(bpm))
    beats = librosa.frames_to_time(beat_frames, sr=sr)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    best = (-2.0, "C", "major")
    for scale, prof in (("major", _MAJOR), ("minor", _MINOR)):
        for k in range(12):
            c = float(np.corrcoef(chroma, np.roll(prof, k))[0, 1])
            if c > best[0]:
                best = (c, _NAMES[k], scale)
    strength, key, scale = best
    body = y[int(len(y) * 0.15):int(len(y) * 0.85)]
    rms = float(np.sqrt(np.mean(body ** 2)))
    onsets = librosa.onset.onset_detect(y=body, sr=sr)
    onset_rate = len(onsets) / (len(body) / sr)
    oenv = librosa.onset.onset_strength(y=body, sr=sr)
    oenv = oenv - oenv.mean()
    ac = librosa.autocorrelate(oenv)
    ac = ac / (ac[0] + 1e-9)
    dance = float(max(0.0, ac[10:] .max())) if len(ac) > 20 else 0.0
    intro, env = _envelope(y, np)
    return {
        "bpm": round(float(bpm), 2), "bpm_raw": raw, "bpm_conf": 0.0,
        "key": f"{key} {scale}", "camelot": CAMELOT.get((key, scale), "?"), "key_strength": float(strength),
        "loud_db": float(20 * np.log10(rms + 1e-9)), "onset_rate": float(onset_rate), "dance": dance,
        "duration": len(y) / 44100.0, "first_beat": float(beats[0]) if len(beats) else 0.0,
        "intro_s": intro, "envelope": env,
    }


def analyze_file(path, bpm_min=100, bpm_max=150):
    import numpy as np
    from .audio import load_mono

    audio = load_mono(path, 44100)
    if len(audio) < 44100 * 5:
        raise ValueError("fichier audio vide ou illisible")
    if not _have_essentia():
        return _analyze_librosa(audio, bpm_min, bpm_max)
    import essentia
    essentia.log.warningActive = False
    import essentia.standard as es
    bpm, beats, conf, _, _ = es.RhythmExtractor2013(method="multifeature")(audio)
    raw = float(bpm)
    while bpm < bpm_min: bpm *= 2
    while bpm > bpm_max: bpm /= 2
    if abs(bpm - round(bpm)) < 0.25:  # club tracks are produced at whole-number tempos
        bpm = float(round(bpm))
    key, scale, strength = es.KeyExtractor(profileType="edma")(audio)

    body = audio[int(len(audio) * 0.15):int(len(audio) * 0.85)]
    rms = float(np.sqrt(np.mean(body ** 2)))
    _, onset_rate = es.OnsetRate()(body.astype(np.float32))
    dance, _ = es.Danceability()(body)
    intro, env = _envelope(audio, np)
    return {
        "bpm": round(float(bpm), 2), "bpm_raw": raw, "bpm_conf": float(conf),
        "key": f"{key} {scale}", "camelot": CAMELOT.get((key, scale), "?"), "key_strength": float(strength),
        "loud_db": float(20 * np.log10(rms + 1e-9)), "onset_rate": float(onset_rate), "dance": float(dance),
        "duration": len(audio) / 44100.0, "first_beat": float(beats[0]) if len(beats) else 0.0,
        "intro_s": intro, "envelope": env,
    }


def _worker(path):
    try:
        return path, analyze_file(path), None
    except Exception as e:  # noqa: BLE001 - report any decoder failure per file
        return path, None, str(e)


def analyze_many(paths, progress=lambda i, n, msg: None, force=False):
    cache = load_cache()
    results, todo = {}, []
    for p in paths:
        k = _cache_key(p)
        if not force and k in cache:
            results[p] = cache[k]
        else:
            todo.append(p)
    done = len(results)
    errors = {}
    if todo:
        with ProcessPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 1)) as ex:
            futs = [ex.submit(_worker, p) for p in todo]
            for f in as_completed(futs):
                p, res, err = f.result()
                done += 1
                progress(done, len(paths), os.path.basename(p))
                if res:
                    results[p] = res
                    cache[_cache_key(p)] = res
                else:
                    errors[p] = err
                if done % 10 == 0:
                    save_cache(cache)
    save_cache(cache)
    return results, errors


def energy_scores(analyses):
    """Relative energy (z-score mix of loudness, percussion density, danceability) for a set of tracks."""
    import numpy as np
    keys = list(analyses)
    if len(keys) < 2:
        return {k: 0.0 for k in keys}
    def z(field):
        v = np.array([analyses[k][field] for k in keys], dtype=float)
        sd = v.std() or 1.0
        return (v - v.mean()) / sd
    e = 0.45 * z("loud_db") + 0.35 * z("onset_rate") + 0.2 * z("dance")
    return {k: round(float(x), 3) for k, x in zip(keys, e)}
