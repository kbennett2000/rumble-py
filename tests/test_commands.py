# Tests for the Dispatcher.
#
# Uses FakeMumbleClient / FakeAudioCapture / FakeTextToSpeech so we don't
# touch real audio, real Mumble, or a real TTS engine. The dispatcher's
# feed_dtmf() lets us inject characters straight into the state machine.

from __future__ import annotations

import os
import threading
import time

import pytest

from rumble.commands import Dispatcher
from rumble.config import (
    AudioConfig,
    Bank,
    ChannelMapping,
    IdentConfig,
    MumbleServerConfig,
    RumbleConfig,
    WebConfig,
)
from rumble.dtmf_detector import ToneEvent
from tests._dispatcher_fakes import (
    FakeAudioCaptureFactory,
    FakeMumbleFactory,
    FakeTtsFactory,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _two_bank_config() -> RumbleConfig:
    """Build a config with two banks and overlapping DTMF codes."""
    return RumbleConfig(
        callsign="W1TEST",
        banks={
            0: Bank(
                servers=(
                    MumbleServerConfig(
                        name="local",
                        host="127.0.0.1",
                        port=64738,
                        username="W1TEST",
                    ),
                ),
                channels=(
                    ChannelMapping(
                        server_number="001",
                        channel_number="1",
                        server_ref="local",
                        channel_path="Root/General",
                        nickname="local general",
                    ),
                    ChannelMapping(
                        server_number="001",
                        channel_number="2",
                        server_ref="local",
                        channel_path="Root/Lobby",
                        nickname="local lobby",
                    ),
                ),
            ),
            1: Bank(
                servers=(
                    MumbleServerConfig(
                        name="remote",
                        host="mumble.example.org",
                        port=64738,
                        username="W1TEST",
                    ),
                ),
                channels=(
                    ChannelMapping(
                        server_number="001",
                        channel_number="1",
                        server_ref="remote",
                        channel_path="Root/Repeaters/Midwest",
                        nickname="midwest",
                    ),
                ),
            ),
        },
        audio=AudioConfig(),
        ident=IdentConfig(),
        web=WebConfig(),
        initial_bank=0,
    )


@pytest.fixture
def dispatcher_under_test():
    """Yield (dispatcher, mumble_factory, audio_factory, tts_factory)."""
    cfg = _two_bank_config()
    mf = FakeMumbleFactory()
    af = FakeAudioCaptureFactory()
    tf = FakeTtsFactory()
    d = Dispatcher(
        cfg,
        mumble_factory=mf,
        audio_capture_factory=af,
        tts_factory=tf,
    )
    d.start()
    try:
        # The TTS worker is a daemon thread with its own pacing — give it a
        # moment to drain the startup announcement so we don't race tests
        # that count synthesize calls.
        _wait_until(lambda: any("listening" in s for s in tf.last.synthesize_calls))
        yield d, mf, af, tf
    finally:
        d.stop()


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _feed_string(d: Dispatcher, s: str) -> None:
    for ch in s:
        d.feed_dtmf(ch)


# ---------------------------------------------------------------------------
# Command dispatch — happy paths
# ---------------------------------------------------------------------------


class TestDispatchDisconnect:
    def test_hash_star_moves_to_root_and_speaks(self, dispatcher_under_test) -> None:
        d, mf, _af, tf = dispatcher_under_test
        _feed_string(d, "#*")
        assert mf.last.move_to_channel_calls[-1] == "Root"
        assert _wait_until(lambda: any("disconnected" in s for s in tf.last.synthesize_calls))


class TestDispatchChangeChannel:
    def test_known_mapping_moves_and_speaks_nickname(self, dispatcher_under_test) -> None:
        d, mf, _af, tf = dispatcher_under_test
        _feed_string(d, "#001#1*")
        assert "Root/General" in mf.last.move_to_channel_calls
        assert _wait_until(lambda: any("local general" in s for s in tf.last.synthesize_calls))

    def test_unknown_mapping_does_not_move(self, dispatcher_under_test) -> None:
        d, mf, _af, tf = dispatcher_under_test
        before = list(mf.last.move_to_channel_calls)
        _feed_string(d, "#001#9*")
        assert mf.last.move_to_channel_calls == before
        assert _wait_until(lambda: any("not found" in s for s in tf.last.synthesize_calls))

    def test_switching_to_different_server_reconnects(self, dispatcher_under_test) -> None:
        d, mf, _af, tf = dispatcher_under_test
        # Bank 0 active; switch bank then attempt a channel that's only in bank 1.
        _feed_string(d, "#1**")
        _wait_until(lambda: d.active_bank == 1)
        _feed_string(d, "#001#1*")
        # Two fakes created (one per server), the second one moved to Midwest.
        assert len(mf.created) == 2
        assert mf.created[1].server.name == "remote"
        assert "Root/Repeaters/Midwest" in mf.created[1].move_to_channel_calls
        # And the original mumble client was disconnected.
        assert mf.created[0].disconnect_calls == 1


class TestDispatchLoadConfig:
    def test_load_config_switches_active_bank(self, dispatcher_under_test) -> None:
        d, _mf, _af, tf = dispatcher_under_test
        _feed_string(d, "#1**")
        assert _wait_until(lambda: d.active_bank == 1)
        assert _wait_until(lambda: any("loaded bank 1" in s for s in tf.last.synthesize_calls))

    def test_load_config_missing_bank_speaks_not_configured(self, dispatcher_under_test) -> None:
        d, _mf, _af, tf = dispatcher_under_test
        _feed_string(d, "#7**")
        # Active bank shouldn't move.
        assert d.active_bank == 0
        assert _wait_until(lambda: any("not configured" in s for s in tf.last.synthesize_calls))

    def test_load_config_then_channel_uses_new_mapping(self, dispatcher_under_test) -> None:
        d, _mf, _af, _tf = dispatcher_under_test
        # 001/1 in bank 0 → Root/General; in bank 1 → Midwest.
        _feed_string(d, "#1**")
        _wait_until(lambda: d.active_bank == 1)
        _feed_string(d, "#001#1*")
        # 001/1 should now resolve to the bank-1 mapping (Midwest), reachable
        # through the freshly-created MumbleClient for the "remote" server.
        assert _wait_until(
            lambda: "Root/Repeaters/Midwest" in _all_moves(d),
            timeout=2.0,
        )


def _all_moves(d: Dispatcher) -> list[str]:
    """Flatten every move_to_channel call across every fake client created."""
    moves: list[str] = []
    if d.mumble is not None:
        moves.extend(getattr(d.mumble, "move_to_channel_calls", []))
    return moves


# ---------------------------------------------------------------------------
# Sticky mute / admin settings
# ---------------------------------------------------------------------------


class TestStickyMute:
    def test_admin_00_0_sets_sticky_mute(self, dispatcher_under_test) -> None:
        d, mf, _af, tf = dispatcher_under_test
        _feed_string(d, "#00#0*")
        assert d.sticky_mute is True
        # And we issued a real mute to Mumble.
        assert mf.last.set_mute_calls.count(True) >= 1
        assert _wait_until(lambda: any(s == "muted" for s in tf.last.synthesize_calls))

    def test_admin_00_1_clears_sticky_mute(self, dispatcher_under_test) -> None:
        d, mf, _af, tf = dispatcher_under_test
        _feed_string(d, "#00#0*")
        assert d.sticky_mute is True
        _feed_string(d, "#00#1*")
        assert d.sticky_mute is False
        assert _wait_until(lambda: any(s == "unmuted" for s in tf.last.synthesize_calls))

    def test_sticky_mute_blocks_auto_unmute_after_command(self, dispatcher_under_test) -> None:
        d, mf, _af, _tf = dispatcher_under_test
        # Engage sticky mute.
        _feed_string(d, "#00#0*")
        # Now run a normal command. The auto-mute-on-#/unmute-on-end flow
        # should NOT issue a set_mute(False) because sticky is engaged.
        mf.last.set_mute_calls.clear()
        _feed_string(d, "#*")
        # No False call should have been recorded.
        assert False not in mf.last.set_mute_calls

    def test_unknown_admin_setting_logs_and_speaks(self, dispatcher_under_test) -> None:
        d, _mf, _af, tf = dispatcher_under_test
        _feed_string(d, "#03#1*")  # not a configured admin code
        assert _wait_until(
            lambda: any("unknown admin setting" in s for s in tf.last.synthesize_calls)
        )


# ---------------------------------------------------------------------------
# Mute during command entry
# ---------------------------------------------------------------------------


class TestMuteDuringCommandEntry:
    def test_tone_start_mutes_mumble(self, dispatcher_under_test) -> None:
        d, mf, af, _tf = dispatcher_under_test
        mf.last.set_mute_calls.clear()
        # Drive the full tone-event path (not feed_dtmf), since muting
        # happens on tone start which feed_dtmf doesn't emit.
        af.last.fire(ToneEvent("start", "#"))
        assert mf.last.set_mute_calls == [True]

    def test_completed_command_unmutes_mumble(self, dispatcher_under_test) -> None:
        d, mf, af, _tf = dispatcher_under_test
        mf.last.set_mute_calls.clear()
        # # then * = Disconnect. After the * stop event, state is idle
        # and we should unmute.
        af.last.fire(ToneEvent("start", "#"))
        af.last.fire(ToneEvent("stop", "#"))
        af.last.fire(ToneEvent("start", "*"))
        af.last.fire(ToneEvent("stop", "*"))
        # We expect True somewhere from the starts and False from the final stop.
        assert True in mf.last.set_mute_calls
        assert False in mf.last.set_mute_calls
        # Last call should be the unmute.
        assert mf.last.set_mute_calls[-1] is False


# ---------------------------------------------------------------------------
# Lifecycle / threading
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_stop_is_idempotent(self) -> None:
        cfg = _two_bank_config()
        mf, af, tf = FakeMumbleFactory(), FakeAudioCaptureFactory(), FakeTtsFactory()
        d = Dispatcher(
            cfg,
            mumble_factory=mf,
            audio_capture_factory=af,
            tts_factory=tf,
        )
        d.start()
        d.stop()
        d.stop()  # second stop is a no-op, must not raise

    def test_is_running_flag(self) -> None:
        cfg = _two_bank_config()
        mf, af, tf = FakeMumbleFactory(), FakeAudioCaptureFactory(), FakeTtsFactory()
        d = Dispatcher(
            cfg,
            mumble_factory=mf,
            audio_capture_factory=af,
            tts_factory=tf,
        )
        assert not d.is_running
        d.start()
        assert d.is_running
        d.stop()
        assert not d.is_running

    def test_wait_returns_when_stopped_from_other_thread(self) -> None:
        cfg = _two_bank_config()
        mf, af, tf = FakeMumbleFactory(), FakeAudioCaptureFactory(), FakeTtsFactory()
        d = Dispatcher(
            cfg,
            mumble_factory=mf,
            audio_capture_factory=af,
            tts_factory=tf,
        )
        d.start()
        stopper = threading.Thread(target=lambda: (time.sleep(0.05), d.stop()))
        stopper.start()
        d.wait()  # blocks until the other thread calls stop
        stopper.join()
        assert not d.is_running

    def test_concurrent_feed_dtmf_does_not_crash(self, dispatcher_under_test) -> None:
        d, _mf, _af, _tf = dispatcher_under_test

        def feeder(seq: str) -> None:
            for ch in seq:
                d.feed_dtmf(ch)

        threads = [
            threading.Thread(target=feeder, args=(seq,))
            for seq in ["#*", "#00#0*", "#001#1*", "#001#2*"]
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)
        # If we got here without deadlock or exception, we're good.
        assert d.is_running


# ---------------------------------------------------------------------------
# Optional integration test against the docker server
# ---------------------------------------------------------------------------


INTEGRATION_ENABLED = os.environ.get("RUMBLE_INTEGRATION") == "1"

requires_integration = pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="requires the local dev Mumble server (RUMBLE_INTEGRATION=1)",
)


@requires_integration
class TestIntegrationDispatcher:
    def test_dispatch_change_channel_against_live_mumble(self) -> None:
        # Real Mumble client against the docker dev server. The dev server
        # only has Root by default, so we point the test mapping at Root —
        # the path through the dispatcher (state machine → command lookup
        # → move_to_channel) is what's being exercised, not the move itself.
        # Real channel hierarchies are out of scope for milestone 5.
        from rumble.commands import _default_mumble_factory

        cfg = RumbleConfig(
            callsign="W1TEST",
            banks={
                0: Bank(
                    servers=(
                        MumbleServerConfig(
                            name="local",
                            host="127.0.0.1",
                            port=64738,
                            username="rumble-dispatcher-test",
                        ),
                    ),
                    channels=(
                        ChannelMapping(
                            server_number="001",
                            channel_number="1",
                            server_ref="local",
                            channel_path="Root",
                            nickname="root channel",
                        ),
                    ),
                ),
            },
            audio=AudioConfig(),
            ident=IdentConfig(),
            web=WebConfig(),
            initial_bank=0,
        )

        af = FakeAudioCaptureFactory()
        tf = FakeTtsFactory()
        d = Dispatcher(
            cfg,
            mumble_factory=_default_mumble_factory,
            audio_capture_factory=af,
            tts_factory=tf,
        )
        d.start()
        try:
            for ch in "#001#1*":
                d.feed_dtmf(ch)
            assert _wait_until(
                lambda: d.mumble is not None and d.mumble.current_channel == "Root",
                timeout=5.0,
            ), (
                f"channel did not reach Root; "
                f"actual={d.mumble.current_channel if d.mumble else None!r}"
            )
            assert _wait_until(
                lambda: any("root channel" in s for s in tf.last.synthesize_calls),
                timeout=5.0,
            )
        finally:
            d.stop()
