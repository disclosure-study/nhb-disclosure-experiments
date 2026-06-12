"""Threaded static server for previewing web/ (screenshot-friendly).

    python scripts/serve_preview.py [port]

ThreadingHTTPServer avoids the head-of-line blocking that makes single-threaded
http.server hang screenshot/automation tools. To pick up edited assets without a
hard refresh, serve on a fresh port (a new origin has an empty browser cache).
"""
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
PORT = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8013))
handler = partial(SimpleHTTPRequestHandler, directory=ROOT)
print(f"serving {os.path.abspath(ROOT)} on http://127.0.0.1:{PORT}")
ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()
