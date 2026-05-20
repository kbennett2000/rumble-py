# Helpers for Mumble client tests and the manual smoke script.

from __future__ import annotations

import numpy as np

MUMBLE_SAMPLE_RATE = 48000


def synth_pcm(duration_s: float = 1.0, freq: float = 440.0) -> bytes:
    """Return mono 16-bit LE PCM at 48 kHz, sine wave at ``freq``.

    Uses 0.3 amplitude so the signal is audible but well below clipping when
    mixed with other speakers on a Mumble channel.
    """
    n = int(MUMBLE_SAMPLE_RATE * duration_s)
    t = np.arange(n) / MUMBLE_SAMPLE_RATE
    samples = (np.sin(2 * np.pi * freq * t) * 0.3 * 32767).astype(np.int16)
    return samples.tobytes()
