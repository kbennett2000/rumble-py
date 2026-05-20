# Entry point — `python -m rumble --config path/to/config.yaml`.

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from types import FrameType

from rumble import __version__
from rumble.audio import list_input_devices
from rumble.commands import Dispatcher
from rumble.config import ConfigError, load_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rumble",
        description=(
            "DTMF-controlled Mumble client for linking analog amateur radios " "over the internet."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML config (see config.example.yaml for the format).",
    )
    parser.add_argument(
        "--bank",
        type=int,
        default=None,
        help="Bank to load at start; overrides config's initial_bank.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    parser.add_argument(
        "--list-audio-devices",
        action="store_true",
        help="Print available audio input devices and exit.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"rumble-py {__version__}",
    )
    return parser


def _print_audio_devices() -> None:
    devices = list_input_devices()
    if not devices:
        print("No audio input devices found.", file=sys.stderr)
        return
    print("Available audio input devices:")
    for d in devices:
        print(
            f"  [{d['index']:>2}] {d['name']}  " f"({d['channels']} ch @ {d['sample_rate']:.0f} Hz)"
        )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Include the thread name in log records — we're multi-threaded and it's
    # the single most useful field for debugging timing / locking issues.
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
    )

    if args.list_audio_devices:
        _print_audio_devices()
        return 0

    if args.config is None:
        print(
            "error: --config is required (or use --list-audio-devices)",
            file=sys.stderr,
        )
        return 2

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    dispatcher = Dispatcher(config, bank=args.bank, config_path=args.config)

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        logging.info("received signal %d, shutting down", signum)
        dispatcher.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    dispatcher.start()
    dispatcher.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
