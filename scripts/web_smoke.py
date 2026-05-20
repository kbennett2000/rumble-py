#!/usr/bin/env python3
# Manual harness: start the dispatcher (with the web UI) against a real Mumble
# server, print the URL, and wait for Ctrl-C. Use this to click around in a
# browser without running the full DTMF audio pipeline.

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from types import FrameType

from rumble.commands import Dispatcher
from rumble.config import ConfigError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Spin up a dispatcher + web UI for manual browser testing."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.dev.yaml"),
        help="Path to YAML config (default: config.dev.yaml).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if not config.web.enabled:
        print(
            "warning: web.enabled is false in this config; the web UI won't start.",
            file=sys.stderr,
        )

    dispatcher = Dispatcher(config, config_path=args.config)

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        logging.info("received signal %d, shutting down", signum)
        dispatcher.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    dispatcher.start()

    if config.web.enabled:
        # The dispatcher's own log line says the same thing, but print it
        # clearly here too so the manual operator sees it above the noise.
        print(
            f"\n→ Web UI at http://{config.web.host}:{config.web.port}/  "
            "(Ctrl-C to stop)\n",
            flush=True,
        )

    try:
        dispatcher.wait()
    except KeyboardInterrupt:
        dispatcher.stop()

    # Brief settle so the uvicorn / TTS threads land their final logs.
    time.sleep(0.2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
