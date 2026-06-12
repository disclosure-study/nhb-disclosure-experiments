"""Study-3 audit wrapper (DESIGN §9.7). Recompute NUMBERS_FOR_MS.md from raw JSONL.

    python analysis/s3_audit.py --data server/data/s3
"""
import argparse
from pathlib import Path
import audit

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="server/data/s3")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d = Path(a.data)
    audit.run("s3", d, Path(a.out) if a.out else d / "audit_out")
