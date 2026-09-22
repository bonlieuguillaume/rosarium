"""Serve the rosarium webmap in the browser: ``python rosarium.py webmap --open``.

Standard library only. The server hands out the static page (``static/``) and
answers JSON routes under ``/api``; every feature exposed in the page brings
its own routes in a module of ``api/`` (see `FEATURES`). Everything else —
the map, the drawing, the lists, the selection — happens in the page, with
Leaflet and Leaflet.draw.

Usage:
    python rosarium.py webmap                    # http://localhost:8050
    python rosarium.py webmap --open             # and open the browser
    python rosarium.py webmap --port 9000 --path-file C:/data/list.txt

The default port is 8050 on purpose — 8000 is taken by the Moonfleet viewer,
and both may run at the same time.

The page's defaults — path file, path style, map view, dates — come from the
command line; the path file stays editable in the page.
"""

import argparse
import json
import mimetypes
import os
import platform
import sys
import threading
import time
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The repository root, whatever the current directory: features and api
# modules are imported from there
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.aoi_to_slc.aoi_to_slc import DEFAULT_STYLE, S3_PATH_STYLES  # noqa: E402
from frontend.api import aoi_to_slc as api_aoi_to_slc  # noqa: E402

STATIC = HERE / "static"
# Also what `rosarium.py download` reads by default: the two ends meet there
DEFAULT_PATH_FILE = ROOT / "data" / "utils" / "list.txt"

# The api modules serving the page, each with a `ROUTES` dict
# {"GET": {path: fn}, "POST": {path: fn}} whose functions take (body, config).
# A new feature in the page adds its module here.
FEATURES = [api_aoi_to_slc]

# Filled by main() from the command line, handed to the page by /api/config
# and to every route
CONFIG = {}

# --- Page liveness --------------------------------------------------------------
# The page pings while open and says goodbye when closed; once no page is
# left, the server stops on its own (unless --stay). A goodbye is followed by
# a short grace period, so a reload — goodbye, then a new page pinging — does
# not stop it. The long timeout is only a fallback for a page that vanished
# without a goodbye (browser crash): background tabs throttle their timers,
# so it must stay well above a minute.
PING_INTERVAL = 5       # seconds, what the page uses (handed out in the config)
PING_TIMEOUT = 120      # no ping for that long: the page is gone
BYE_GRACE = 3           # after a goodbye, wait that long for a new page
LIVENESS = {"last_ping": None, "deadline": None}


def api_config(_body, config):
    return config


def api_ping(_body, _config):
    LIVENESS["last_ping"] = time.monotonic()
    LIVENESS["deadline"] = LIVENESS["last_ping"] + PING_TIMEOUT
    return {"ok": True}


def api_bye(_body, _config):
    LIVENESS["deadline"] = time.monotonic() + BYE_GRACE
    return {"ok": True}


def _watch_pages(httpd, stop_event):
    """Stop the server once a page has connected and none is left."""
    while not stop_event.wait(1):
        deadline = LIVENESS["deadline"]
        if deadline is not None and time.monotonic() > deadline:
            print("  page closed, stopping the server.")
            httpd.shutdown()
            return


def _collect_routes():
    routes = {
        "GET": {"/api/config": api_config},
        "POST": {"/api/ping": api_ping, "/api/bye": api_bye},
    }
    for module in FEATURES:
        for method, table in module.ROUTES.items():
            clash = set(table) & set(routes[method])
            if clash:
                raise RuntimeError(f"{module.__name__} redefines {method} route(s): {sorted(clash)}")
            routes[method].update(table)
    return routes


ROUTES = _collect_routes()


# --- Server -------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    """index.html at the root, the other static files by name, JSON under /api."""

    def log_message(self, *args):
        pass  # the routes print what matters; request logs would drown it

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ROUTES["GET"]:
            self._call(ROUTES["GET"][path], {})
            return
        if path == "/":
            path = "/index.html"
        # Static files only from static/, no path escaping it
        target = (STATIC / path.lstrip("/")).resolve()
        if target.is_file() and STATIC in target.parents:
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type == "application/javascript":
                content_type += "; charset=utf-8"
            self._send(target.read_bytes(), content_type)
        else:
            self.send_error(404)

    def do_POST(self):
        route = ROUTES["POST"].get(self.path)
        if route is None:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        self._call(route, body)

    def _call(self, route, body):
        # Any error goes back to the page as text, where the status line shows
        # it; the server never dies on a bad AOI or a catalogue hiccup
        try:
            self._send_json(route(body, CONFIG))
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)

    def _send_json(self, payload, status=200):
        self._send(json.dumps(payload).encode("utf-8"), "application/json", status)

    def _send(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    """One server per port. HTTPServer sets SO_REUSEADDR, which on Windows lets
    a second process bind a port already in use — both then run and requests
    land on either. Without it, the second start fails, as it should.

    On Linux and macOS SO_REUSEADDR does not have that effect, and without it
    the port stays blocked for a minute (TIME_WAIT) after Ctrl+C, so an
    immediate restart would fail: keep the default there."""

    allow_reuse_address = sys.platform != "win32"


def _build_parser(prog=None):
    parser = argparse.ArgumentParser(
        prog=prog or "python frontend/server.py",
        description="rosarium webmap, served locally",
    )
    parser.add_argument("--port", type=int, default=8050, help="port to serve on (default: 8050)")
    parser.add_argument("--open", action="store_true", help="open the browser once the server is up")
    parser.add_argument(
        "--path-file", default=str(DEFAULT_PATH_FILE),
        help="default path file, editable in the page; a relative path resolves against "
        "the repository root (default: data/utils/list.txt, what `download` reads)",
    )
    parser.add_argument(
        "--style", choices=list(S3_PATH_STYLES), default=DEFAULT_STYLE,
        help="form of each line: mount = /eodata/..., s3 = s3://eodata/..., key = eodata/... "
        f"(default: {DEFAULT_STYLE})",
    )
    parser.add_argument("--center", nargs=2, type=float, default=(46.5, 2.5), metavar=("LAT", "LON"),
                        help="initial map centre (default: 46.5 2.5)")
    parser.add_argument("--zoom", type=int, default=6, help="initial zoom (default: 6)")
    parser.add_argument("--days", type=int, default=30, help="initial date range: the last N days (default: 30)")
    parser.add_argument("--max-items", type=int, default=300,
                        help="stop listing after that many products (default: 300)")
    parser.add_argument("--stay", action="store_true",
                        help="keep running after the page is closed (default: stop with it)")
    return parser


def _banner(url, args):
    env = os.environ.get("CONDA_DEFAULT_ENV") or "(no conda env)"
    lines = [
        "",
        "  rosarium webmap",
        f"  url        {url}",
        f"  env        {env}  (python {platform.python_version()}, {sys.executable})",
        f"  repo       {ROOT}",
        f"  path file  {args.path_file}  ({args.style} style)",
        "  " + ("Ctrl+C to stop" if args.stay else "stops when the page is closed; Ctrl+C to stop now"),
        "",
    ]
    print("\n".join(lines), flush=True)


def main(argv=None, prog=None):
    args = _build_parser(prog).parse_args(argv)

    end = date.today()
    CONFIG.update(
        root=str(ROOT),
        path_file=args.path_file, style=args.style,
        center=list(args.center), zoom=args.zoom,
        start=(end - timedelta(days=args.days)).isoformat(), end=end.isoformat(),
        max_items=args.max_items,
        ping_interval=PING_INTERVAL,
    )

    url = f"http://localhost:{args.port}/"
    try:
        httpd = Server(("", args.port), Handler)
    except OSError as exc:
        # Another server — this one already running, or another project's —
        # holds the port; the raw error is unreadable on Windows
        print(f"  port {args.port} is already in use ({exc.strerror}). "
              "Pick another one with --port, e.g. --port 8051", file=sys.stderr)
        return 1
    with httpd:
        _banner(url, args)
        if args.open:
            # A short delay so the server is listening when the page loads
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        stop_watch = threading.Event()
        if not args.stay:
            threading.Thread(target=_watch_pages, args=(httpd, stop_watch), daemon=True).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server stopped.")
        finally:
            stop_watch.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
