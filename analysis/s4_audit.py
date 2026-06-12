"""Study-4 audit wrapper (DESIGN §9.8). Recompute NUMBERS_FOR_MS.md from raw JSONL.

    python analysis/s4_audit.py --data server/data/s4
"""
import argparse
from pathlib import Path
import audit

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="server/data/s4")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d = Path(a.data)
    audit.run("s4", d, Path(a.out) if a.out else d / "audit_out")
