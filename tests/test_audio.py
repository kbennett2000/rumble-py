# Smoke tests for the AudioCapture wrapper.
#
# Real audio I/O isn't unit-testable here — that's done manually with a
# radio in milestone 6. These tests only confirm that the public surface
# can be constructed and that the context-manager protocol is wired up.

from __future__ import annotations

from rumble.audio import AudioCapture, list_input_devices


class TestListInputDevices:
    def test_returns_a_list(self) -> None:
        devices = list_input_devices()
        assert isinstance(devices, list)
        # Each entry, if any, has the expected keys.
        for d in devices:
            assert {"index", "name", "channels", "sample_rate"} <= d.keys()
            assert isinstance(d["index"], int)
            assert isinstance(d["name"], str)
            assert isinstance(d["channels"], int)
            assert d["channels"] > 0


class TestAudioCaptureConstructor:
    def test_default_args_do_not_crash(self) -> None:
        # device=None means "use system default"; no stream is opened yet.
        cap = AudioCapture()
        assert cap is not None

    def test_explicit_none_device(self) -> None:
        AudioCapture(device=None, sample_rate=16000)


class TestAudioCaptureContextManager:
    def test_context_exit_without_start_is_clean(self) -> None:
        # Regression guard: __exit__ must be safe even if start() was never
        # called (no stream to close, no worker to join).
        with AudioCapture() as cap:
            assert cap is not None
        # If we get here, __exit__ did not raise.

    def test_stop_without_start_is_idempotent(self) -> None:
        cap = AudioCapture()
        cap.stop()
        cap.stop()
