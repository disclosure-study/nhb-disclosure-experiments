"""
Immutable export CLI.

    python -m app.export --study s3
    python -m app.export --study s4

Copies the canonical write-ahead JSONL plus a materialized participants.csv and a
SQLite mirror into data/<study>/export-<UTC timestamp>/, with a MANIFEST that
records the platform version. This export — not the live server DB — is the
record of truth that the s3_audit.py / s4_audit.py scripts read.
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import admin, config


def export(study: str) -> Path:
    sdir = config.DATA_DIR / study
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = sdir / f"export-{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    # 1) canonical JSONL
    for f in sorted(sdir.glob("*.jsonl")):
        shutil.copy2(f, out / f.name)

    # 2) materialized participants.csv
    (out / "participants.csv").write_text(admin.participants_csv(study), encoding="utf-8")

    # 3) SQLite mirror
    if config.DB_PATH.exists():
        shutil.copy2(config.DB_PATH, out / "platform_mirror.sqlite3")

    # 4) manifest
    counts = {f.name: sum(1 for _ in open(f, encoding="utf-8")) for f in sorted(out.glob("*.jsonl"))}
    manifest = [
        f"platform_version={config.PLATFORM_VERSION}",
        f"study={study}",
        f"exported_at={stamp}",
        "jsonl_line_counts:",
        *[f"  {k}={v}" for k, v in counts.items()],
    ]
    (out / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Export raw study data.")
    ap.add_argument("--study", choices=["s3", "s4"], required=True)
    args = ap.parse_args()
    out = export(args.study)
    print(f"Exported {args.study} -> {out}")


if __name__ == "__main__":
    main()
