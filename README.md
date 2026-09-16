# ps5plex

Watch your Real-Debrid library on the PS5, served from this Mac.

| What | Where |
|---|---|
| **Source picker** (phone or Mac) | **http://192.168.3.20:8099** |
| Plex web | http://192.168.3.20:32400/web |
| Plex on the PS5 | sign in to the same Plex account |

Those use this Mac's current LAN address. If your router hands it a different
one, find it with `ipconfig getifaddr en0` and update `PLEX_ADVERTISE_URL` in
`.env`. A DHCP reservation on the router avoids the problem entirely.

```
Real-Debrid --> rdserve (HTTP) --> rclone mount --> librarian --> Plex --> PS5
```

**Your Plex library contains only what you put on your Watchlist.** A Real-Debrid
account collects years of already-watched torrents; mirroring all of it into Plex
is noise, and walking it costs API calls you will regret. The watchlist robot
records everything it fetches in `state/managed.json`, and the librarian links
nothing else.

rdserve serves every torrent as a flat folder named after the release. The
**librarian** reads those names, works out what each one actually is, and builds
a symlink tree Plex can read cleanly:

```
/library/movies/La La Land (2016)/La La Land (2016).mkv
/library/shows/The Boys/Season 05/The Boys - S05E01.mkv
```

Symlinks download nothing — they point back into the mount, which streams on
demand.

Nothing runs in the background. You start it when you want to watch something and
`Ctrl+C` when you're done.

## First run

1. **Tokens.**
   ```
   cp .env.example .env
   ```
   - `RD_TOKEN` — https://real-debrid.com/apitoken
   - `PLEX_CLAIM` — https://plex.tv/claim — **expires 4 minutes after you generate
     it**, so grab it immediately before step 2. Only needed once; blank it after.

2. **Start it.**
   ```
   docker compose up
   ```
   (or double-click `ps5plex.command` in Finder)

   First start pulls images and builds the Plex image — a few minutes. After that
   it's seconds.

3. **Configure Plex** at http://localhost:32400/web. The network and analysis
   settings are seeded automatically on first boot by `docker/plex-prefs.sh`, so
   all you do here is sign in and add two libraries:

   | Library | Type | Folder |
   |---|---|---|
   | Movies | Movie | `/media/movies` |
   | TV Shows | TV Show | `/media/shows` |

   Turn **off** "Generate video preview thumbnails" if it isn't already — see
   *Gotchas*.

4. **PS5.** Install Plex from the PlayStation Store, sign in to the same Plex
   account, and the server appears.

## Choosing what to watch

Open **http://localhost:8099** on this Mac, or **http://192.168.3.20:8099** from
your phone — it is on your LAN already, nothing extra to host.

Releases already cached at Real-Debrid are listed **first** and marked
"⚡ plays now" — they start immediately. Anything else is marked "must download"
and has to be fetched first, which can take minutes just to resolve the magnet.

It lists your Plex Watchlist, and for each title every available release, ranked
best-first, with size, estimated bitrate and whether Real-Debrid already has it
cached. The bitrate is information, not a filter: nothing is pushed down for
being big, you decide what your link can carry. Click one and only that one is
added; picking another source for the same title replaces it. Like Stremio's
stream list, and nothing is fetched behind your back.

The same page lists what is currently in your library, with a remove button.

Each title also has a **subtitles** button. It lists what OpenSubtitles has,
Turkish first then English, from the same `subs5.strem.io` addon your Stremio
already uses — no API key. Picking one saves it as a sidecar file next to the
video, `Backrooms (2026).tur.srt` beside `Backrooms (2026).mkv`, which Plex reads
as a selectable track.

Sidecar files are deliberate. Subtitles embedded in a release are often the wrong
version, and image-based ones (PGS) cannot be rendered by the PS5 client at all —
Plex has to burn those into the picture, which turns a free direct stream into a
full transcode. A plain `.srt` is the cheapest thing for the PS5 to handle.

Automatic fetching still exists but is **off by default**. `AUTO_FETCH=1` turns it
on and `MAX_PER_TITLE` caps it, because the first version quietly pulled 17
torrents for a single show.

Taking a title off your Watchlist removes it from the library on the next pass.
The torrent stays in your Real-Debrid account; only the library forgets it.

## The watchlist robot

Add a title to your Plex Watchlist — from the PS5 Plex app, your phone, anywhere
— and within ~2 minutes it is in your library and playable.

Two values in `.env` switch it on:

```
./scripts/plex-token.sh          # fills PLEX_TOKEN (needs the server claimed)
SCRAPER_URL=...                  # your Stremio addon's install URL
```

`SCRAPER_URL` is whichever scraper you already use in Stremio — Comet, Torrentio
or MediaFusion. In Stremio: Addons -> the addon -> copy install URL, minus the
trailing `/manifest.json`. It already carries your Real-Debrid configuration.
They all speak the same `/stream/{type}/{imdb}.json` shape.

Note Torrentio is behind Cloudflare and returns 403 to anything that isn't a
browser, so Comet or MediaFusion work better here.

What it does each pass: read the Watchlist, resolve each title to an IMDb id, ask
the scraper what exists, score the candidates with the same `rank.py` the
librarian uses, and send the winner to Real-Debrid. Camera rips are never
selected, whatever their claimed resolution. If the title is already in your
Real-Debrid account it is adopted rather than fetched again.

Series are fetched one season at a time. Stremio's metadata gives the seasons
that have actually aired, then the scraper is asked for episode 1 of each. Comet
resolves a season pack down to the individual episode file, but reports the
*torrent's* hash — so adding it pulls in the whole season. One request and one
add per season, not per episode.

### Original audio

Ranking prefers the language a title was actually made in, not English. Stremio's
metadata gives the country of origin, which maps to a language, and a dub *away*
from it is penalised heavily.

The distinction that matters is dub versus subtitle. `VOSTFR` is French subtitles
over Japanese audio — fine. `Castellano` is a Spanish dub — not fine. And for a
Spanish film, Castellano *is* the original, so it carries no penalty at all.
`ENG.ITA` lists two audio tracks rather than being an Italian dub, so naming the
original language cancels most of the penalty.

Burned-in subtitles (`CHS`, `中字`) are penalised harder than soft ones, since
they cannot be switched off.

### Rate limits

Real-Debrid limits its API hard, and an over-eager client gets refused on
`api.real-debrid.com` while `real-debrid.com` keeps serving normally — which
looks exactly like a network fault and is not one. `./scripts/rd-check.sh` tells
the two apart. If it says rate-limited: wait, don't restart the stack.

## Daily use

```
docker compose up      # start
Ctrl+C                 # stop — nothing is left running
```

The Mac has to stay awake while you're watching.

## Gotchas

**The PS5 says "Currently Unavailable".** Its Plex app rejects the server's TLS
certificate — the server log shows `CERT: incomplete TLS handshake ... tlsv1
alert unknown ca`. This is a Plex bug, not a network problem, and it happens on
bare-metal installs too. The fix is to make Plex mint a fresh certificate:

```
docker compose stop plex
docker run --rm -v ps5plex_plex-config:/config alpine \
  rm -f "/config/Library/Application Support/Plex Media Server/Cache/certificate.p12"
docker compose up -d plex
```

Keep **secure connections on "Preferred"**, not Required or Disabled. Everything
else — DNS, firewall, subnets, the `172.x` address Docker advertises — is a red
herring here; read the server log before chasing any of it.

**"Not authorized" in the browser, and Plex thinking your PS5 is remote.** Behind
Docker's NAT every connection appears to come from the bridge gateway
(`192.168.65.1`) rather than your LAN, so Plex locks you out of setup *and* would
bill PS5 playback as *remote* — which since April 2026 needs a paid Plex Pass.
`plex-prefs.sh` sets `allowedNetworks` to RFC1918 space on first boot for the
setup lock-out; the loopback relay below handles the remote/local question.

**Plex advertises an unreachable address.** Inside the container it only knows its
`172.x` bridge IP. Set `PLEX_ADVERTISE_URL` in `.env` to this Mac's LAN address
(`ipconfig getifaddr en0`); `plex-prefs.sh` writes it to `customConnections`.

**Never let Plex download your library.** rclone runs with `--vfs-cache-mode off`
on purpose: in `full` mode it downloads each file *in its entirety* the first time
anything reads a byte, so merely scanning drags the whole library onto disk. For
the same reason preview thumbnails, chapter thumbs, loudness analysis and deep
media analysis are all disabled — each one reads whole files off Real-Debrid.

**Release extras hijack the movie.** Groups like Tigole ship the feature plus a
dozen featurettes in one folder, and Plex's movie scanner treats every video file
in a folder as its own movie — so clicking "La La Land" can start a 120 MB
making-of. The librarian links only the largest file of a movie torrent.

**Classification is parsing, not regex.** Deciding movie-vs-series from a release
name is genuinely hard: `Invencible [HDTV 1080p][Cap.406]`,
`Паук Нуар - 1 сезон`, `[Anime Time] Attack On Titan (Complete Collection)`.
`librarian/classify.py` uses **guessit**, a maintained release-name parser, plus
two guards found by testing against this actual library:

  * guessit reads a leading number and a year as season/episode, turning
    *10 Things I Hate About You (1999)* into S1999E10. Real seasons are small.
  * "Complete Series"/"Complete Collection" packs carry no season token at all.

That reaches ~97% on the 87 releases here. The remainder are anime batches with
no season, episode or keyword anywhere in the name — undecidable from the name,
so they go in `config/overrides.yml`.

Run `python3 librarian/test_classify.py` to check the rules still hold.

**Give the Docker VM enough CPU.** Docker Desktop → Settings → Resources. There's no
hardware transcoding here (that's a Plex Pass feature), so software transcoding is
the entire budget — give it 10+ cores.

## If the PS5 transcodes something it should direct play

Check Plex -> Settings -> Status -> Now Playing. Two causes, both outside the
file itself:

**The PS5 app's own quality setting.** If it is set to a fixed bitrate rather
than Original/Maximum, the console *asks* for a transcode and the server obliges
— the log shows the client requesting `directPlay=0&directStream=0`. Set Video
Quality to **Original** in the PS5 Plex app.

**Plex thinking the console is remote.** Plex decides "local" one way only: is
the client on the same subnet as one of its own interfaces? Inside Docker its
interface is `172.x`, and every outside client arrives NAT'd from Docker's gateway
`192.168.65.1` — so Plex logs the PS5 as `(Allowed Network (WAN))` and, since
April 2026, refuses to play without a paid Remote Watch Pass. There is no setting
for this; the old "LAN Networks" preference (`LanNetworksBandwidth`) was removed
from the server and writing it does nothing.

`docker/loopback-proxy.sh` solves it at the source: the published port lands on a
`socat` relay inside the Plex container, which forwards over loopback. Plex sees
`127.0.0.1`, logs `(Loopback)`, and treats every client as local. It is a layer-4
relay, so TLS still terminates in Plex and the `plex.direct` certificate keeps
working. The relay must not use port 32401 — Plex binds `127.0.0.1:32401` for an
internal service, and a relay there makes every Plex start fail with
`Error binding acceptor: Address in use`.

Transcode scratch must stay on **disk**, never tmpfs. A 6 GB RAM disk was enough
for a high-bitrate transcode to fill, after which the kernel SIGKILLed the
transcoder and playback died with "Playback error".

## Every byte goes through this Mac

Real-Debrid → this Mac → PS5. Plex clients only ever fetch from the Plex server;
the PS5 cannot pull from Real-Debrid itself, however good its own connection is.
So the Mac's internet link decides what plays smoothly. The picker shows each
release's estimated bitrate (size ÷ runtime) next to its size so you can judge
that yourself; it does not rank on it.

**The PS5 app, not Plex, is what "crashes".** Its playback buffer is a few
seconds and it abandons a stream at the first stall, then retries as a transcode,
which stalls the same way. Server-side, Plex's transcoder throttle buffer is
raised from 60 s to 300 s (`TranscoderThrottleBuffer`, seeded on boot), so when
the source arrives faster than realtime the server holds a five-minute head
start the console cannot drain. rclone also reads with a 256 MB buffer and
rdserve caches Real-Debrid's download links instead of re-authorising every
chunk, both to smooth the feed.

## Codecs: why the library prefers 1080p x264

The PS5 decodes 4K HEVC fine — it plays 4K UHD Blu-rays. But **Plex's PS5 profile
only accepts HEVC inside MP4**, and torrents are MKV. For H.264, Plex does a nearly
free MKV→MP4 container swap. For HEVC it refuses, and falls back to a full re-encode.

| Release | What Plex does | Cost |
|---|---|---|
| 1080p **x264** MKV | Direct Stream (container swap) | ~nothing |
| 1080p x265 MKV | full re-encode → 1080p H.264 | moderate |
| 2160p x265 HDR MKV | re-encode + software HDR→SDR tonemap | brutal |

4K is a *downgrade* here: it gets re-encoded down to 1080p for the PS5 anyway, with
HDR flattened to SDR. A 1080p x264 release usually looks better and costs nothing.

### Measured on this machine

Play one of each from the PS5 and watch **Plex → Dashboard → Now Playing**, which
reports Direct Play / Direct Stream / Transcode per stream. Record results here:

Good candidates already in this library:

| Test file | Expected | Actual | Holds realtime? |
|---|---|---|---|
| `Ted.2012...x264.YIFY.mp4` | Direct **Play** (MP4+x264) | _todo_ | |
| `Conclave 2024 ...H264...mkv` | Direct **Stream** (remux) | _todo_ | |
| any 1080p x265 (most of the library) | Transcode | _todo_ | |
| a 2160p x265 | Transcode | _todo_ | |

This library is 47/87 x265 and only 8/87 x264, so most existing titles will
transcode. That is what the watchlist robot's x264 preference is for.

This decides whether 4K stays in the watchlist robot's fallback ladder.

## Troubleshooting

**`rdserve` never becomes healthy.** It refuses to start without `RD_TOKEN`; if it
starts but logs `could not list torrents`, the token is wrong or expired, or
Real-Debrid is rate-limiting you (`./scripts/rd-check.sh`).

**Plex libraries are empty.** Check the mount actually happened:
```
docker compose exec plex ls /media
docker compose exec plex cat /config/rclone.log
```
An empty listing with no error usually means rclone couldn't reach rdserve, or
nothing has been picked yet — rdserve only serves what `managed.json` lists.

**Playback stutters on a 4K file.** Expected without Plex Pass — see the table above.
Fetch the 1080p release instead.

**A series shows up under Movies.** Its folder name has no recognisable season or
episode marker, so `guessit` cannot tell. Add a substring for it to
`config/overrides.yml`.

**TV matching is weaker than movies.** Single-episode torrents land as flat folders
rather than `Show/Season 01/...`, which Plex's TV scanner handles inconsistently.
Movies are the solid path today.

## Layout

```
compose.yaml              the whole stack
rdserve/                  serves your picked torrents as an HTTP tree
config/rclone.conf        http remote pointing at rdserve
config/overrides.yml      movie/series overrides for names guessit cannot read
docker/plex.Dockerfile    linuxserver/plex + rclone + librarian + relay
docker/plex-prefs.sh      seeds Plex network/analysis prefs on first boot
docker/rclone-mount.sh    mounts rdserve at /media before Plex starts
docker/loopback-proxy.sh  socat relay so Plex sees the PS5 as local
librarian/                builds the Plex-shaped symlink tree, shared rank.py
watchlist/                the picker web UI and the (off by default) poller
.env                      your tokens (gitignored)
```
