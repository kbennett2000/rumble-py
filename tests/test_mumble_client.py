# Tests for MumbleClient.
#
# Two layers:
#
# A) Unit tests — pure construction/property/state checks. Always run.
# B) Integration tests — require the local dev Mumble server (see
#    docker/docker-compose.yml). Skipped unless RUMBLE_INTEGRATION=1 is set.

from __future__ import annotations

import os
import threading
import time

import pytest

from rumble.mumble_client import (
    ConnectionState,
    MumbleAudioFrame,
    MumbleChannelNotFoundError,
    MumbleClient,
    MumbleConnectionError,
    MumbleError,
    MumbleNotConnectedError,
)
from tests._mumble_helpers import synth_pcm

INTEGRATION_ENABLED = os.environ.get("RUMBLE_INTEGRATION") == "1"

requires_integration = pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="requires the local dev Mumble server (set RUMBLE_INTEGRATION=1)",
)


# ===========================================================================
# A) Unit tests — no network
# ===========================================================================


class TestConstructorValidation:
    def test_empty_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="host"):
            MumbleClient(host="", username="alice")

    def test_empty_username_rejected(self) -> None:
        with pytest.raises(ValueError, match="username"):
            MumbleClient(host="localhost", username="")

    def test_default_port_is_64738(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        assert client._port == 64738

    def test_username_is_exposed_via_property(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        assert client.username == "alice"


class TestExceptionHierarchy:
    @pytest.mark.parametrize(
        "exc_type",
        [MumbleConnectionError, MumbleChannelNotFoundError, MumbleNotConnectedError],
    )
    def test_subclasses_are_catchable_as_base(self, exc_type: type) -> None:
        with pytest.raises(MumbleError):
            raise exc_type("boom")


class TestDefaultProperties:
    def test_initial_state_is_disconnected(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        assert client.state is ConnectionState.DISCONNECTED
        assert client.is_connected is False

    def test_no_channel_when_disconnected(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        assert client.current_channel is None

    def test_no_users_when_disconnected(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        assert client.users_in_current_channel == []

    def test_initially_unmuted_undeafened(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        assert client.muted is False
        assert client.deafened is False


class TestPreConnectStateCaching:
    def test_set_mute_before_connect_is_remembered(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        client.set_mute(True)
        assert client.muted is True

    def test_set_deaf_before_connect_is_remembered(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        client.set_deaf(True)
        assert client.deafened is True

    def test_send_audio_when_disconnected_is_noop(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        # No raise, no side effects.
        client.send_audio(b"\x00" * 1024)

    def test_move_to_channel_when_disconnected_raises(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        with pytest.raises(MumbleNotConnectedError):
            client.move_to_channel("Root/General")

    def test_disconnect_when_disconnected_is_noop(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        client.disconnect()
        client.disconnect()
        assert client.state is ConnectionState.DISCONNECTED


class TestListenerRegistration:
    def test_can_register_multiple_listeners(self) -> None:
        client = MumbleClient(host="localhost", username="alice")
        calls: list[ConnectionState] = []
        client.on_state_changed(lambda s: calls.append(s))
        client.on_state_changed(lambda s: calls.append(s))
        # Trigger a state transition directly (private API; this is OK in a test).
        with client._lock:
            client._transition_state(ConnectionState.CONNECTING)
        assert calls == [ConnectionState.CONNECTING, ConnectionState.CONNECTING]

    def test_bad_listener_does_not_break_others(self, caplog: pytest.LogCaptureFixture) -> None:
        client = MumbleClient(host="localhost", username="alice")
        good_calls: list[ConnectionState] = []

        def bad(_: ConnectionState) -> None:
            raise RuntimeError("oops")

        client.on_state_changed(bad)
        client.on_state_changed(lambda s: good_calls.append(s))

        with client._lock:
            client._transition_state(ConnectionState.CONNECTING)

        assert good_calls == [ConnectionState.CONNECTING]


# ===========================================================================
# B) Integration tests — require docker dev Mumble server
# ===========================================================================
#
# These tests rely on the server brought up by docker/docker-compose.yml.
# Skipped by default; run with `RUMBLE_INTEGRATION=1 pytest -v -k mumble`.


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll `predicate` until it returns truthy or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@requires_integration
class TestIntegrationConnect:
    def test_connect_to_dev_server(self) -> None:
        client = MumbleClient(host="localhost", username="rumble-test-1")
        client.connect(timeout=10.0)
        try:
            assert client.is_connected
            assert client.state is ConnectionState.CONNECTED
            # The dev server's root channel is named "Root".
            assert client.current_channel == "Root"
        finally:
            client.disconnect()

    def test_move_to_nonexistent_channel_raises(self) -> None:
        client = MumbleClient(host="localhost", username="rumble-test-2")
        client.connect(timeout=10.0)
        try:
            with pytest.raises(MumbleChannelNotFoundError):
                client.move_to_channel("Root/DoesNotExist")
        finally:
            client.disconnect()

    def test_two_clients_see_each_other(self) -> None:
        a = MumbleClient(host="localhost", username="rumble-test-a")
        b = MumbleClient(host="localhost", username="rumble-test-b")
        a.connect(timeout=10.0)
        b.connect(timeout=10.0)
        try:
            assert _wait_until(
                lambda: "rumble-test-b" in a.users_in_current_channel, timeout=5.0
            ), f"A did not see B; saw {a.users_in_current_channel!r}"
            assert _wait_until(
                lambda: "rumble-test-a" in b.users_in_current_channel, timeout=5.0
            ), f"B did not see A; saw {b.users_in_current_channel!r}"
        finally:
            a.disconnect()
            b.disconnect()


@requires_integration
class TestIntegrationAudio:
    def test_audio_round_trip(self) -> None:
        # A sends one second of synthetic sine; B's listener should fire.
        a = MumbleClient(host="localhost", username="rumble-audio-a")
        b = MumbleClient(host="localhost", username="rumble-audio-b")

        received: list[MumbleAudioFrame] = []
        b.on_audio_received(received.append)

        a.connect(timeout=10.0)
        b.connect(timeout=10.0)
        try:
            # B needs to be settled (in same channel as A) before A speaks.
            assert _wait_until(lambda: "rumble-audio-a" in b.users_in_current_channel, timeout=5.0)
            a.send_audio(synth_pcm(duration_s=1.0, freq=440.0))
            # Give pymumble time to packetize, ship over Opus, decode on B.
            assert _wait_until(
                lambda: len(received) > 0, timeout=8.0
            ), "B did not receive any audio from A"
            assert any(f.user_name == "rumble-audio-a" for f in received)
            assert all(len(f.pcm) > 0 for f in received)
            assert all(f.sample_rate == 48000 for f in received)
        finally:
            a.disconnect()
            b.disconnect()


@requires_integration
class TestIntegrationReconnect:
    def test_reconnects_after_drop(self) -> None:
        # Simulate an unexpected drop. We close the underlying socket *and*
        # invoke our internal drop handler directly, because pymumble 1.6.1
        # does not reliably fire its DISCONNECTED callback after a socket
        # close on Python 3.12 — its run() catches socket.error but not
        # ValueError, and a closed-FD select() raises the latter. Our
        # reconnect machinery is what we actually need to exercise; pymumble
        # drop detection is upstream's responsibility.
        client = MumbleClient(host="localhost", username="rumble-reconnect", reconnect=True)
        states: list[ConnectionState] = []
        client.on_state_changed(states.append)

        client.connect(timeout=10.0)
        try:
            assert client.is_connected
            attempts_before = client._reconnect_attempts
            MumbleClient._safe_close_socket(client._mumble)
            client._on_unexpected_disconnect()

            # Should transition: CONNECTED -> RECONNECTING -> CONNECTED
            assert _wait_until(
                lambda: client.state is ConnectionState.RECONNECTING, timeout=5.0
            ), f"never saw RECONNECTING; states={[s.name for s in states]}"
            assert _wait_until(
                lambda: client.is_connected, timeout=15.0
            ), f"did not reconnect; states={[s.name for s in states]}"

            assert client._reconnect_attempts > attempts_before
            state_names = [s.name for s in states]
            assert "RECONNECTING" in state_names
            assert state_names.count("CONNECTED") >= 2
        finally:
            client.disconnect()

    def test_disconnect_during_reconnect_yields_failed(self) -> None:
        # Point at a port that nobody's listening on so reconnect can never
        # succeed; then call disconnect() while we're stuck in the backoff
        # loop and confirm we end in FAILED.
        client = MumbleClient(
            host="127.0.0.1",
            port=1,  # nothing listens on 1
            username="rumble-reconnect-fail",
            reconnect=True,
        )
        # Skip the initial connect (which would also fail) — manually set up
        # the state and kick off the reconnect loop. We mimic exactly what
        # _on_unexpected_disconnect would do.
        client._state = ConnectionState.CONNECTED  # pretend we were connected
        client._on_unexpected_disconnect()
        assert _wait_until(lambda: client.state is ConnectionState.RECONNECTING, timeout=2.0)
        # While the reconnect thread is sleeping in backoff, cancel.
        client.disconnect()
        assert _wait_until(lambda: client.state is ConnectionState.FAILED, timeout=5.0)

        # And the reconnect thread must have exited.
        thread = client._reconnect_thread
        assert thread is None or not thread.is_alive()


@requires_integration
class TestIntegrationMuteRoundtrip:
    def test_mute_round_trip_visible_to_peer(self) -> None:
        a = MumbleClient(host="localhost", username="rumble-mute-a")
        b = MumbleClient(host="localhost", username="rumble-mute-b")
        a.connect(timeout=10.0)
        b.connect(timeout=10.0)
        try:
            assert _wait_until(lambda: "rumble-mute-a" in b.users_in_current_channel, timeout=5.0)
            a.set_mute(True)

            # B observes A's self_mute flag via pymumble's user model.
            def a_is_muted_per_b() -> bool:
                for u in b._mumble.users.values():
                    if u["name"] == "rumble-mute-a":
                        return bool(u.get("self_mute", False))
                return False

            assert _wait_until(
                a_is_muted_per_b, timeout=5.0
            ), "B did not observe A's mute state within timeout"
        finally:
            a.disconnect()
            b.disconnect()


@requires_integration
class TestThreadSafetyOfPublicAPI:
    def test_concurrent_property_reads_do_not_crash(self) -> None:
        client = MumbleClient(host="localhost", username="rumble-thread")
        client.connect(timeout=10.0)
        try:
            stop = threading.Event()

            def reader() -> None:
                while not stop.is_set():
                    _ = client.is_connected
                    _ = client.current_channel
                    _ = client.users_in_current_channel
                    _ = client.muted

            threads = [threading.Thread(target=reader) for _ in range(4)]
            for t in threads:
                t.start()
            time.sleep(0.5)
            stop.set()
            for t in threads:
                t.join(timeout=2.0)
        finally:
            client.disconnect()
