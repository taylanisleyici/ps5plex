"""A Stremio-style source picker for your Plex Watchlist.

The automatic version fetched 17 torrents for one show without ever showing them,
which is not something that should happen to your Real-Debrid account. So the
default is now manual: this serves a page listing your watchlist, and for each
title the available releases ranked best-first, with the reasons visible. You
click the one you want and only that one is added.

Automatic fetching still exists (AUTO_FETCH=1) but is off unless asked for, and
capped by MAX_PER_TITLE so a long-running series cannot pull a back catalogue.
"""
import html
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

import poller
from rank import is_banned, score

PORT = int(os.environ.get("PICKER_PORT", "8081"))

CSS = """
body{font:15px system-ui;margin:0;background:#141414;color:#e8e8e8}
header{padding:14px 18px;background:#1e1e1e;border-bottom:1px solid #333}
a{color:#e5a00d;text-decoration:none} a:hover{text-decoration:underline}
.wrap{padding:16px 18px;max-width:900px}
.item{padding:10px 12px;margin:6px 0;background:#1c1c1c;border-radius:6px}
.row{display:flex;justify-content:space-between;gap:12px;align-items:center}
.meta{color:#999;font-size:13px}
.best{border-left:3px solid #e5a00d}
.bad{opacity:.45}
form{display:inline}
button{background:#e5a00d;border:0;color:#111;padding:6px 12px;border-radius:4px;
cursor:pointer;font-weight:600}
button.sec{background:#333;color:#ddd}
"""


def page(title, body):
    return (f"<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
            f"<header><a href='/'>ps5plex</a> &middot; {html.escape(title)}</header>"
            f"<div class='wrap'>{body}</div></body></html>")


def gb(n):
    return f"{n / 1024 ** 3:.1f} GB" if n else "?"


def candidates(kind, imdb, season=None, episode=None):
    """Every usable release for a title, best first, with why."""
    origin = poller.title_origin(kind, imdb)
    if kind == "show":
        url = f"{poller.SCRAPER}/stream/series/{imdb}:{season or 1}:{episode or 1}.json"
    else:
        url = f"{poller.SCRAPER}/stream/movie/{imdb}.json"
    r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()

    out, seen = [], set()
    for s in r.json().get("streams", []):
        release, info_hash, size, cached, context = poller._stream_fields(s)
        if not (release and info_hash) or info_hash in seen:
            continue
        seen.add(info_hash)
        pts = score(release, size, origin, context)
        out.append({"release": release, "hash": info_hash, "size": size,
                    "cached": cached, "score": pts,
                    "junk": pts is None or is_banned(release) or is_banned(context)})
    out.sort(key=lambda c: (-1e9 if c["score"] is None else -c["score"]))
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body, code=200):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- pages ----------------------------------------------------------
    def home(self):
        managed = poller.load_managed()
        rows = []
        for kind, title, key in poller.watchlist():
            imdb = poller.imdb_id(key) or ""
            have = [v for v in managed.values() if v.get("imdb") == imdb]
            state = (f"<span class='meta'>{len(have)} fetched</span>"
                     if have else "<span class='meta'>nothing fetched</span>")
            link = f"/pick?kind={kind}&imdb={imdb}&title={urllib.parse.quote(title)}"
            rows.append(f"<div class='item'><div class='row'><div><b>{html.escape(title)}</b>"
                        f"<br>{state}</div><a href='{link}'><button>choose source</button></a>"
                        f"</div></div>")
        if not rows:
            rows = ["<p class='meta'>Nothing on your Plex Watchlist.</p>"]

        lib = "".join(
            f"<div class='item'><div class='row'><div>{html.escape(v['title'])}"
            + (f" <span class='meta'>S{v['season']:02d}</span>" if v.get("season") else "")
            + f"<br><span class='meta'>{html.escape(f[:70])}</span></div>"
            f"<form method='post' action='/remove'>"
            f"<input type='hidden' name='folder' value='{html.escape(f)}'>"
            f"<button class='sec'>remove</button></form></div></div>"
            for f, v in managed.items())

        return page("watchlist", "<h3>On your watchlist</h3>" + "".join(rows)
                    + "<h3>In your library</h3>" + (lib or "<p class='meta'>Empty.</p>"))

    def pick(self, q):
        kind = q.get("kind", ["movie"])[0]
        imdb = q.get("imdb", [""])[0]
        title = q.get("title", ["?"])[0]
        season = int(q.get("season", ["1"])[0])
        episode = int(q.get("episode", ["1"])[0])
        if not imdb:
            return self._send(page("error", "<p>No IMDb id.</p>"), 400)

        head = f"<h3>{html.escape(title)}</h3>"
        if kind == "show":
            schedule = poller.aired_episodes(imdb)
            seasons = " ".join(
                f"<a href='/pick?kind=show&imdb={imdb}&title={urllib.parse.quote(title)}"
                f"&season={s}&episode=1'>{'<b>S%02d</b>' % s if s == season else 'S%02d' % s}</a>"
                for s in schedule)
            eps = " ".join(
                f"<a href='/pick?kind=show&imdb={imdb}&title={urllib.parse.quote(title)}"
                f"&season={season}&episode={e}'>{'<b>E%02d</b>' % e if e == episode else 'E%02d' % e}</a>"
                for e in schedule.get(season, []))
            head += f"<p class='meta'>season: {seasons}</p><p class='meta'>episode: {eps}</p>"

        rows = []
        for c in candidates(kind, imdb, season, episode):
            cls = "item bad" if c["junk"] else ("item best" if not rows else "item")
            why = []
            if c["junk"]:
                why.append("cam/screener — not recommended")
            if c["cached"]:
                why.append("instant (cached)")
            why.append(gb(c["size"]))
            if c["score"] is not None:
                why.append(f"score {c['score']:.0f}")
            rows.append(
                f"<div class='{cls}'><div class='row'><div>{html.escape(c['release'][:96])}"
                f"<br><span class='meta'>{' &middot; '.join(why)}</span></div>"
                f"<form method='post' action='/add'>"
                f"<input type='hidden' name='hash' value='{c['hash']}'>"
                f"<input type='hidden' name='imdb' value='{imdb}'>"
                f"<input type='hidden' name='kind' value='{kind}'>"
                f"<input type='hidden' name='title' value='{html.escape(title)}'>"
                f"<input type='hidden' name='season' value='{season if kind == 'show' else ''}'>"
                f"<button>add this</button></form></div></div>")
        return self._send(page(title, head + (
            "".join(rows) or "<p class='meta'>No releases found.</p>")))

    # ---- actions --------------------------------------------------------
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        managed = poller.load_managed()

        if self.path == "/add":
            season = form.get("season", [""])[0]
            season = int(season) if season else None
            poller._add_and_record(
                (0, form["hash"][0], form["hash"][0]), form["title"][0], form["imdb"][0],
                "", form["kind"][0], managed, season)
            return self._redirect("/")

        if self.path == "/remove":
            folder = form["folder"][0]
            managed.pop(folder, None)
            poller.save_managed(managed)
            # The torrent itself is left in Real-Debrid; only the library forgets it.
            return self._redirect("/")
        self.send_error(404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/":
                return self._send(self.home())
            if parsed.path == "/pick":
                return self.pick(urllib.parse.parse_qs(parsed.query))
        except Exception as e:
            return self._send(page("error", f"<pre>{html.escape(str(e))}</pre>"), 500)
        self.send_error(404)


def main():
    print(f"[picker] open http://localhost:{PORT}/", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
