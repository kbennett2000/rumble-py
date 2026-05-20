# Synthesis helpers for DTMF detector tests and manual experimentation.
#
# Leading underscore so pytest doesn't try to collect this as a test module.

from __future__ import annotations

import numpy as np

from rumble.dtmf_detector import DTMF_KEYS

# Reverse lookup: char → (low_freq, high_freq).
_CHAR_TO_FREQS: dict[str, tuple[int, int]] = {char: pair for pair, char in DTMF_KEYS.items()}


def synth_tone(char: str, duration_s: float, sample_rate: int = 8000) -> np.ndarray:
    """Return mono float32 samples for the dual-tone audio of one DTMF key.

    Uses the canonical ``0.5 * (sin(low) + sin(high))`` envelope — peak
    amplitude approaches ±1.0 when the two sines align in phase, so the
    signal is loud but never clips.

    Args:
        char: One of ``"0"``-``"9"``, ``"*"``, ``"#"``, ``"A"``-``"D"``.
        duration_s: Tone duration in seconds.
        sample_rate: Sample rate in Hz.

    Returns:
        1-D float32 array of length ``int(duration_s * sample_rate)``.
    """
    low, high = _CHAR_TO_FREQS[char]
    n = int(duration_s * sample_rate)
    t = np.arange(n) / sample_rate
    signal = 0.5 * (np.sin(2 * np.pi * low * t) + np.sin(2 * np.pi * high * t))
    return signal.astype(np.float32)


def synth_silence(duration_s: float, sample_rate: int = 8000) -> np.ndarray:
    """Return ``duration_s`` seconds of mono float32 zero-amplitude audio."""
    n = int(duration_s * sample_rate)
    return np.zeros(n, dtype=np.float32)
