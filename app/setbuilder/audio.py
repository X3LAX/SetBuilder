"""Audio decoding through ffmpeg (works on macOS and Windows, no Essentia needed)."""
import subprocess

import numpy as np

from .config import which


def load_mono(path, sr=44100):
    """Whole file as a mono float32 numpy array at `sr` Hz."""
    r = subprocess.run([which("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
                        "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, timeout=600)
    return np.frombuffer(r.stdout[:len(r.stdout) // 4 * 4], dtype=np.float32).copy()
