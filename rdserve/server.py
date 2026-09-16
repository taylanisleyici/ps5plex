"""Serve the Real-Debrid titles we asked for as a plain HTTP directory tree.

This replaces zurg. zurg is a closed-source binary with the API hostname compiled
in, and this network drops TLS handshakes for exactly that name while the
api-1/api-2/api-6 aliases — same servers, same IPs — answer fine. Nothing about
zurg could be configured around that.

What it has to do is small, because the library only ever contains what the
watchlist robot fetched:

    /                       one directory per managed torrent
    /<torrent>/             its video files
    /<torrent>/<file>       302 to a fresh Real-Debrid download link

rclone's `http` backend mounts that as a filesystem, Plex reads the mount, and
playback streams straight from Real-Debrid's CDN. Nothing is stored locally.
"""
import html
import json
import os
import pathlib
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

STATE = pathlib.Path(os.environ.get("PS5PLEX_STATE", "/state/managed.json"))
RD_TOKEN = os.environ.get("RD_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "8080"))
REFRESH = int(os.environ.get("RD_REFRESH_SECS", "300"))
VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".webm")

RD_HOSTS = ("api.real-debrid.com", "api-1.real-debrid.com",
            "api-2.real-debrid.com", "api-6.real-debrid.com")


def pick_host():
    for host in RD_HOSTS:
        try:
            requests.get(f"https://{host}/rest/1.0/time", timeout=8).raise_for_status()
            return host
        except Exception:
            continue
    return RD_HOSTS[0]


RD = f"https://{pick_host()}/rest/1.0"
AUTH = {"Authorization": f"Bearer {RD_TOKEN}"}

# torrent folder name -> {file name: rd link}. Rebuilt on a timer so that adding
# something to the watchlist shows up without a restart.
_tree = {}
_lock = threading.Lock()


def refresh():
    """Rebuild the tree from the torrents the watchlist robot recorded."""
    try:
        managed = json.loads(STATE.read_text())
    except (OSError, ValueError):
        managed = {}
    if not managed:
        return

    try:
        torrents = requests.get(f"{RD}/torrents", params={"limit": 200},
                                headers=AUTH, timeout=30).json()
    except Exception as e:
        print(f"[rdserve] could not list torrents: {e}", flush=True)
        return

    tree = {}
    for t in torrents:
        name = t.get("filename")
        if name not in managed or t.get("status") != "downloaded":
            continue
        try:
            info = requests.get(f"{RD}/torrents/info/{t['id']}", headers=AUTH,
                                timeout=30).json()
        except Exception:
            continue
        # `links` lines up with the *selected* files, in order.
        selected = [f for f in info.get("files", []) if f.get("selected")]
        links = info.get("links", [])
        files = {}
        for f, link in zip(selected, links):
            leaf = f["path"].lstrip("/").rsplit("/", 1)[-1]
            if leaf.lower().endswith(VIDEO_EXTS):
                files[leaf] = link
        if files:
            tree[name] = files

    with _lock:
        _tree.clear()
        _tree.update(tree)
    print(f"[rdserve] {len(tree)} titles, "
          f"{sum(len(v) for v in tree.values())} files", flush=True)


def refresher():
    while True:
        try:
            refresh()
        except Exception as e:
            print(f"[rdserve] refresh failed: {e}", flush=True)
        time.sleep(REFRESH)


# Real-Debrid download links stay valid for hours, but rclone re-requests the
# file for every chunk it reads, and each request was a fresh /unrestrict/link
# call: ~0.6 s of dead time per 32-128 MB, plus API quota. Remember the CDN URL
# instead, and only ask again when it has aged or actually failed.
_direct = {}
_direct_lock = threading.Lock()
DIRECT_TTL = int(os.environ.get("RD_LINK_TTL_SECS", "7200"))


def _direct_url(link, leaf):
    now = time.time()
    with _direct_lock:
        hit = _direct.get(link)
        if hit and hit[1] > now:
            return hit[0]
    try:
        r = requests.post(f"{RD}/unrestrict/link", headers=AUTH,
                          data={"link": link}, timeout=30)
        r.raise_for_status()
        direct = r.json()["download"]
    except Exception as e:
        print(f"[rdserve] unrestrict failed for {leaf}: {e}", flush=True)
        with _direct_lock:
            _direct.pop(link, None)
        return None
    with _direct_lock:
        _direct[link] = (direct, now + DIRECT_TTL)
    return direct


def listing(title, entries):
    """Minimal HTML index — rclone's http backend just reads the hrefs."""
    rows = "\n".join(
        f'<a href="{html.escape(urllib.parse.quote(name))}{"/" if is_dir else ""}">'
        f'{html.escape(name)}</a><br>' for name, is_dir in entries)
    return f"<html><head><title>{html.escape(title)}</title></head><body>\n{rows}\n</body></html>"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass                      # one line per range request is far too noisy

    def _send_html(self, body):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path).strip("/")
        with _lock:
            tree = dict(_tree)

        if not path:
            return self._send_html(listing("titles", [(n, True) for n in sorted(tree)]))
        if path == "refresh":
            # The picker calls this right after adding a torrent, so the new
            # folder is listable without waiting out the timer.
            refresh()
            return self._send_html("ok")

        folder, _, leaf = path.partition("/")
        if folder not in tree:
            self.send_error(404)
            return
        if not leaf:
            return self._send_html(listing(folder, [(n, False) for n in sorted(tree[folder])]))

        link = tree[folder].get(leaf)
        if not link:
            self.send_error(404)
            return
        direct = _direct_url(link, leaf)
        if not direct:
            self.send_error(502)
            return
        # Hand the client straight to Real-Debrid's CDN; range requests and all
        # the streaming work happen there, not here.
        self.send_response(302)
        self.send_header("Location", direct)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    if not RD_TOKEN:
        raise SystemExit("[rdserve] RD_TOKEN is not set")
    print(f"[rdserve] using {RD}, serving on :{PORT}", flush=True)
    threading.Thread(target=refresher, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
