#!/usr/bin/env python3
"""Serve the quiz locally.  python serve.py  ->  http://localhost:8899"""
import http.server, functools, socketserver, webbrowser
from pathlib import Path

PORT, ROOT = 8899, Path(__file__).resolve().parent / "site"
if not (ROOT / "data.json").exists():
    raise SystemExit("site/data.json missing — run `python run.py` first")
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
with socketserver.TCPServer(("", PORT), handler) as httpd:
    url = f"http://localhost:{PORT}"
    print(f"serving {ROOT} at {url}  (ctrl-c to stop)")
    webbrowser.open(url)
    httpd.serve_forever()
