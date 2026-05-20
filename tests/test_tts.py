# Smoke tests for the TTS module.
#
# pyttsx3 needs a real engine (espeak-ng on Linux, SAPI on Windows). If the
# engine can't be initialized we skip — no fakes here, the whole point of
# this test is to exercise the real synthesis + WAV-read + resample path.

from __future__ import annotations

import numpy as np
import pytest

from rumble.tts import (
    TARGET_SAMPLE_RATE,
    TextToSpeech,
    TtsError,
    _read_and_convert,
    _resample_linear,
)


@pytest.fixture(scope="module")
def tts() -> TextToSpeech:
    try:
        return TextToSpeech()
    except TtsError as exc:
        pytest.skip(f"TTS engine unavailable: {exc}")


class TestSynthesize:
    def test_returns_non_empty_pcm(self, tts: TextToSpeech) -> None:
        pcm = tts.synthesize("hello world")
        # Should be at least ~10 ms of audio at 48 kHz mono int16 — i.e.
        # 48000 * 0.01 * 2 = 960 bytes. The spec's ">1000 bytes" floor.
        assert len(pcm) > 1000, f"only got {len(pcm)} bytes"

    def test_output_is_int16_at_target_rate(self, tts: TextToSpeech) -> None:
        pcm = tts.synthesize("test")
        # Must be a multiple of 2 (int16).
        assert len(pcm) % 2 == 0
        # And not absurdly long for a short phrase.
        n_samples = len(pcm) // 2
        max_samples = TARGET_SAMPLE_RATE * 5  # 5 seconds is generous
        assert n_samples < max_samples


# ---------------------------------------------------------------------------
# Pure-numpy helper tests (no engine needed)
# ---------------------------------------------------------------------------


class TestResampleLinear:
    def test_passthrough_when_rates_match(self) -> None:
        a = np.array([1, 2, 3, 4, 5], dtype=np.int16)
        result = _resample_linear(a, 48000, 48000)
        assert np.array_equal(result, a)

    def test_upsample_doubles_length(self) -> None:
        a = np.array([0, 100, 0, 100], dtype=np.int16)
        result = _resample_linear(a, 24000, 48000)
        assert result.size == 8

    def test_downsample_halves_length(self) -> None:
        a = np.zeros(100, dtype=np.int16)
        result = _resample_linear(a, 48000, 24000)
        assert result.size == 50

    def test_returns_int16_dtype(self) -> None:
        a = np.array([1, 2, 3], dtype=np.int16)
        result = _resample_linear(a, 22050, 48000)
        assert result.dtype == np.int16


class TestReadAndConvert:
    def test_reads_a_minimal_wav(self, tmp_path) -> None:
        # Synthesize a minimal mono 22050 Hz WAV; confirm we get back 48 kHz
        # mono int16 bytes.
        import wave

        wav_path = tmp_path / "tone.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            # Half a second of silence — easier than a tone for this test.
            w.writeframes(b"\x00\x00" * (22050 // 2))

        pcm = _read_and_convert(str(wav_path))
        # 22050 Hz × 0.5 s = 11025 samples; upsampled to 48 kHz → 24000.
        # Each sample is 2 bytes.
        assert abs(len(pcm) - 24000 * 2) < 10
