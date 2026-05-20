# Tests for the DTMF detector — Goertzel correctness + state-machine behavior.

from __future__ import annotations

import numpy as np
import pytest

from rumble.dtmf_detector import (
    DTMF_HIGH_FREQS,
    DTMF_KEYS,
    DTMF_LOW_FREQS,
    DtmfDetector,
    ToneEvent,
    goertzel_magnitude,
)
from tests._dtmf_helpers import synth_silence, synth_tone

SAMPLE_RATE = 8000
FRAME_SIZE = 205


def _sine(freq: float, duration_s: float, amplitude: float = 0.5) -> np.ndarray:
    """One pure sine wave at ``freq`` Hz of duration ``duration_s`` seconds."""
    n = int(duration_s * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# Goertzel correctness
# ---------------------------------------------------------------------------


class TestGoertzelMagnitude:
    @pytest.mark.parametrize("target", DTMF_LOW_FREQS + DTMF_HIGH_FREQS)
    def test_pure_sine_at_target_freq(self, target: int) -> None:
        # A 0.5-amplitude sine at exactly `target` should produce a strong
        # response at `target` and weak responses at all other DTMF freqs.
        samples = _sine(target, duration_s=FRAME_SIZE / SAMPLE_RATE)

        mag_at_target = goertzel_magnitude(samples, target, SAMPLE_RATE)
        assert (
            mag_at_target > 0.2
        ), f"expected magnitude > 0.2 at {target} Hz, got {mag_at_target:.4f}"

        for other in DTMF_LOW_FREQS + DTMF_HIGH_FREQS:
            if other == target:
                continue
            mag_at_other = goertzel_magnitude(samples, other, SAMPLE_RATE)
            assert mag_at_other < 0.05, (
                f"expected magnitude < 0.05 at {other} Hz "
                f"when sine is {target} Hz, got {mag_at_other:.4f}"
            )

    def test_silence_returns_near_zero(self) -> None:
        samples = np.zeros(FRAME_SIZE, dtype=np.float32)
        for freq in DTMF_LOW_FREQS + DTMF_HIGH_FREQS:
            assert goertzel_magnitude(samples, freq, SAMPLE_RATE) == 0.0

    def test_normalization_is_amplitude_over_two(self) -> None:
        # The documented invariant: a pure sine of amplitude A yields ~A/2.
        for amplitude in (0.25, 0.5, 1.0):
            samples = _sine(770, duration_s=FRAME_SIZE / SAMPLE_RATE, amplitude=amplitude)
            mag = goertzel_magnitude(samples, 770, SAMPLE_RATE)
            assert (
                abs(mag - amplitude / 2) < 0.02
            ), f"amplitude {amplitude}: expected ~{amplitude / 2}, got {mag:.4f}"


# ---------------------------------------------------------------------------
# Single-tone detection — all 16 DTMF keys
# ---------------------------------------------------------------------------


class TestDetectionOfEveryKey:
    @pytest.mark.parametrize("char", sorted(DTMF_KEYS.values()))
    def test_every_dtmf_char_is_detected(self, char: str) -> None:
        # 100 ms tone followed by 100 ms silence — enough frames on each
        # side that debouncing emits both start and stop cleanly.
        chunk = np.concatenate([synth_tone(char, 0.1), synth_silence(0.1)])
        detector = DtmfDetector()
        events = detector.process(chunk)
        assert events == [
            ToneEvent("start", char),
            ToneEvent("stop", char),
        ]


# ---------------------------------------------------------------------------
# Start/stop pairing and tone transitions
# ---------------------------------------------------------------------------


class TestStartStopBehavior:
    def test_tone_then_silence_emits_start_then_stop(self) -> None:
        chunk = np.concatenate([synth_tone("5", 0.1), synth_silence(0.1)])
        detector = DtmfDetector()
        assert detector.process(chunk) == [
            ToneEvent("start", "5"),
            ToneEvent("stop", "5"),
        ]

    def test_back_to_back_different_tones(self) -> None:
        # No silence between the two tones — the detector must emit the stop
        # of the old tone and the start of the new tone in sequence.
        chunk = np.concatenate(
            [
                synth_tone("5", 0.1),
                synth_tone("8", 0.1),
                synth_silence(0.1),
            ]
        )
        detector = DtmfDetector()
        assert detector.process(chunk) == [
            ToneEvent("start", "5"),
            ToneEvent("stop", "5"),
            ToneEvent("start", "8"),
            ToneEvent("stop", "8"),
        ]

    def test_cross_chunk_tone_continuity(self) -> None:
        # A tone that starts in chunk 1 and ends in chunk 2 should produce
        # exactly one start (in chunk 1) and one stop (in chunk 2).
        detector = DtmfDetector()
        events_1 = detector.process(synth_tone("3", 0.1))
        events_2 = detector.process(synth_silence(0.1))
        assert events_1 == [ToneEvent("start", "3")]
        assert events_2 == [ToneEvent("stop", "3")]


# ---------------------------------------------------------------------------
# Debounce filter
# ---------------------------------------------------------------------------


class TestDebouncing:
    def test_single_frame_glitch_does_not_emit(self) -> None:
        # One frame worth of tone surrounded by plenty of silence —
        # debounce_frames=2 (default) means this should be ignored entirely.
        chunk = np.concatenate(
            [
                synth_silence(0.05),
                synth_tone("5", FRAME_SIZE / SAMPLE_RATE),  # exactly one frame
                synth_silence(0.1),
            ]
        )
        detector = DtmfDetector()
        assert detector.process(chunk) == []

    def test_debounce_frames_one_passes_single_frame(self) -> None:
        # With debounce=1, the same one-frame glitch should actually emit
        # a start/stop pair. This confirms the parameter is doing its job.
        chunk = np.concatenate(
            [
                synth_silence(0.05),
                synth_tone("5", FRAME_SIZE / SAMPLE_RATE),
                synth_silence(0.1),
            ]
        )
        detector = DtmfDetector(debounce_frames=1)
        events = detector.process(chunk)
        assert ToneEvent("start", "5") in events
        assert ToneEvent("stop", "5") in events


# ---------------------------------------------------------------------------
# Silence handling
# ---------------------------------------------------------------------------


class TestSilenceHandling:
    def test_long_silence_emits_nothing(self) -> None:
        detector = DtmfDetector()
        assert detector.process(synth_silence(0.5)) == []

    def test_empty_chunk_emits_nothing(self) -> None:
        detector = DtmfDetector()
        assert detector.process(np.empty(0, dtype=np.float32)) == []

    def test_chunk_smaller_than_frame_emits_nothing(self) -> None:
        detector = DtmfDetector()
        # Less than one full frame — no detection runs at all.
        assert detector.process(synth_tone("5", 0.01)) == []


# ---------------------------------------------------------------------------
# Noise tolerance — loose checks
# ---------------------------------------------------------------------------


class TestNoiseTolerance:
    @pytest.mark.parametrize("char", ["5", "0", "*", "#", "A"])
    def test_light_gaussian_noise_does_not_break_detection(self, char: str) -> None:
        # Fixed seed so the test is deterministic.
        rng = np.random.default_rng(42)
        clean = np.concatenate([synth_tone(char, 0.1), synth_silence(0.1)])
        noisy = clean + 0.1 * rng.standard_normal(clean.shape).astype(np.float32)

        detector = DtmfDetector()
        events = detector.process(noisy)

        starts = [e for e in events if e.kind == "start"]
        stops = [e for e in events if e.kind == "stop"]
        # We don't insist on an exactly-clean output under noise — just that
        # the right character was detected at some point.
        assert ToneEvent("start", char) in starts
        assert ToneEvent("stop", char) in stops


# ---------------------------------------------------------------------------
# Reset semantics
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_after_held_tone_suppresses_stop(self) -> None:
        # Hold a tone, then reset before any silence arrives. The next chunk
        # of silence must NOT emit a stop, because reset() forgot the tone.
        detector = DtmfDetector()
        events = detector.process(synth_tone("7", 0.1))
        assert events == [ToneEvent("start", "7")]

        detector.reset()
        assert detector.process(synth_silence(0.2)) == []

    def test_reset_clears_leftover_samples(self) -> None:
        # Feed a partial-frame chunk so something is in _leftover, reset,
        # then feed a complete tone-+-silence sequence. The detection must
        # behave as if the partial chunk never existed.
        detector = DtmfDetector()
        partial = synth_tone("D", 0.01)  # less than one frame
        assert detector.process(partial) == []

        detector.reset()
        events = detector.process(np.concatenate([synth_tone("D", 0.1), synth_silence(0.1)]))
        assert events == [ToneEvent("start", "D"), ToneEvent("stop", "D")]
