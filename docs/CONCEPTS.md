# Concepts: Mumble, VoIP, and Linking for Hams

This is the elmer-at-dinner version of the VoIP plumbing that rumble-py
sits on top of. If you've got a license and you've worked a repeater but
you haven't run a SIP phone or a VOIP server in your life, this is for
you. You'll come out the other end able to set up a Mumble server, pick a
channel structure that makes sense for your group, and understand the
words on the wire.

If you already know what Mumble is, what a TLS cert means, and how a
self-hosted server differs from a public one — feel free to skip to
[A typical node topology](#a-typical-node-topology).

## Why rumble-py uses Mumble

A linking system needs three things: a low-latency audio codec, a way to
move audio between many endpoints, and some structure for *who hears
whom*. There are dozens of protocols that do this. We picked
[Mumble](https://www.mumble.info/) because:

- It's been around since **2005**. The protocol is stable, the codebase
  is mature, and the bugs have been found.
- It's **open source under the BSD license**. No company owns the
  protocol; no company can sunset it.
- It uses the **Opus codec**, which is the same codec WebRTC and a lot
  of modern VOIP runs on. Sounds great at 24-48 kbps and tolerates
  packet loss gracefully.
- It's designed for **low latency**, originally for online gaming —
  typical end-to-end audio delays on the public internet are 80-150 ms,
  well below the threshold where conversations get awkward.
- **Encryption is built in.** All client-to-server traffic is TLS;
  client-to-client (voice) traffic between users on the same server is
  encrypted via the server.
- The **server runs on anything.** Linux, Windows, FreeBSD, a Pi, an
  AWS micro instance, your neighbor's NAS.
- There's a **permissive license** on both the protocol docs and the
  reference implementations, so writing your own client — which is what
  we did, via [pymumble](https://github.com/azlux/pymumble) — is a real
  option.

Mumble was built for gamers. We're stealing it for hams. The fit turns
out to be excellent: low-latency push-to-talk audio, organized channels,
encrypted by default, your own server, no central registry.

## What is a Mumble server?

A **Mumble server** is a long-running process that listens on TCP and UDP
port **64738** by default. (Yes, both — TCP for the control channel and
fallback voice, UDP for normal voice.) When you start it, it just sits
there and waits.

You connect to it with a **Mumble client**: the desktop client at
[mumble.info](https://www.mumble.info/), a phone app, or — in our case —
rumble-py. The client opens a TLS connection, exchanges some
authentication, and gets a list of available **channels**. Operators on
the same channel can hear each other. Operators in different channels
cannot, even though they're on the same server.

That's it. That's the whole concept.

The reference Mumble server implementation is called **Murmur** (sometimes
just `mumble-server` in package names — `apt install mumble-server`
installs Murmur). Throughout these docs we use "Mumble server" to mean
"any process speaking the Mumble protocol on port 64738." If you're
googling for setup help, search terms with both *Murmur* and *Mumble
server* will land you in the right places.

> **Plain old Mumble desktop clients can also connect.** A buddy with a
> regular laptop and Mumble installed can pop in and listen to whatever
> your node is relaying. They don't need rumble-py. This is great for
> club Field Days where some folks have radios and some folks are stuck
> at home.

## Channels and channel paths

Mumble channels form a **tree**. The root channel exists on every server
and is named (by convention) `Root`. Channels can have sub-channels,
which can have sub-sub-channels, and so on. The full path of a channel
is a slash-separated walk from the root:

```
Root
├── Lobby
├── Repeaters
│   ├── 2m
│   │   ├── W0XYZ
│   │   └── N1ABC
│   └── 70cm
│       └── K9TST
└── Nets
    ├── Skywarn
    └── ARES
```

Some example channel paths from that tree:

- `Root` — the root channel itself
- `Root/Lobby` — the lobby
- `Root/Repeaters/2m/W0XYZ` — fictional repeater W0XYZ's 2m frequency

In rumble-py's config you write `channel_path: "Root/Repeaters/2m/W0XYZ"`
and the client walks the tree to find it. (The leading `Root/` is
optional in our config; we strip it for you.)

The reason for the tree structure is that **operators in different
channels don't hear each other.** This is how you let multiple groups
share one Mumble server without stepping on each other. A Skywarn net can
be running in `Root/Nets/Skywarn` while rag-chew is happening in
`Root/Lobby` and a 2m repeater link is live in `Root/Repeaters/2m/W0XYZ`,
and none of those three groups is annoying any other.

For rumble-py, channels are how you organize **destinations**. The DTMF
command `#001#1*` looks up server `001`, channel `1` in your active
bank's channel map, and that resolves to a Mumble channel path. Operators
just type `#001#1*` on the keypad; they don't have to remember
`Root/Repeaters/2m/W0XYZ`.

## Hosting your own Mumble server

You have three reasonable options. Pick based on who else needs to reach
the server and how much fiddling you're willing to do.

### Option 1 — VPS ($3-5/month)

The closest thing to a no-brainer. Pick a small VPS from any major
provider (Hetzner, OVH, DigitalOcean, Linode, Vultr — all fine), install
Mumble server with `apt install mumble-server`, open ports 64738 TCP and
UDP in the provider's firewall, and you're done. Public IP, you don't
think about port forwarding, latency is generally good because data
centers have nice peering.

- **Pros:** static public IP, professional uptime, simple firewall.
- **Cons:** monthly cost, and you have to be slightly more careful about
  security since the server is on the public internet (set a password,
  consider Let's Encrypt for the TLS cert).

### Option 2 — Raspberry Pi behind your home router

Free if you already own the Pi. Install Mumble server the same way, then
forward TCP and UDP port 64738 from your router to the Pi's LAN IP.
Either get a static IP from your ISP (sometimes possible, often not), or
use a dynamic DNS service like [duckdns.org](https://www.duckdns.org/) or
[no-ip.com](https://www.noip.com/) so people can reach you at a
hostname even when your residential IP changes.

- **Pros:** free, full control, lives on your shack network.
- **Cons:** depends on your home internet being up, requires port-
  forwarding, dynamic DNS adds a moving part.

### Option 3 — Use a public Mumble server

There are public Mumble servers run by hams, clubs, and Mumble
enthusiasts. They typically have rules about what kind of audio is
appropriate. If you find one that welcomes amateur radio traffic, this
is the easiest path: skip running a server entirely.

- **Pros:** zero setup, no cost.
- **Cons:** you don't control the channel tree, the server can go away
  or change rules, you're a guest on someone else's infrastructure.

For development, the `docker/docker-compose.yml` in this repo starts a
local Mumble server with one command — that's option zero, and it's how
the development tests run. See [the README quick start](../README.md#quick-start).

<details>
<summary>Click for a minimal annotated Murmur config (for VPS or Pi)</summary>

Murmur's config is `/etc/mumble/mumble-server.ini` on Debian/Ubuntu. The
defaults that ship with the package are sane; the only fields most
operators ever touch:

```ini
# Welcome banner sent on connect (HTML allowed).
welcometext="<br /><b>W0XYZ Linking Server</b><br />"

# Listen address. 0.0.0.0 = all interfaces. Default port is 64738.
host=0.0.0.0
port=64738

# A server-wide password if you want to keep the riff-raff out.
# Leave empty for an open server.
serverpassword=

# Maximum simultaneous users.
users=100

# Maximum bitrate per user (bits/sec). 72000 is the Mumble default and
# is plenty for voice; higher rates eat upload bandwidth.
bandwidth=72000

# Set this on first start; "register" the SuperUser to manage permissions.
# See https://wiki.mumble.info/wiki/Murmur_Guide
serveradmin=AE9S
```

After editing, restart with `sudo systemctl restart mumble-server`. The
log shows up in `/var/log/mumble-server/mumble-server.log`.

</details>

## Authentication and certs

Mumble's authentication model is a little unusual; it's worth
understanding before you scratch your head at the cert warning the first
time you connect.

**Every Mumble client has its own certificate.** Not the server — the
client. When the desktop Mumble client is installed, it generates a
self-signed certificate the first time it runs. That cert is the client's
identity from then on. When you connect to a server, the cert's
public-key fingerprint is what the server records to identify you across
sessions.

**The server has a cert too,** typically self-signed by default. The
client's first connection to a new server prompts you to accept that
cert. Once accepted, the client trusts it forever (until it changes).

This is fine for closed groups — your club, your friends, your own nodes.
The trust model is "trust on first use" (TOFU), the same as SSH host
keys. For a public-facing node where strangers will connect, you might
prefer a proper Let's Encrypt cert, which Murmur supports via the
`sslCert` and `sslKey` config options.

For rumble-py: we currently don't pin server certs. The client accepts
whatever the server presents. That's intentional for now — every existing
ham use of Mumble is closed-group TOFU. If you need stricter cert
verification, that's a future feature; for the moment, point
`certfile`/`keyfile` at a client cert if your server requires one for
identity, but you'll still TOFU the server.

> **The first time someone connects to your Mumble server with the
> desktop client, they will see a cert warning.** Tell them to click
> "Accept permanently." This is normal. It will not happen again.

## Comparing to IRLP, EchoLink, AllStar Link, Hamshack Hotline

There's not a single best linking system — it depends on what you're
trying to do, who you want to talk to, and how much you want to host
yourself. This table tries to be fair.

| | rumble-py | IRLP | EchoLink | AllStar Link | Hamshack Hotline |
|---|---|---|---|---|---|
| **Protocol** | Mumble (open standard, BSD) | IRLP-proprietary | EchoLink-proprietary | IAX2 / Asterisk (open) | SIP (open standard) |
| **Open source** | Yes, all layers | Client open, server proprietary | Mostly closed | Yes | Yes |
| **Audio codec** | Opus (24-48 kbps) | GSM 13 kbps | GSM 13 kbps | Various Asterisk codecs | G.711/G.722 |
| **Latency** | 80-150 ms | 100-200 ms | 150-300 ms | 80-200 ms | 50-150 ms |
| **Directory** | None — operator chooses server | Centralized, IRLP-managed | Centralized, EchoLink-managed | Centralized AllStar Link | None — direct dial |
| **Hardware floor** | Pi 4 (~$45) | Dedicated IRLP node board | Sound card + Windows | Pi or PC | Cisco / Yealink phone, ~$30 used |
| **Self-hosted hub** | Yes (Mumble server) | No | No | Yes (private nets possible) | No (Asterisk possible but unusual) |
| **Mental model** | "Connect a radio to a chat room" | "Link two nodes by number" | "Dial a callsign" | "Trunk two repeaters" | "Phone call between hams" |
| **Best for** | Linking, nets, rag-chew, club nodes | Linking established nodes | Casual contacts via PC | Linking, nets, complex routing | Hammy phone calls |

A few comments on the table:

- **IRLP** is rock-solid and has been the gold standard for repeater
  linking for two decades. The trade-off is that you need an IRLP
  hardware node (the board is around $200), and the directory is run
  by the IRLP project. If you want a no-fiddle node and don't mind
  the hardware purchase, IRLP is great.
- **EchoLink** is the easiest one to *use* (any Windows PC or phone can
  be an endpoint), but the most centralized one architecturally. It's
  also closed-source, so you can't run your own EchoLink hub.
- **AllStar Link** is the most powerful of the linking systems for
  complex setups. It's also the most complex to operate — full Asterisk
  underneath. If you want fine-grained dial-plan style routing of
  multiple repeaters and nets, AllStar is unmatched. The learning
  curve is real.
- **Hamshack Hotline** is a different beast: it's voice-over-IP between
  hams over standard SIP phones (Cisco IP phones from eBay). Great for
  casual chats; not a linking system in the IRLP sense.
- **rumble-py** is positioned as: "I want IRLP, but on hardware I
  already own, with no proprietary dependencies." If that matches what
  you want, it's the right tool. If you want polished hardware and a
  managed directory, IRLP is genuinely better.

## A typical node topology

What does it actually look like running?

```
   shack A                                           shack B
   ┌─────────────┐                                   ┌─────────────┐
   │  Operator   │                                   │  Operator   │
   │  (radio)    │                                   │  (radio)    │
   └──────┬──────┘                                   └──────┬──────┘
          │ RF                                              │ RF
          ▼                                                 ▼
   ┌─────────────┐                                   ┌─────────────┐
   │  Radio +    │                                   │  Radio +    │
   │  interface  │                                   │  interface  │
   └──────┬──────┘                                   └──────┬──────┘
          │ audio over USB                                  │
          ▼                                                 ▼
   ┌─────────────┐                                   ┌─────────────┐
   │  rumble-py  │  ◄── DTMF parsed locally          │  rumble-py  │
   │  node       │      (commands never leave shack) │  node       │
   └──────┬──────┘                                   └──────┬──────┘
          │           ↘                                     │
          │ TLS / TCP+UDP 64738   ↘ Opus audio over TLS    │
          └─────────────────────►◄┴◄─────────────────────────┘
                                ▼
                       ┌──────────────────┐
                       │  Mumble server   │
                       │  (VPS / Pi / public)
                       └──────────────────┘
```

Audio path: radio → interface → sound card → rumble-py → Mumble server →
the other rumble-py node → its sound card → its interface → its radio.
At every hop the audio is digital except for the first and last RF legs.

**Where the audio is encoded (Opus):** between rumble-py and the Mumble
server. Mumble decodes incoming Opus to PCM before delivering it to
rumble-py via the SOUNDRECEIVED callback.

**Where TLS lives:** between every rumble-py instance and the Mumble
server. Operator-to-operator traffic on the same server is encrypted
because each leg of the conversation is TLS to the server, which is
trusted to relay.

**Where DTMF happens:** entirely on the operator's local node. The tones
never make it to the Mumble server — by the time the audio leaves the
node, it's voice only (or, today during development, nothing — see the
[project status](../README.md#project-status)). Each operator commands
their own node and only their own node.

## Latency, bandwidth, and audio quality

Rough numbers, to set expectations:

- **Codec bitrate.** Mumble's Opus runs 24-48 kbps per active speaker.
  A node that's transmitting uses up to ~48 kbps upload. A node that's
  receiving uses ~48 kbps download per other operator currently
  talking. For typical voice nets with one operator at a time, you're
  looking at under 100 kbps on either side.
- **End-to-end audio latency.** 80-150 ms is typical on a healthy
  internet path. Cross-continent paths can hit 250 ms. For comparison:
  a local repeater adds about 50 ms of digital processing on a modern
  repeater, and an IRLP-linked path adds 100-300 ms of internet
  transport. Rumble-py is in the same ballpark.
- **Jitter buffer.** Mumble's jitter buffer is dynamic, typically 20-60
  ms. If you're on a flaky LTE connection you'll hear it stretching;
  on wired ethernet it's invisible.
- **Sample rate.** Mumble runs internally at **48 kHz mono** for audio.
  Rumble-py's DTMF detection runs at **8 kHz mono** — narrowband is
  plenty for tone detection and matches a typical narrowband FM
  receiver's bandwidth. TTS gets resampled from whatever pyttsx3
  produces (usually 22050 Hz) up to 48 kHz before going on the wire.

Bandwidth-wise, this is light. A Raspberry Pi 4 on home broadband is
massive overkill for a single rumble-py node. The bottleneck is almost
never bandwidth; it's audio levels, PTT timing, and the operator at the
other end of the link.

## What rumble-py does NOT do

Worth being explicit. These are intentional non-goals, or "later."

- **Not a directory service.** There is no rumble.org listing the
  active nodes. You connect to a specific Mumble server because you
  configured rumble-py to.
- **Not a node registry.** Nobody knows you exist except the Mumble
  server you connect to.
- **Not authentication for the radio side.** Anyone with the DTMF codes
  and access to your radio frequency can drive your node. You are
  trusting your operators. If that's not OK for your group, talk to
  them about a passphrase, or change DTMF codes after compromises.
- **Not a radio-band-plan enforcer.** Rumble-py doesn't know what
  frequency your radio is on, and doesn't care. If you transmit your
  Mumble audio out of band, that's between you and your license.
- **Not a logger** (yet). It logs operationally — connect/disconnect,
  commands received, errors — but it doesn't log audio or QSO
  metadata. That's a future feature.
- **Not a phone patch.** There's no PSTN / SIP bridge. If you want
  ham-to-phone, look at AllStar Link or Hamshack Hotline.
- **Not a repeater controller.** It can sit on the back of a repeater
  and act as a link, but it doesn't time out, lock out, courtesy-tone,
  or any of the things a real repeater controller does. Use a real
  controller (Arcom RC210, NHRC, S-COM 7330, etc.) for repeater
  duties; use rumble-py for the linking side.

## Glossary

| Term | Meaning |
|---|---|
| **Mumble** | An open-source VOIP protocol and ecosystem. The thing rumble-py talks to. |
| **Murmur** | The reference server implementation of Mumble. On Debian / Ubuntu the package is `mumble-server`. |
| **Channel** | A named room on a Mumble server. Operators in the same channel hear each other. |
| **Channel path** | The slash-separated full name of a channel from Root, e.g. `Root/Repeaters/W0XYZ`. |
| **Opus** | The audio codec Mumble uses. Open-source, low-latency, voice and music both. |
| **Ice** | A separate optional control interface for Mumble servers (think SOAP for Murmur). Rumble-py doesn't use it. |
| **Certificate** | A small file containing a public key + identity, used to authenticate clients and servers in Mumble's TLS. |
| **TOFU** | "Trust on first use." The cert-acceptance model Mumble uses by default. Same idea as SSH host keys. |
| **Port forwarding** | A router configuration that lets external connections reach a specific machine on your LAN. Required if you self-host Mumble at home. |
| **TLS** | Transport Layer Security. The encryption layer Mumble uses for control and (server-to-server-to-client) audio. |
| **DTMF** | Dual-Tone Multi-Frequency. The touch-tone keypad system. Two simultaneous sine waves per key. |
| **Bank** | A rumble-py concept: a named set of servers + channel mappings. Operators switch banks live with `#N*`. |
| **Channel mapping** | A `(server_number, channel_number)` → channel path entry in a bank, e.g. `001/1 → Root/Lobby`. |
| **Sticky mute** | A rumble-py mute that persists across DTMF commands. Set with `#00#0*`, cleared with `#00#1*`. |

See also: [`HARDWARE.md`](HARDWARE.md) for the radio side,
[`CONFIGURATION.md`](CONFIGURATION.md) for the rumble-py config format,
[`DEPLOYMENT.md`](DEPLOYMENT.md) for running a node 24/7.
