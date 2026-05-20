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

## License

MIT — see [LICENSE](LICENSE).
