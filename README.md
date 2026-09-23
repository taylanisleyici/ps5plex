# ps5plex

Stream your Real-Debrid torrents to a PS5 (or any Plex client) through a Plex
server running in Docker on your own computer.

```
Real-Debrid --> rdserve (HTTP) --> rclone mount --> librarian --> Plex --> PS5
```

- Add a title to your **Plex Watchlist**, then choose a release for it in the
  **source picker**, a small web page on your LAN. Cached releases play right away.
- The **librarian** turns release names into a clean Plex library (`Movie (Year)/`,
  `Show/Season 01/`) out of symlinks. Nothing is downloaded to disk. Files stream
  from Real-Debrid when you press play.
- Your library holds only what you picked. It doesn't mirror your whole
  Real-Debrid account.
- Subtitles are fetched from OpenSubtitles and saved as `.srt` files next to each
  video. They're ranked by how well they fit the release you picked.
- No Plex Pass needed. Plex treats the PS5 as a local client, even behind
  Docker's NAT.

Nothing runs in the background. Start it when you want to watch something, and
press `Ctrl+C` when you're done.

## Requirements

- Docker with Compose. Docker Desktop on macOS, or Docker Engine on Linux.
- A [Real-Debrid](https://real-debrid.com) account.
- A free [Plex](https://plex.tv) account.
- A Stremio scraper addon URL: [Comet](https://github.com/g0ldyy/comet) or
  MediaFusion, set up with your Real-Debrid account. Torrentio sits behind
  Cloudflare and blocks non-browser clients, so don't use it here.
- The computer and the PS5 on the same network. Wired is better: every byte
  streams through this computer.

## Install

### 1. Clone and configure

```sh
git clone https://github.com/taylanisleyici/ps5plex.git
cd ps5plex
cp .env.example .env
```

Fill in `.env`:

| Variable | Value |
|---|---|
| `RD_TOKEN` | https://real-debrid.com/apitoken |
| `TZ` | Your timezone, e.g. `Europe/Berlin` |
| `PLEX_ADVERTISE_URL` | `http://<your-lan-ip>:32400`. Find the IP with `ipconfig getifaddr en0` (macOS) or `hostname -I` (Linux). A DHCP reservation on your router keeps it stable. |
| `SCRAPER_URL` | In Stremio: Addons → your scraper → copy the install URL, **without** the trailing `/manifest.json`. |

Leave `PLEX_CLAIM` and `PLEX_TOKEN` empty for now.

### 2. Claim the Plex server and start it

Get a claim token from https://plex.tv/claim. It **expires after 4 minutes**, so
paste it into `PLEX_CLAIM` and start the stack right away:

```sh
docker compose up
```

On macOS you can also double-click `ps5plex.command` in Finder.

The first start builds the images and takes a few minutes. Later starts take
seconds.

### 3. Add the libraries

Open http://localhost:32400/web and sign in. Network and analysis settings are
already applied. Add two libraries:

| Library | Type | Folder |
|---|---|---|
| Movies | Movies | `/library/movies` |
| TV Shows | TV Shows | `/library/shows` |

### 4. Connect the picker to Plex

In a second terminal:

```sh
./scripts/plex-token.sh
```

This copies the server's token into `PLEX_TOKEN` in `.env`. Also clear
`PLEX_CLAIM`, since it's single-use. Then stop the stack with `Ctrl+C` and
run `docker compose up` again.

### 5. On the PS5

Install Plex from the PlayStation Store and sign in with the same account. In
the app's settings, set **Video Quality to Original**. A fixed bitrate makes
the console ask for a transcode.

## Using it

1. Add a movie or show to your Plex Watchlist, from the PS5 app, your phone or
   the web.
2. Open the picker at **http://\<your-lan-ip\>:8099** (or http://localhost:8099).
3. Choose a release. **⚡ plays now** means Real-Debrid already has it cached.
   Anything else has to download at Real-Debrid first.
4. After a few seconds it's in your Plex library. Play it on the PS5.

The picker shows each release's size, resolution, codec, audio languages and
estimated bitrate, with filters for all of them. Camera rips and foreign dubs
are hidden unless you ask for them. For shows, a **season pack** adds the whole
season at once. Picking another release for the same title replaces the first.

Removing a title from your Watchlist removes it from the library too. The
torrent stays in your Real-Debrid account.

### Subtitles

Each pick saves the best-matching SRT files for your languages, five per
language by default. Plex lists them in order as "English (SRT External)" and so
on, so if the first one is out of sync, try the next. The **subtitles** button on
a title shows the full ranking.

**On the PS5, pick the external SRT tracks.** Image-based subtitles embedded in
a release (PGS) force Plex into a full software re-encode, and playback usually
fails.

### Settings

The **settings** link (top right of the picker) sets subtitle languages and how
many to keep, the subtitle mode, the audio track (the original language or a
fixed one), maximum resolution, HDR preference, and whether cached releases sort
first. Saving also sets the default languages on the Plex server, so the PS5 app
uses them automatically.

Choices are stored in `settings.json` on the `ps5plex-state` Docker volume.
Tokens stay in `.env`.

### Automatic fetching

Set `AUTO_FETCH=1` in `.env` to add the top-ranked release for every Watchlist
title without using the picker. It's off by default because it adds torrents on
its own. `MAX_PER_TITLE` caps how many it adds per title.

## Troubleshooting

**The PS5 says "Currently Unavailable".** The PS5 Plex app sometimes rejects
the server's TLS certificate. The server log shows `tlsv1 alert unknown ca`.
Make Plex issue a new one:

```sh
docker compose stop plex
docker run --rm -v ps5plex_plex-config:/config alpine \
  rm -f "/config/Library/Application Support/Plex Media Server/Cache/certificate.p12"
docker compose up -d plex
```

Keep Plex's **Secure connections** setting on *Preferred*.

**Plex advertises an address the PS5 can't reach.** Check `PLEX_ADVERTISE_URL`
in `.env`. Your computer's LAN IP may have changed.

**The libraries stay empty.** Check that the mount is working:

```sh
docker compose exec plex ls /media
docker compose exec plex cat /config/rclone.log
```

An empty listing without errors usually just means nothing has been picked yet.

**`rdserve` never becomes healthy.** `RD_TOKEN` is missing, wrong or expired.
If Real-Debrid calls fail even with a valid token, run `./scripts/rd-check.sh`.
If some Real-Debrid hostnames answer and others don't, your network is filtering
them.

**Playback stalls or drops to a transcode.** Everything streams through your
computer, so its internet connection limits what plays smoothly. Compare the
bitrate shown in the picker with your bandwidth. Pick a smaller release, or use
Ethernet instead of Wi-Fi. Also give Docker enough CPU (Docker Desktop →
Settings → Resources). Transcoding runs in software only, so it needs the cores.

**A series shows up under Movies.** Some release names don't say whether
they're a movie or a series. Add a substring of the name to
`config/overrides.yml` with `series` or `movie`.

## What the PS5 plays without transcoding

The PS5 Plex app always streams over DASH, so Plex remuxes files on the fly. In
that remux:

| Stream | Result |
|---|---|
| H.264, HEVC (including 4K HDR10/Dolby Vision) | Copied as-is, almost no CPU |
| AAC, AC3, EAC3 audio | Copied |
| DTS, DTS-HD, TrueHD audio | Converted to AAC 5.1, cheap |
| PGS/image subtitles | Burned into the video, full re-encode. Avoid |

Dolby Vision likely plays as HDR10.

## How it works

| Piece | Role |
|---|---|
| `rdserve/` | Serves the torrents you picked as an HTTP directory tree |
| `docker/rclone-mount.sh` | Mounts that tree at `/media` in the Plex container. Streaming only, no disk cache |
| `librarian/` | Parses release names with guessit and builds the symlink library in `/library` |
| `watchlist/` | The picker web UI, and the optional auto-fetcher |
| `docker/plex-prefs.sh` | Sets Plex's network settings on first boot and turns off thumbnail and analysis jobs, which would read whole files from Real-Debrid |
| `docker/loopback-proxy.sh` | Relays port 32400 over loopback so Plex treats every client as local |
| `config/overrides.yml` | Movie/series overrides for names the parser can't read |

Tests (need `pip install guessit`): `python3 librarian/test_classify.py`,
`python3 librarian/test_rank.py`, `python3 watchlist/test_picker.py`.

## Friendly reminder

This is a heavily vibe-coded project. It works on my machine, and it should work on yours. If it doesn't, please open an issue and i will try to help.

## License

[GNU AGPL-3.0](LICENSE). You can use, change, share and even sell it, but any
copy or modified version you distribute or run as a network service must be
released under the same license, with full source.

Copyright (C) 2026 Taylan İşleyici
