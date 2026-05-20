# FastAPI app for the rumble-py web UI.
#
# All routes either render Jinja2 templates or invoke a method on the
# dispatcher. The dispatcher is the only state this module holds — every
# request reads fresh from dispatcher properties, so there's no caching to
# worry about.
#
# This module never imports pymumble, sounddevice, or numpy. It talks to
# the dispatcher's public API and to the LogBuffer for the SSE log tail.

from __future__ import annotations

import asyncio
import html as html_lib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from rumble import __version__
from rumble.config import ConfigError
from rumble.web.log_buffer import LogBuffer

if TYPE_CHECKING:
    from rumble.commands import Dispatcher

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_VALID_DTMF_CHARS = set("0123456789*#ABCD")


def _build_context(dispatcher: Dispatcher) -> dict[str, Any]:
    """Snapshot dispatcher state into a Jinja context dict.

    Reading dispatcher properties is cheap and safe to do once per request;
    we capture everything into one dict so the partials don't have to do
    their own dispatcher lookups (which could race against state changes).
    """
    mumble = dispatcher.mumble
    config = dispatcher.config
    bank = config.get_bank(dispatcher.active_bank)
    current_server = next(
        (
            s
            for s in bank.servers
            if mumble is not None and s.name == _current_server_name(mumble, bank)
        ),
        bank.servers[0] if bank.servers else None,
    )
    return {
        "version": __version__,
        "callsign": config.callsign,
        "active_bank": dispatcher.active_bank,
        "available_banks": dispatcher.available_banks,
        "state_name": (
            dispatcher.state.name if hasattr(dispatcher.state, "name") else str(dispatcher.state)
        ),
        "is_connected": mumble.is_connected if mumble is not None else False,
        "is_muted": mumble.muted if mumble is not None else False,
        "current_channel": mumble.current_channel if mumble is not None else None,
        "current_server_name": current_server.name if current_server is not None else None,
        "users_in_channel": mumble.users_in_current_channel if mumble is not None else [],
        "sticky_mute": dispatcher.sticky_mute,
        "dtmf_buffer": dispatcher.current_command_buffer,
        "channels": bank.channels,
        "dtmf_keys": _DTMF_KEYPAD,
    }


def _current_server_name(mumble: Any, bank: Any) -> str | None:
    """Best-effort match between mumble.host and a configured server name."""
    host = getattr(mumble, "_host", None) or getattr(mumble, "host", None)
    if host is None:
        return None
    for s in bank.servers:
        if s.host == host:
            return s.name
    return None


# 4x4 DTMF keypad layout. A-D are visually rendered but disabled — they're
# valid DTMF characters but the rumble grammar doesn't use them.
_DTMF_KEYPAD: list[list[tuple[str, bool]]] = [
    [("1", True), ("2", True), ("3", True), ("A", False)],
    [("4", True), ("5", True), ("6", True), ("B", False)],
    [("7", True), ("8", True), ("9", True), ("C", False)],
    [("*", True), ("0", True), ("#", True), ("D", False)],
]


def create_app(dispatcher: Dispatcher, log_buffer: LogBuffer | None = None) -> FastAPI:
    """Build a FastAPI app bound to a running dispatcher and log buffer.

    Args:
        dispatcher: The :class:`Dispatcher` whose state and methods this app
            exposes. The dispatcher must already be started (the app reads
            its properties on every request).
        log_buffer: Source for the SSE log tail. If ``None``, an empty
            buffer is used (no logs will stream, but the endpoint still
            answers 200).

    Returns:
        A configured :class:`FastAPI` instance, ready to hand to uvicorn.
    """
    app = FastAPI(title="rumble-py", version=__version__)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    buf = log_buffer if log_buffer is not None else LogBuffer()

    # -------------------------------------------------------------------
    # Pages and partials
    # -------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="index.html", context=_build_context(dispatcher)
        )

    @app.get("/partials/status", response_class=HTMLResponse)
    async def status_partial(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="partials/status.html",
            context=_build_context(dispatcher),
        )

    @app.get("/partials/channels", response_class=HTMLResponse)
    async def channels_partial(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="partials/channels.html",
            context=_build_context(dispatcher),
        )

    # -------------------------------------------------------------------
    # SSE log tail
    # -------------------------------------------------------------------

    @app.get("/events/logs")
    async def sse_logs(request: Request) -> StreamingResponse:
        async def stream() -> Any:
            # Replay the scrollback first.
            for line in buf.snapshot():
                yield _sse_format(line)

            loop = asyncio.get_running_loop()
            queue, sub = buf.subscribe(loop)
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        line = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        # SSE keepalive comment — tells the browser the
                        # connection is still alive without delivering data.
                        yield ": keepalive\n\n"
                        continue
                    yield _sse_format(line)
            finally:
                buf.unsubscribe(sub)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # -------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------

    @app.post("/actions/dtmf", response_class=HTMLResponse)
    async def post_dtmf(request: Request, char: str = Form(...)) -> HTMLResponse:
        char = char.strip().upper()
        if len(char) != 1 or char not in _VALID_DTMF_CHARS:
            raise HTTPException(status_code=400, detail=f"invalid DTMF char: {char!r}")
        dispatcher.feed_dtmf(char)
        return templates.TemplateResponse(
            request=request,
            name="partials/status.html",
            context=_build_context(dispatcher),
        )

    @app.post("/actions/bank", response_class=HTMLResponse)
    async def post_bank(request: Request, bank: int = Form(...)) -> HTMLResponse:
        if bank not in dispatcher.available_banks:
            raise HTTPException(
                status_code=400,
                detail=f"bank {bank} is not configured (available: "
                f"{dispatcher.available_banks})",
            )
        dispatcher.set_bank(bank)
        return templates.TemplateResponse(
            request=request,
            name="partials/status.html",
            context=_build_context(dispatcher),
        )

    @app.post("/actions/mute", response_class=HTMLResponse)
    async def post_mute(request: Request, muted: bool = Form(...)) -> HTMLResponse:
        mumble = dispatcher.mumble
        if mumble is not None:
            mumble.set_mute(muted)
        return templates.TemplateResponse(
            request=request,
            name="partials/status.html",
            context=_build_context(dispatcher),
        )

    @app.post("/actions/disconnect", response_class=HTMLResponse)
    async def post_disconnect(request: Request) -> HTMLResponse:
        mumble = dispatcher.mumble
        if mumble is not None:
            mumble.move_to_channel("Root")
        return templates.TemplateResponse(
            request=request,
            name="partials/status.html",
            context=_build_context(dispatcher),
        )

    @app.post("/actions/reload-config", response_class=HTMLResponse)
    async def post_reload_config() -> HTMLResponse:
        try:
            dispatcher.reload_config()
        except ConfigError as exc:
            # Render the error as an HTML banner so HTMX can swap it into
            # the reload-banner slot the same way the success banner is
            # handled. Escape the message because it can contain
            # user-controlled YAML fragments (paths, bank names, etc.).
            return HTMLResponse(
                "<div class='banner banner-bad'>config reload failed: "
                f"{html_lib.escape(str(exc))}</div>",
                status_code=400,
            )
        return HTMLResponse("<div class='banner banner-ok'>config reloaded</div>")

    return app


def _sse_format(line: str) -> str:
    """Format a (possibly multi-line) log entry as an SSE data event."""
    parts = line.splitlines() or [""]
    return "".join(f"data: {p}\n" for p in parts) + "\n"
