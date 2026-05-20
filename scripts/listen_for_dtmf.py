#!/usr/bin/env python3
# Manual test harness: listen on an audio input and print every DTMF
# tone start/stop event with a timestamp. Use this to verify the detector
# against a real radio. Ctrl-C to quit.

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from rumble.audio import AudioCapture, list_input_devices
from rumble.dtmf_detector import DEFAULT_SAMPLE_RATE, ToneEvent


def _parse_device_arg(raw: str) -> int | str:
    """Accept either a numeric index or a substring of the device name."""
    return int(raw) if raw.isdigit() else raw


def _prompt_for_device() -> int | str:
    devices = list_input_devices()
    if not devices:
        print("No audio input devices detected.", file=sys.stderr)
        sys.exit(1)
    print("Available input devices:")
    for d in devices:
        print(
            f"  [{d['index']:>2}] {d['name']}  "
            f"({d['channels']} ch @ {d['sample_rate']:.0f} Hz)"
        )
    choice = input("Pick a device by index (or substring of name): ").strip()
    return _parse_device_arg(choice)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Listen on an audio input and print every detected DTMF tone."
    )
    parser.add_argument(
        "--device",
        help="Input device index, or a substring of the device name.",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help=f"Sample rate in Hz (default: {DEFAULT_SAMPLE_RATE}).",
    )
    args = parser.parse_args()

    device: int | str | None
    if args.device is not None:
        device = _parse_device_arg(args.device)
    else:
        device = _prompt_for_device()

    def on_tone(event: ToneEvent) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"{ts}  {event.kind:<5}  {event.char!r}", flush=True)

    print(f"Listening on device {device!r} @ {args.sample_rate} Hz. Ctrl-C to quit.")
    with AudioCapture(
        device=device, sample_rate=args.sample_rate, on_tone=on_tone
    ) as cap:
        cap.start()
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
