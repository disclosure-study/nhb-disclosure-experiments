"""
Generate the GitHub Pages preview build.

docs/ is a verbatim copy of web/ (the static frontend). On *.github.io the
frontend auto-detects that there is no backend and runs in browser-only PREVIEW
mode: client-side randomization, the offline assistant, and a visible local event
log. The live data-collection instrument is the same web/ served by the FastAPI
server (see server/). Re-run this whenever web/ changes:

    python scripts/build_pages.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DOCS = ROOT / "docs"


def main() -> None:
    if DOCS.exists():
        shutil.rmtree(DOCS)
    shutil.copytree(WEB, DOCS)
    # .nojekyll so Pages serves every file (incl. any underscore-prefixed paths) as-is.
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    files = sum(1 for _ in DOCS.rglob("*") if _.is_file())
    print(f"[build_pages] copied web/ -> docs/ ({files} files) + .nojekyll")


if __name__ == "__main__":
    main()
