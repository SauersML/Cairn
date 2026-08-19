#!/usr/bin/env python3
"""Smoke test for contrib/proof_portfolios.py using only the standard library."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def write(root, name, text):
    (root / "research" / f"{name}.md").write_text(text, encoding="utf-8")


def main():
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "research").mkdir()
        claims = {
            "target": "root: true\ngoal: true\n",
            "leaf-a": "",
            "leaf-b": "",
            "leaf-c": "",
            "cycle-d": "",
            "cycle-e": "",
        }
        for cid, extra in claims.items():
            write(root, cid, f"---\nrg: 2\nid: {cid}\nkind: claim\ntitle: {cid}\n{extra}---\n\n{cid}.\n")
        routes = {
            "target-via-a": ("target", "[leaf-a]"),
            "target-via-bc": ("target", "[leaf-b, leaf-c]"),
            "target-via-cycle": ("target", "[cycle-d]"),
            "cycle-d-via-e": ("cycle-d", "[cycle-e]"),
            "cycle-e-via-d": ("cycle-e", "[cycle-d]"),
        }
        for rid, (target, reqs) in routes.items():
            write(root, rid,
                  f"---\nrg: 2\nid: {rid}\nkind: route\ntitle: {rid}\ntarget: {target}\nrequires: {reqs}\n---\n\nProof reduction.\n")
        proc = subprocess.run(
            [sys.executable, str(repo / "contrib" / "proof_portfolios.py"),
             "--root", str(root), "target", "--max-size", "3", "--json"],
            check=True, text=True, capture_output=True)
        data = json.loads(proc.stdout)
        got = [row["leaves"] for row in data["portfolios"]]
        assert got == [["leaf-a"], ["leaf-b", "leaf-c"]], got
        assert data["portfolio_count"] == 2
        assert data["truncated"] is False
    print("proof_portfolios smoke test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
