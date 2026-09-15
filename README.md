# ps5plex

Watch your Real-Debrid library on the PS5, served from this Mac.

```
Real-Debrid --> zurg (WebDAV) --> rclone mount --> librarian --> Plex --> PS5
```

zurg serves every torrent as a flat folder named after the release. The
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
selected, whatever their claimed resolution.

## Daily use

```
docker compose up      # start
Ctrl+C                 # stop — nothing is left running
```

The Mac has to stay awake while you're watching.

## Gotchas

**"Not authorized" in the browser, and Plex thinking your PS5 is remote.** Behind
Docker's NAT every connection appears to come from the bridge gateway
(`192.168.65.1`) rather than your LAN, so Plex locks you out of setup *and* would
bill PS5 playback as *remote* — which since April 2026 needs a paid Plex Pass.
`plex-prefs.sh` sets `allowedNetworks` and `LanNetworksBandwidth` to RFC1918 space
on first boot to prevent both.

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

**`zurg` never becomes healthy.** It doesn't open its port until Real-Debrid accepts
the token, so this almost always means `RD_TOKEN` is wrong or expired.

**Plex libraries are empty.** Check the mount actually happened:
```
docker compose exec plex ls /media/movies
docker compose exec plex cat /config/rclone.log
```
An empty listing with no error usually means rclone couldn't reach zurg.

**Playback stutters on a 4K file.** Expected without Plex Pass — see the table above.
Fetch the 1080p release instead.

**A series shows up under Movies.** Its folder name has no recognisable season or
episode marker, so the `shows` filters in `config/zurg-config.yml` miss it. Add a
pattern there.

**TV matching is weaker than movies.** Single-episode torrents land as flat folders
rather than `Show/Season 01/...`, which Plex's TV scanner handles inconsistently.
Movies are the solid path today.

## Layout

```
compose.yaml              the whole stack
config/zurg-config.yml    RD library -> movies/ + shows/ split (no secrets)
config/rclone.conf        WebDAV remote pointing at zurg
docker/plex.Dockerfile    linuxserver/plex + rclone
docker/plex-prefs.sh      seeds Plex network/analysis prefs on first boot
docker/rclone-mount.sh    mounts zurg at /media before Plex starts
watchlist/                Phase 2 — the watchlist robot
.env                      your tokens (gitignored)
```
