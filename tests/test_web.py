# Tests for the FastAPI web UI.
#
# These don't bind to a real port — TestClient runs FastAPI in-process and
# drives it directly. SSE streaming is smoke-checked only (content-type +
# initial connect); driving the full stream with real async timing belongs
# in a manual harness, not a unit test.

from __future__ import annotations

import asyncio
import logging
import threading

import pytest
from fastapi.testclient import TestClient

from rumble.config import (
    AudioConfig,
    Bank,
    ChannelMapping,
    ConfigError,
    IdentConfig,
    MumbleServerConfig,
    RumbleConfig,
    WebConfig,
)
from rumble.web.app import _sse_format, create_app
from rumble.web.log_buffer import (
    LogBuffer,
    RingBufferHandler,
    install_log_capture,
    uninstall_log_capture,
)
from tests._dispatcher_fakes import FakeDispatcher

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _config_two_banks() -> RumbleConfig:
    return RumbleConfig(
        callsign="W1WEB",
        banks={
            0: Bank(
                servers=(MumbleServerConfig(name="local", host="127.0.0.1", port=64738),),
                channels=(
                    ChannelMapping(
                        server_number="001",
                        channel_number="1",
                        server_ref="local",
                        channel_path="Root",
                        nickname="local root",
                    ),
                ),
            ),
            1: Bank(
                servers=(MumbleServerConfig(name="other", host="other.example"),),
                channels=(),
            ),
        },
        audio=AudioConfig(),
        ident=IdentConfig(),
        web=WebConfig(),
        initial_bank=0,
    )


@pytest.fixture
def fake_and_client():
    fake = FakeDispatcher(_config_two_banks())
    app = create_app(fake, log_buffer=fake.log_buffer)
    with TestClient(app) as client:
        yield fake, client


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------


class TestPages:
    def test_index_returns_200_with_callsign(self, fake_and_client) -> None:
        _fake, client = fake_and_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "W1WEB" in resp.text
        # And the keypad is rendered.
        assert ">1<" in resp.text and ">#<" in resp.text

    def test_status_partial_contains_state_and_channel(self, fake_and_client) -> None:
        _fake, client = fake_and_client
        resp = client.get("/partials/status")
        assert resp.status_code == 200
        # The fake reports CONNECTED on the default connect().
        assert "CONNECTED" in resp.text
        # And Root is the default channel after FakeMumbleClient.connect().
        assert "Root" in resp.text

    def test_channels_partial_lists_configured_channels(self, fake_and_client) -> None:
        _fake, client = fake_and_client
        resp = client.get("/partials/channels")
        assert resp.status_code == 200
        assert "001/1" in resp.text
        assert "local root" in resp.text


# ---------------------------------------------------------------------------
# DTMF action
# ---------------------------------------------------------------------------


class TestDtmfAction:
    def test_posting_dtmf_invokes_feed(self, fake_and_client) -> None:
        fake, client = fake_and_client
        resp = client.post("/actions/dtmf", data={"char": "#"})
        assert resp.status_code == 200
        assert fake.feed_dtmf_calls == ["#"]
        # Response is the status partial.
        assert "CONNECTED" in resp.text

    def test_lowercase_letter_is_uppercased(self, fake_and_client) -> None:
        fake, client = fake_and_client
        client.post("/actions/dtmf", data={"char": "a"})
        assert fake.feed_dtmf_calls == ["A"]

    @pytest.mark.parametrize("bad", ["", "##", "X", "12"])
    def test_invalid_char_is_4xx(self, fake_and_client, bad: str) -> None:
        fake, client = fake_and_client
        resp = client.post("/actions/dtmf", data={"char": bad})
        # Empty string fails FastAPI's input validation (422) before our
        # handler runs; the other bad inputs reach our handler and get 400.
        # Either way it's a 4xx and feed_dtmf must not have been called.
        assert 400 <= resp.status_code < 500
        assert fake.feed_dtmf_calls == []

    def test_missing_char_is_4xx(self, fake_and_client) -> None:
        fake, client = fake_and_client
        resp = client.post("/actions/dtmf", data={})
        # FastAPI returns 422 for missing required form fields.
        assert resp.status_code == 422
        assert fake.feed_dtmf_calls == []


# ---------------------------------------------------------------------------
# Bank action
# ---------------------------------------------------------------------------


class TestBankAction:
    def test_valid_bank_switches(self, fake_and_client) -> None:
        fake, client = fake_and_client
        resp = client.post("/actions/bank", data={"bank": "1"})
        assert resp.status_code == 200
        assert fake.set_bank_calls == [1]

    def test_invalid_bank_returns_4xx(self, fake_and_client) -> None:
        fake, client = fake_and_client
        resp = client.post("/actions/bank", data={"bank": "9"})
        assert resp.status_code == 400
        assert "not configured" in resp.json()["detail"]
        assert fake.set_bank_calls == []


# ---------------------------------------------------------------------------
# Mute action
# ---------------------------------------------------------------------------


class TestMuteAction:
    def test_mute_true_mutes(self, fake_and_client) -> None:
        fake, client = fake_and_client
        resp = client.post("/actions/mute", data={"muted": "true"})
        assert resp.status_code == 200
        assert fake.mumble.set_mute_calls[-1] is True

    def test_mute_false_unmutes(self, fake_and_client) -> None:
        fake, client = fake_and_client
        # Start muted, then explicitly unmute.
        fake.mumble.set_mute(True)
        resp = client.post("/actions/mute", data={"muted": "false"})
        assert resp.status_code == 200
        assert fake.mumble.set_mute_calls[-1] is False


# ---------------------------------------------------------------------------
# Disconnect action
# ---------------------------------------------------------------------------


class TestDisconnectAction:
    def test_disconnect_moves_to_root(self, fake_and_client) -> None:
        fake, client = fake_and_client
        # Move away from Root first so we can detect the move-back.
        fake.mumble.move_to_channel("Root/General")
        resp = client.post("/actions/disconnect")
        assert resp.status_code == 200
        assert fake.mumble.move_to_channel_calls[-1] == "Root"


# ---------------------------------------------------------------------------
# Reload config action
# ---------------------------------------------------------------------------


class TestReloadConfigAction:
    def test_happy_path(self, fake_and_client) -> None:
        fake, client = fake_and_client
        resp = client.post("/actions/reload-config")
        assert resp.status_code == 200
        assert len(fake.reload_config_calls) == 1
        assert "config reloaded" in resp.text

    def test_config_error_returns_4xx_with_html_banner(self, fake_and_client) -> None:
        fake, client = fake_and_client
        fake.reload_should_raise = ConfigError("boom: bank 3 missing")
        resp = client.post("/actions/reload-config")
        assert resp.status_code == 400
        # Response is HTML (so HTMX can swap it into the banner slot), not
        # JSON. Uses the same banner-bad class as other warning/error
        # styles in the template.
        assert resp.headers["content-type"].startswith("text/html")
        assert "banner-bad" in resp.text
        assert "boom: bank 3 missing" in resp.text

    def test_config_error_html_escapes_payload(self, fake_and_client) -> None:
        # ConfigError messages can include user-controlled YAML strings; the
        # error banner must HTML-escape them or we'd have an XSS hole on a
        # localhost UI that, per README, can be exposed on the LAN.
        fake, client = fake_and_client
        fake.reload_should_raise = ConfigError("<script>alert(1)</script>")
        resp = client.post("/actions/reload-config")
        assert resp.status_code == 400
        assert "<script>" not in resp.text
        assert "&lt;script&gt;" in resp.text


# ---------------------------------------------------------------------------
# SSE log stream
# ---------------------------------------------------------------------------
#
# We don't drive TestClient.stream() against this endpoint: TestClient
# doesn't propagate a disconnect cleanly enough to unwind the keepalive
# wait, and the test would hang for the full 15-second keepalive window.
# The real streaming behavior is exercised in the browser by
# scripts/web_smoke.py. Here we test what's testable in isolation:
# the route is mounted, and the LogBuffer fan-out actually works.


class TestSseRouteRegistration:
    def test_sse_route_is_mounted(self, fake_and_client) -> None:
        _fake, client = fake_and_client
        paths = {r.path for r in client.app.routes}
        assert "/events/logs" in paths


class TestSseFormatHelper:
    def test_single_line_becomes_one_data_event(self) -> None:
        out = _sse_format("hello")
        assert out == "data: hello\n\n"

    def test_multiline_becomes_multiple_data_lines(self) -> None:
        out = _sse_format("line one\nline two")
        # Two "data:" lines, terminated by a blank line.
        assert out == "data: line one\ndata: line two\n\n"


class TestLogBufferAsyncFanout:
    def test_subscriber_receives_new_record_pushed_from_another_thread(self) -> None:
        # Verifies the call_soon_threadsafe path: a record produced by a
        # different thread reaches the asyncio queue on the event loop.
        async def go() -> str:
            buf = LogBuffer()
            loop = asyncio.get_running_loop()
            queue, sub = buf.subscribe(loop)
            try:
                threading.Thread(target=lambda: buf.add("hi from worker"), daemon=True).start()
                line = await asyncio.wait_for(queue.get(), timeout=1.0)
                return line
            finally:
                buf.unsubscribe(sub)

        assert asyncio.run(go()) == "hi from worker"

    def test_unsubscribe_stops_delivery(self) -> None:
        async def go() -> bool:
            buf = LogBuffer()
            loop = asyncio.get_running_loop()
            queue, sub = buf.subscribe(loop)
            buf.unsubscribe(sub)
            buf.add("no one's listening")
            try:
                await asyncio.wait_for(queue.get(), timeout=0.2)
                return False  # we shouldn't have received anything
            except TimeoutError:
                return True

        assert asyncio.run(go()) is True


# ---------------------------------------------------------------------------
# Log buffer + handler (unit, no web)
# ---------------------------------------------------------------------------


class TestLogBuffer:
    def test_filter_captures_rumble_records(self) -> None:
        buf = LogBuffer(capacity=10)
        handler = install_log_capture(buf)
        try:
            logging.getLogger("rumble.test").info("hello from rumble")
            logging.getLogger("rumble.sub.deep").warning("warn from rumble")
        finally:
            uninstall_log_capture(handler)

        snapshot = buf.snapshot()
        assert any("hello from rumble" in line for line in snapshot)
        assert any("warn from rumble" in line for line in snapshot)

    def test_filter_ignores_non_rumble_records(self) -> None:
        buf = LogBuffer(capacity=10)
        handler = install_log_capture(buf)
        try:
            logging.getLogger("urllib3.connection").warning("ignored")
            logging.getLogger("some.other.lib").info("ignored too")
        finally:
            uninstall_log_capture(handler)

        snapshot = buf.snapshot()
        assert all("ignored" not in line for line in snapshot)

    def test_ring_buffer_trims_old_records(self) -> None:
        buf = LogBuffer(capacity=3)
        handler = RingBufferHandler(buf)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("rumble.ring")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            for i in range(5):
                logger.info("msg-%d", i)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(logging.NOTSET)

        snapshot = buf.snapshot()
        assert len(snapshot) == 3
        # Oldest two have been trimmed; we should see the last three.
        assert snapshot[-1].endswith("msg-4")
        assert snapshot[0].endswith("msg-2")
