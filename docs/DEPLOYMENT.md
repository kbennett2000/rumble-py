# Deployment: Running rumble-py as a Real Node

You've gotten rumble-py to talk to a Mumble server from your laptop.
Now you want it on a small computer in the corner of your shack,
running 24/7, restarting itself on failure, available to whoever needs
it. This doc is the path from "works on my laptop" to "it's a node."

## Choosing a host

| Host | Verdict |
|---|---|
| **Raspberry Pi 4 or 5** | The sweet spot. Cheap, low power (~5 W), quiet, plenty of CPU for one node, native USB for the radio interface. **This is what we recommend.** |
| **Old laptop** | Fine. Lots of operators use the spare Thinkpad on the shelf. Battery acts as a free UPS. Downsides: noisier than a Pi, more power. |
| **Intel NUC / similar SFF PC** | Overkill but excellent. If you've got one lying around, use it. |
| **VPS** | **Don't.** Your radio interface won't be there. Mumble *server* is a great fit for a VPS, but the rumble-py node has to be physically near the radio. |
| **Repurposed router / OpenWrt box** | Tempting but no. Limited RAM, no Python ecosystem, the audio device support is unreliable. |

Throughout this doc we assume **Raspberry Pi 4 with the SignaLink USB
interface** because that's what most operators will end up with. The
steps for any other Linux host are the same after the apt commands;
adjust as needed.

## Initial setup on a Raspberry Pi

### 1. Flash the OS

Use the official [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
to flash **Raspberry Pi OS Lite (64-bit)** onto an SD card. We want
"Lite" — no desktop, no GUI. A node doesn't need pixels.

Before flashing, click the gear icon in the Imager to pre-configure:

- **Hostname:** `rumble-node` (or anything memorable).
- **Username + password:** non-default, please.
- **Enable SSH:** yes, with public key if you have one.
- **WiFi credentials:** if the Pi will be on WiFi (wired ethernet is
  preferable for a node, but WiFi works).
- **Locale and timezone:** your local timezone — important for log
  timestamps and the ident scheduler later.

Insert the SD card and boot the Pi. SSH in. If `rumble-node.local`
doesn't resolve, find the Pi's IP from your router's DHCP table.

### 2. Install system dependencies

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip git \
                    espeak-ng portaudio19-dev
```

Verify:

```bash
python3.11 --version          # should be 3.11.x or newer
espeak-ng "test"              # should hear something on default audio
arecord -l                    # should list the SignaLink as a USB audio device
```

If `arecord -l` doesn't see the SignaLink, replug the USB cable and
check `lsusb` for a Burr-Brown / Texas Instruments PCM-series chipset.

### 3. Clone the repo

The project lives in `/opt/rumble-py` by convention. Pick whatever path
you like; just keep the systemd unit's `WorkingDirectory` in sync.

```bash
sudo mkdir -p /opt
sudo chown $USER:$USER /opt
cd /opt
git clone https://github.com/kbennett2000/rumble-py.git
cd rumble-py
```

### 4. Create a venv and install rumble-py

We install the package (no `[dev]` extras on a production node — saves
some pip download time):

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The `-e` (editable) install makes `git pull` updates take effect on the
next service restart, without re-running pip.

### 5. Create a production config

```bash
cp config.example.yaml config.yaml
$EDITOR config.yaml
```

At minimum, set:

- `callsign:` to yours.
- `audio.input_device:` to a substring of your SignaLink (e.g.
  `"SignaLink"` or `"USB Audio Codec"`).
- One real `servers:` entry pointing at the Mumble server you're using.
- One `channels:` mapping for the basic `001/1` → root combination.

Test-run it once before installing the service:

```bash
python -m rumble --config config.yaml
```

You should see:

```
... rumble.mumble_client: state: DISCONNECTED -> CONNECTING
... rumble.mumble_client: connected to mumble.example.org:64738 as 'AE9S'
... rumble.mumble_client: state: CONNECTING -> CONNECTED
... rumble.commands: audio capture started (device='SignaLink', sample_rate=8000 Hz)
... rumble.commands: web UI listening on http://127.0.0.1:8080/
... rumble.commands: dispatcher started; bank=0 callsign='AE9S'
```

`Ctrl-C` to stop. If anything is off, fix it here before turning it
into a service.

## Installing as a systemd service

A service starts on boot, restarts on crash, and logs cleanly to the
journal. Create the unit file at `/etc/systemd/system/rumble.service`:

```ini
[Unit]
Description=rumble-py — DTMF-controlled Mumble linking node
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple

# Run as the same non-root user that owns /opt/rumble-py.
# Don't run as root unless you have a very specific reason.
User=pi
Group=pi

# Working directory must contain the config file path used below.
WorkingDirectory=/opt/rumble-py

# Run via the venv's Python interpreter.
ExecStart=/opt/rumble-py/.venv/bin/python -m rumble --config /opt/rumble-py/config.yaml

# Restart on failure (e.g. a network blip the wrapper can't recover from).
# rumble-py handles its own Mumble reconnect; this catches outright crashes.
Restart=on-failure
RestartSec=5s

# Send SIGTERM and give it time to disconnect cleanly.
KillMode=mixed
TimeoutStopSec=10

# Hardening — limit blast radius if something goes wrong. Optional but
# recommended for a long-running daemon with network access.
ProtectSystem=full
ProtectHome=read-only
NoNewPrivileges=true
PrivateTmp=true

# Logs go to the journal by default; capture stdout + stderr.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Adjust `User=` / `Group=` / `WorkingDirectory=` to match your install.

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl start rumble
sudo systemctl status rumble       # should be "active (running)"
```

If status shows failure, jump to logs immediately:

```bash
sudo journalctl -u rumble -e
```

## Log rotation

systemd's journal handles log rotation automatically — by default it
caps the on-disk journal at 10% of the filesystem or 4 GB, whichever
is smaller. For a node that runs `INFO`-level logs, you'll go years
without hitting that limit.

To inspect the rotated history, all the standard `journalctl` flags
work:

```bash
journalctl -u rumble -e            # most recent
journalctl -u rumble --since today
journalctl -u rumble --since "2 hours ago"
journalctl -u rumble -f            # live tail
journalctl -u rumble -p warning    # warnings and above only
```

If you want the logs in a flat file too (for scp'ing somewhere, or for
your club's archives), add a logrotate config or pipe `journalctl -u
rumble -f` into a tee. For most nodes the journal alone is enough.

## Autostart on boot

```bash
sudo systemctl enable rumble
```

That symlinks the unit into `multi-user.target.wants/`. Reboot the Pi
and confirm the service comes up automatically:

```bash
sudo reboot
# wait, SSH back in
sudo systemctl status rumble
```

Should be active. If it isn't, check `journalctl -u rumble -b` to see
what happened during this boot.

## Updating

When you want to pull in upstream changes:

```bash
cd /opt/rumble-py
sudo systemctl stop rumble
git pull
.venv/bin/pip install -e .         # only if pyproject.toml changed
sudo systemctl start rumble
sudo systemctl status rumble
```

You don't have to stop the service for **config-only** changes — use
the web UI's *Reload config* button, or `#N*` to switch banks live.
Restarting is only required when:

- The code changed (`git pull` brought new files).
- The audio device or web bind address changed.
- The Python venv was updated.

## Network considerations

### Outbound

The rumble-py node needs to reach your Mumble server on **TCP and UDP
port 64738** (or whatever you configured). For a typical home setup,
outbound traffic from your LAN is unrestricted; no firewall changes
needed on the node end.

If you're behind a corporate firewall, talk to whoever owns it. UDP is
not optional — Mumble degrades gracefully to TCP-only if UDP is
blocked, but voice quality suffers (jitter goes up, packets are
slightly bigger).

### Inbound (if you're also hosting Mumble at home)

If the Mumble server is on the same LAN as the rumble-py node, you've
got two choices:

1. **Local-only:** point `host:` in the config at the LAN IP (e.g.
   `192.168.1.50`). No port forwarding needed.
2. **Public Mumble server, hosted at home:** port-forward TCP and UDP
   64738 from your router to the Mumble server's LAN IP. Then point
   `host:` at your public hostname or IP. See
   [Concepts: hosting your own Mumble server](CONCEPTS.md#hosting-your-own-mumble-server)
   for the tradeoffs.

### Web UI

The web UI binds to `127.0.0.1:8080` by default. If you want to reach it
from a phone on the same WiFi, change `web.host: 0.0.0.0` — but **only
on a trusted LAN**, since there's no auth. See
[web security caveat](../README.md#the-web-ui).

If you change `web.host` you'll need to restart the service:

```bash
sudo systemctl restart rumble
```

## Monitoring

### Day-to-day

```bash
journalctl -u rumble -f
```

That's the live log tail. New connections, channel changes, ident
events (once milestone 7 lands), and errors all show up here.

For a glanceable status: the web UI at `http://<node-ip>:8080/` shows
connection state, current channel, log tail, and lets you exercise
commands without keying the radio. Bookmark it on your phone.

### Long-term health

Check on the node every few weeks. SD cards on Raspberry Pi nodes have
a finite write-cycle life — modern ones (Samsung Evo, SanDisk Industrial,
Sandisk High Endurance) routinely run for years, but they do
eventually fail.

Things to glance at:

- `df -h` — is the SD card filling up? journald should self-cap at 4 GB
  but doublecheck.
- `dmesg --level=err,warn` — any USB disconnects, sound device drops,
  or SD card errors?
- `journalctl -u rumble --since "7 days ago" | grep -i error` —
  any unexpected errors in the past week?
- The Mumble server's own logs — anyone disconnecting unexpectedly,
  any cert renewal issues?

For a quieter watch, a basic uptime monitor (UptimeRobot, healthchecks.io)
pinging your node's web UI every 5 minutes will text you when it goes
down.

## Security

The trust model for a rumble-py node:

- **Anyone with the DTMF codes** and access to your radio's frequency
  can drive your node. They can switch channels, change banks, mute,
  disconnect. Treat the DTMF code list like a key to the shack.
- **Anyone on the LAN** with access to the web UI port can do all the
  same things, plus reload the config file. Trust your LAN.
- **The public internet** should never see the web UI. There is no
  authentication, and even the CSRF posture isn't bulletproof yet
  (see [issues-to-file.md](issues-to-file.md)).

Practical hardening for a public-facing node:

- Run the service as a dedicated non-root user (the systemd unit
  above does this — `User=pi` should be replaced with `User=rumble`
  and a matching user created with `sudo useradd -r rumble`).
- Keep `web.host: 127.0.0.1`. If you must access the web UI
  remotely, SSH-tunnel:

  ```bash
  ssh -L 8080:127.0.0.1:8080 pi@rumble-node.local
  ```

  Then open `http://localhost:8080/` on your laptop. SSH handles
  encryption and auth; the UI never touches the network in clear.
- Set a server password on your Mumble server. Stops casual
  unauthorized clients.
- Keep your Pi's OS patched: `sudo apt update && sudo apt upgrade`
  monthly is plenty.

## Multi-node setups

Nothing about rumble-py prevents multiple nodes — quite the opposite,
that's the whole point. The typical multi-node arrangement:

```
   ┌─────────────────────┐
   │  Mumble server      │
   │  (one place)        │
   │   Root              │
   │   ├── Lobby         │
   │   ├── 2m            │
   │   ├── 70cm          │
   │   └── Nets          │
   └──────┬──────────────┘
          │
          ├── node 1: rumble-py + 2m radio, callsign W0XYZ
          ├── node 2: rumble-py + 70cm radio, callsign W0XYZ-1
          └── node 3: rumble-py + UHF radio, callsign K9TST
```

Each node:

- Runs its own copy of rumble-py, with its own `config.yaml` and its
  own callsign.
- Connects to the same Mumble server.
- Joins whichever channel is appropriate for its radio's purpose.

Use cases:

- **Club node + repeater link:** one node sits on the club's repeater
  output, one node sits at the dispatcher's shack, both connect to
  the same Mumble channel.
- **Multi-band linking:** 2m and 70cm nodes in the same channel —
  hams on either band hear each other.
- **Emergency comms staging:** all club members' shack nodes join a
  designated channel when an activation happens.

Each node is independent. They share nothing except the Mumble channel
they happen to be in, and updates to one node don't affect the others.
That's deliberately how the architecture works — robust against
single-node failures, easy to reason about.

See [`CONCEPTS.md`](CONCEPTS.md) for the bigger picture on channel
structure, and [`CONFIGURATION.md`](CONFIGURATION.md) for the
per-node config.

---

If you've got the Pi running on a UPS, the radio on a real antenna,
and the Mumble server somewhere reachable — you've got a real node.
Welcome to the air.
