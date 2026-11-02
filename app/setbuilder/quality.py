"""File health check: is each audio file complete, readable and as good as it claims?

Detects: unreadable / corrupt files (decode errors), truncated files (decoded length shorter than announced),
wrong extension for the real codec, "fake" high bitrates (spectrum cut like a 128 kbps file), long silences,
mono files, very short snippets, sample rates players dislike.
"""
import json
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

from .config import which

EXT_CODECS = {".mp3": {"mp3"}, ".m4a": {"aac", "alac"}, ".aac": {"aac"}, ".flac": {"flac"},
              ".wav": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_u8"},
              ".aif": {"pcm_s16be", "pcm_s24be", "pcm_s32be"}, ".aiff": {"pcm_s16be", "pcm_s24be", "pcm_s32be"}}
LOSSLESS = {"flac", "alac"} | EXT_CODECS[".wav"] | EXT_CODECS[".aiff"]
SR = 44100


def _probe(path):
    r = subprocess.run([which("ffprobe") or "ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                        "-show_format", str(path)], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return None
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {"format": data.get("format", {}), "audio": audio} if audio else None


def _decode(path):
    """Whole file as mono float32 at 44.1 kHz, plus the decoder's error lines."""
    r = subprocess.run([which("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
                        "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, timeout=300)
    errors = [l for l in r.stderr.decode(errors="replace").splitlines() if l.strip()]
    return np.frombuffer(r.stdout, dtype=np.float32), errors, r.returncode


def _cutoff_khz(audio):
    """Highest frequency that still carries real energy (lossy encoders low-pass: 128 kbps ≈ 16 kHz)."""
    mid = len(audio) // 2
    seg = audio[max(0, mid - 15 * SR):mid + 15 * SR]
    if len(seg) < SR:
        return None
    n = 8192
    frames = [seg[i:i + n] * np.hanning(n) for i in range(0, len(seg) - n, n)]
    spec = np.mean([np.abs(np.fft.rfft(f)) for f in frames], axis=0) + 1e-12
    db = 20 * np.log10(spec / spec.max())
    freqs = np.fft.rfftfreq(n, 1 / SR)
    # smooth, then find where the spectrum falls below -70 dB for good
    smooth = np.convolve(db, np.ones(9) / 9, mode="same")
    above = np.where(smooth > -70)[0]
    return round(float(freqs[above[-1]]) / 1000, 1) if len(above) else None


def check_file(path):
    path = str(path)
    issues = []          # (level, text): "bad" = unusable, "warn" = usable but worth knowing
    info = {"path": path}
    probe = _probe(path)
    if not probe:
        return {**info, "status": "bad", "issues": [("bad", "fichier illisible ou pas un fichier audio")]}
    a, fmt = probe["audio"], probe["format"]
    codec = a.get("codec_name", "")
    announced = float(fmt.get("duration") or a.get("duration") or 0)
    bitrate = int(float(a.get("bit_rate") or fmt.get("bit_rate") or 0)) // 1000
    sr, channels = int(a.get("sample_rate") or 0), int(a.get("channels") or 0)
    info.update(codec=codec, bitrate=bitrate, sample_rate=sr, channels=channels, duration=round(announced, 1))

    ext = os.path.splitext(path)[1].lower()
    if ext in EXT_CODECS and codec not in EXT_CODECS[ext]:
        issues.append(("warn", f"extension {ext} mais le son est en {codec} (Rekordbox peut le refuser)"))

    audio, errors, rc = _decode(path)
    decoded = len(audio) / SR
    info["decoded"] = round(decoded, 1)
    if rc != 0 or decoded < 1:
        return {**info, "status": "bad", "issues": issues + [("bad", "fichier corrompu : impossible à décoder")]}
    if len(errors) > 5:
        issues.append(("bad", f"fichier abîmé : {len(errors)} erreurs de décodage (coupures ou sauts à la lecture), "
                              "retélécharge-le"))
    elif errors:
        issues.append(("warn", f"{len(errors)} petite(s) erreur(s) de décodage"))
    if announced and decoded < announced - 3:
        issues.append(("bad", f"{announced - decoded:.0f} s de son illisibles ({decoded:.0f} s sur {announced:.0f} s annoncées)"))
    if decoded < 90:
        issues.append(("warn", f"très court ({decoded:.0f} s) : extrait ou preview ?"))

    if codec not in LOSSLESS:
        cut = _cutoff_khz(audio)
        info["cutoff_khz"] = cut
        if cut and bitrate >= 256 and cut < 17:
            issues.append(("warn", f"faux {bitrate} kbps : le son s'arrête à {cut} kHz, comme un fichier 128 kbps ré-encodé"))
        elif bitrate and bitrate < 128:
            issues.append(("warn", f"qualité faible ({bitrate} kbps)"))
    elif sr and sr < 44100:
        issues.append(("warn", f"fréquence d'échantillonnage basse ({sr} Hz)"))

    win = SR
    n = len(audio) // win
    if n:
        rms = np.sqrt(np.mean(audio[:n * win].reshape(n, win) ** 2, axis=1))
        silent = rms < 10 ** (-50 / 20)
        # longest silent run, ignoring the very start and end of the track
        inner = silent[3:-3] if n > 8 else silent[:0]
        longest, run = 0, 0
        for s in inner:
            run = run + 1 if s else 0
            longest = max(longest, run)
        if longest >= 8:
            issues.append(("warn", f"silence de {longest} s au milieu du morceau"))
        if silent.mean() > 0.5:
            issues.append(("bad", "presque entièrement silencieux"))
    if channels == 1:
        issues.append(("warn", "mono"))

    status = "bad" if any(l == "bad" for l, _ in issues) else "warn" if issues else "ok"
    return {**info, "status": status, "issues": issues}


def _worker(path):
    try:
        return check_file(path)
    except Exception as e:  # noqa: BLE001 - one odd file must not stop the batch
        return {"path": str(path), "status": "bad", "issues": [("bad", f"contrôle impossible : {e}")]}


def check_many(paths, progress=lambda i, n, m: None):
    out = {}
    with ProcessPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 1)) as ex:
        futs = [ex.submit(_worker, p) for p in paths]
        for k, f in enumerate(as_completed(futs), 1):
            r = f.result()
            out[r["path"]] = r
            progress(k, len(paths), os.path.basename(r["path"]))
    return out
