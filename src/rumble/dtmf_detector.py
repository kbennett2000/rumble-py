# DTMF tone detector — Goertzel-based, no audio I/O, no state machine.
#
# Two pieces live here:
#
# * goertzel_magnitude(): a pure function from (samples, freq, fs) to a real
#   magnitude. Standard Goertzel recurrence.
# * DtmfDetector: stateful wrapper that chunks an incoming stream into fixed
#   frames, classifies each frame as one of 16 DTMF keys or silence, applies
#   debouncing, and emits ToneEvent("start", char) / ToneEvent("stop", char)
#   on state transitions.
#
# This module is intentionally decoupled from sounddevice (audio.py is where
# real audio I/O lives) and from the command state machine (dtmf.py — they
# meet inside commands.py in the next milestone). That separation means the
# detector can be exercised end-to-end against synthesized audio without ever
# touching a sound card.

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import numpy as np

# ---------------------------------------------------------------------------
# DTMF frequency table
# ---------------------------------------------------------------------------
#
# Standard ITU-T Q.23 DTMF tone pairs. A keypress is the simultaneous presence
# of exactly one low-group and one high-group frequency.

DTMF_LOW_FREQS: Final[tuple[int, ...]] = (697, 770, 852, 941)
DTMF_HIGH_FREQS: Final[tuple[int, ...]] = (1209, 1336, 1477, 1633)

DTMF_KEYS: Final[dict[tuple[int, int], str]] = {
    (697, 1209): "1", (697, 1336): "2", (697, 1477): "3", (697, 1633): "A",
    (770, 1209): "4", (770, 1336): "5", (770, 1477): "6", (770, 1633): "B",
    (852, 1209): "7", (852, 1336): "8", (852, 1477): "9", (852, 1633): "C",
    (941, 1209): "*", (941, 1336): "0", (941, 1477): "#", (941, 1633): "D",
}  # fmt: skip


# ---------------------------------------------------------------------------
# Detector tuning constants
# ---------------------------------------------------------------------------

# Sampling rate used by the rest of the system. 8 kHz is plenty for DTMF —
# all eight tones live well below the Nyquist limit at 4 kHz — and matches
# what most VOIP narrowband codecs use, so we don't pay for resampling.
DEFAULT_SAMPLE_RATE: Final[int] = 8000

# Frame size in samples. 205 samples at 8 kHz is ~25.6 ms, which is the
# smallest window that gives a clean Goertzel reading at the lowest DTMF
# frequency (697 Hz) — anything shorter and the 697/770 bins start to bleed
# into each other. ITU-T Q.24 also says a valid DTMF tone must be ≥40 ms,
# so two consecutive 25 ms frames comfortably fit inside one tone.
DEFAULT_FRAME_SIZE: Final[int] = 205

# Magnitude threshold for "tone present" detection.
#
# `goertzel_magnitude()` normalizes by N, so a pure sine of amplitude A at
# the target frequency yields a magnitude of approximately A/2. With the
# test synthesis convention `0.5*(sin(low) + sin(high))`, each tone has
# amplitude 0.5, so its expected Goertzel magnitude is ~0.25.
#
# Setting the threshold at 0.05 gives us a 5x safety margin against the
# expected clean-signal magnitude, while sitting an order of magnitude
# above the noise floor we'd see from σ ≈ 0.1 Gaussian noise at this frame
# size (~0.01). Real radio audio that comes in well below line level will
# need either pre-gain or a lower threshold; this is documented as a
# constructor argument so callers can tune.
DEFAULT_MIN_MAGNITUDE: Final[float] = 0.05

# Number of consecutive frames that must agree before we change the held
# tone. Two frames at ~25 ms each gives an effective 50 ms minimum tone
# duration before we emit "start" — comfortably within the ITU-T 40 ms
# minimum-duration spec, and enough to suppress single-frame glitches
# (clicks, brief intermod) without adding noticeable latency.
DEFAULT_DEBOUNCE_FRAMES: Final[int] = 2


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToneEvent:
    """A DTMF tone start or stop boundary.

    Attributes:
        kind: ``"start"`` when the detector has just begun holding a new tone,
            ``"stop"`` when a previously held tone has been released (because
            silence took over, or a different tone replaced it).
        char: The DTMF character — one of ``0``-``9``, ``*``, ``#``,
            ``A``-``D``.
    """

    kind: Literal["start", "stop"]
    char: str


# ---------------------------------------------------------------------------
# Goertzel
# ---------------------------------------------------------------------------


def goertzel_magnitude(samples: np.ndarray, target_freq: float, sample_rate: int) -> float:
    """Compute the spectral magnitude of ``target_freq`` in ``samples``.

    Standard second-order Goertzel recurrence. Output is normalized by the
    sample count so that the result has the same meaning regardless of frame
    length: a pure sine of amplitude ``A`` at exactly ``target_freq`` yields
    a magnitude of approximately ``A/2``.

    Args:
        samples: 1-D array of floating-point audio samples (mono).
        target_freq: Frequency of interest, in Hz.
        sample_rate: Sampling rate of ``samples``, in Hz.

    Returns:
        Non-negative real magnitude.
    """
    n = samples.size
    omega = 2.0 * np.pi * target_freq / sample_rate
    coeff = 2.0 * np.cos(omega)

    s_prev = 0.0
    s_prev2 = 0.0
    # Iterating a numpy array in a Python loop is slow but acceptable here:
    # at 8 kHz with 205-sample frames, this runs eight times per ~25 ms tick,
    # well inside the audio-thread budget. If it ever becomes a bottleneck,
    # switch to a vectorized single-bin DFT (np.dot with precomputed phasors).
    for x in samples:
        s = float(x) + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s

    power = s_prev * s_prev + s_prev2 * s_prev2 - coeff * s_prev * s_prev2
    # Floating-point error can produce tiny negative values when the true
    # power is near zero. Clamp to keep sqrt() happy.
    return float(np.sqrt(max(power, 0.0))) / n


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class DtmfDetector:
    """Stateful DTMF detector that emits ToneEvents on state transitions.

    The detector consumes mono float audio in arbitrarily-sized chunks via
    :meth:`process`, internally splits the chunk into fixed-size frames, and
    classifies each frame as either one of 16 DTMF keys or silence. A simple
    debounce filter ensures that a tone must persist for ``debounce_frames``
    consecutive frames before it's accepted; this rejects single-frame
    glitches caused by clicks, brief intermod, or sample-aligned transients.

    Cross-chunk state is preserved between :meth:`process` calls: a tone that
    starts in one chunk and ends in the next will produce the expected
    start/stop pair across the two return lists.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_size: int = DEFAULT_FRAME_SIZE,
        min_magnitude: float = DEFAULT_MIN_MAGNITUDE,
        debounce_frames: int = DEFAULT_DEBOUNCE_FRAMES,
    ) -> None:
        """Construct a detector with the given parameters.

        Args:
            sample_rate: Sampling rate of the audio that will be fed in, in Hz.
            frame_size: Number of samples per detection frame.
            min_magnitude: Minimum normalized Goertzel magnitude for a
                low/high frequency bin to count as "present".
            debounce_frames: Number of consecutive matching frames required
                before the held tone changes.
        """
        self._sample_rate = sample_rate
        self._frame_size = frame_size
        self._min_magnitude = min_magnitude
        self._debounce_frames = debounce_frames

        # Samples carried over from the previous process() call because they
        # didn't fill a complete frame.
        self._leftover: np.ndarray = np.empty(0, dtype=np.float32)

        # The tone we believe is currently being held (or None for silence).
        self._held: str | None = None
        # The tone our recent frames have been voting for, and how many
        # consecutive frames have agreed on it.
        self._candidate: str | None = None
        self._run_count: int = 0

    def reset(self) -> None:
        """Forget all in-progress state.

        After reset, no tone is held and no candidate is being tracked, so
        feeding silence will not emit a spurious ``stop`` event.
        """
        self._leftover = np.empty(0, dtype=np.float32)
        self._held = None
        self._candidate = None
        self._run_count = 0

    def process(self, samples: np.ndarray) -> list[ToneEvent]:
        """Feed a chunk of mono audio samples and return any events emitted.

        Args:
            samples: 1-D numpy array of audio samples, length ≥ 0. Will be
                cast to float32 if it isn't already.

        Returns:
            A list of :class:`ToneEvent` instances in temporal order. May be
            empty if no transitions occurred in this chunk.
        """
        events: list[ToneEvent] = []
        if samples.size == 0:
            return events

        buf = np.concatenate([self._leftover, samples.astype(np.float32, copy=False)])

        n_frames = buf.size // self._frame_size
        for i in range(n_frames):
            frame = buf[i * self._frame_size : (i + 1) * self._frame_size]
            vote = self._classify_frame(frame)
            self._apply_debounce(vote, events)

        # Whatever didn't fit into a full frame is carried over.
        self._leftover = buf[n_frames * self._frame_size :]
        return events

    # ----- internals -----------------------------------------------------

    def _classify_frame(self, frame: np.ndarray) -> str | None:
        """Return the DTMF char this frame votes for, or None for silence."""
        low_mags = [goertzel_magnitude(frame, f, self._sample_rate) for f in DTMF_LOW_FREQS]
        high_mags = [goertzel_magnitude(frame, f, self._sample_rate) for f in DTMF_HIGH_FREQS]

        best_low_idx = int(np.argmax(low_mags))
        best_high_idx = int(np.argmax(high_mags))

        if (
            low_mags[best_low_idx] < self._min_magnitude
            or high_mags[best_high_idx] < self._min_magnitude
        ):
            return None

        return DTMF_KEYS[(DTMF_LOW_FREQS[best_low_idx], DTMF_HIGH_FREQS[best_high_idx])]

    def _apply_debounce(self, vote: str | None, events: list[ToneEvent]) -> None:
        """Update debounce state and append any emitted events to ``events``."""
        if vote == self._candidate:
            self._run_count += 1
        else:
            self._candidate = vote
            self._run_count = 1

        if self._run_count >= self._debounce_frames and self._candidate != self._held:
            old_held = self._held
            self._held = self._candidate
            # Stop-of-old comes before start-of-new so listeners that route
            # only "stop" events into the command state machine see the
            # release before the next press.
            if old_held is not None:
                events.append(ToneEvent(kind="stop", char=old_held))
            if self._held is not None:
                events.append(ToneEvent(kind="start", char=self._held))
