#!/usr/bin/env python3
# End-to-end smoke test against a Mumble server.
#
# Connects, joins a channel, transmits 2 seconds of a 440 Hz sine, then
# listens for incoming audio for 10 seconds. Intended to be run while
# connected to the same channel from the Mumble desktop client so you can
# verify the protocol path manually.
#
# Requires the dev Mumble server (cd docker && docker compose up -d) or any
# other reachable Mumble server.

from __future__ import annotations

import argparse
import logging
import sys
import time

# Make `tests/_mumble_helpers` importable without installing it.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from rumble.mumble_client import (  # noqa: E402
    DEFAULT_MUMBLE_PORT,
    ConnectionState,
    MumbleAudioFrame,
    MumbleClient,
)
from tests._mumble_helpers import synth_pcm  # noqa: E402

LISTEN_SECONDS = 10.0
TONE_DURATION_S = 2.0
TONE_FREQ_HZ = 440.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mumble end-to-end smoke test (connect, send sine, listen)."
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=DEFAULT_MUMBLE_PORT)
    parser.add_argument("--username", default="rumble-smoke")
    parser.add_argument("--channel", default="Root")
    parser.add_argument("--no-send", action="store_true", help="Skip sending audio.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    received_count = 0
    received_bytes = 0

    def on_audio(frame: MumbleAudioFrame) -> None:
        nonlocal received_count, received_bytes
        received_count += 1
        received_bytes += len(frame.pcm)
        # One line per frame would be a lot — print one per second by hand.
        if received_count == 1 or received_count % 50 == 0:
            print(
                f"  ← {len(frame.pcm):4d} bytes from {frame.user_name!r} "
                f"(frame #{received_count})"
            )

    def on_state(state: ConnectionState) -> None:
        print(f"  state -> {state.name}")

    def on_user_joined(name: str) -> None:
        print(f"  user joined: {name!r}")

    def on_user_left(name: str) -> None:
        print(f"  user left:   {name!r}")

    client = MumbleClient(
        host=args.host, port=args.port, username=args.username, reconnect=False
    )
    client.on_audio_received(on_audio)
    client.on_state_changed(on_state)
    client.on_user_joined(on_user_joined)
    client.on_user_left(on_user_left)

    print(f"Connecting to {args.host}:{args.port} as {args.username!r}...")
    client.connect(timeout=10.0)
    try:
        if args.channel and args.channel != "Root":
            print(f"Moving to channel {args.channel!r}...")
            client.move_to_channel(args.channel)
            # Give pymumble a moment to land the move.
            time.sleep(0.5)

        print(f"In channel: {client.current_channel!r}")
        print(f"Other users in channel: {client.users_in_current_channel}")

        if not args.no_send:
            pcm = synth_pcm(duration_s=TONE_DURATION_S, freq=TONE_FREQ_HZ)
            print(
                f"Sending {TONE_DURATION_S:.1f}s of {TONE_FREQ_HZ:.0f} Hz sine "
                f"({len(pcm)} bytes PCM)..."
            )
            client.send_audio(pcm)
            # The sound_output thread drains the buffer over real time, so
            # give it the full tone duration plus a small fudge.
            time.sleep(TONE_DURATION_S + 0.5)

        print(f"Listening for incoming audio for {LISTEN_SECONDS:.0f}s...")
        time.sleep(LISTEN_SECONDS)

        print(
            f"Received {received_count} audio frame(s) totaling "
            f"{received_bytes} bytes."
        )
    finally:
        print("Disconnecting...")
        client.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
