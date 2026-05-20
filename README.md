# rumble-py

DTMF-controlled Mumble client for linking analog amateur radios over the internet.

A Python rewrite of an older C# Windows-only tool, now cross-platform and using
the Mumble protocol directly via [pymumble](https://github.com/azlux/pymumble)
(no need to run the Mumble desktop client).

> **Status:** alpha. Scaffold only — application logic not yet implemented.

## Quick start

```bash
# 1. Create a virtual environment and install the package in editable mode
#    with development dependencies.
python3.11 -m venv .venv
source .venv/bin/activate            # Linux/macOS
# .venv\Scripts\activate             # Windows PowerShell
pip install -e ".[dev]"

# 2. Bring up the local dev Mumble server (requires Docker).
cd docker && docker compose up -d && cd ..

# 3. Run the package (right now this just prints a version banner).
python -m rumble

# 4. Run the tests.
pytest

# 5. Verify the DTMF detector against a real radio. Connect the radio's
#    receive audio to an input device (line-in, USB sound card, etc.) and:
python scripts/listen_for_dtmf.py
#    Then key tones into the radio — each detected tone prints a timestamped
#    start/stop line. Pass --device <index-or-name-substring> to skip the
#    interactive picker.

# 6. End-to-end Mumble smoke test (requires the dev server from step 2):
python scripts/mumble_smoke.py
#    Connects, joins Root, transmits 2s of a 440 Hz sine, listens for 10s.
#    Run a Mumble desktop client into the same channel to hear the tone and
#    to talk back; the script will print every incoming audio frame.

# 7. Run the full app. Copy the example config and edit for your setup:
cp config.example.yaml config.dev.yaml
$EDITOR config.dev.yaml
python -m rumble --config config.dev.yaml
#    This connects to the configured Mumble server, opens an audio input,
#    and starts listening for DTMF. Ctrl-C to shut down.
```

## Web UI

While the dispatcher is running, a small web interface is available at
[http://127.0.0.1:8080/](http://127.0.0.1:8080/) by default. It shows live
state (connection, current channel, users, current bank, DTMF buffer), lets
you exercise the dispatcher without a radio via an on-screen DTMF keypad,
and tails the log in real time over Server-Sent Events. It's intended for
status checks and light remote control — not as a fully-featured admin
console.

To disable it, set `web.enabled: false` in your config.

To expose it on the local network, set `web.host: 0.0.0.0` and pick a port —
**but note that there's no authentication**. Only do this on a trusted LAN
and consider a reverse proxy with basic auth in front if you need wider
access.

For a quick browser test without the audio pipeline:

```bash
python scripts/web_smoke.py --config config.dev.yaml
# → Web UI at http://127.0.0.1:8080/
```

<!-- TODO: screenshot — replace this comment with an image of the UI -->

## License

MIT — see [LICENSE](LICENSE).
