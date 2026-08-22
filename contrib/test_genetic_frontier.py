#!/usr/bin/env python3
"""Standard-library smoke tests for contrib/genetic_frontier.py."""

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "genetic_frontier", HERE / "genetic_frontier.py")
GF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GF)


class Claim:
    def __init__(self, cid, title, body=""):
        self.id = cid
        self.title = title
        self.body = body
        self.status = "OPEN"


class FakeCairn:
    @staticmethod
    def goal_cone(_graph, _target):
        return {"goal", "a", "b", "c", "d", "bridge", "z", "w"}

    @staticmethod
    def undecomposed_open(_graph):
        return ["a", "b", "c", "d"]


class FakeGraph:
    def __init__(self):
        self.claims = {
            "goal": Claim("goal", "Target theorem"),
            "a": Claim("a", "Algebraic lane", "operator compression"),
            "b": Claim("b", "Geometric lane", "sphere matching"),
            "c": Claim("c", "Trace lane", "character trace"),
            "d": Claim("d", "Obstruction lane", "invalidating witness"),
            "bridge": Claim("bridge", "Joint bridge"),
            "z": Claim("z", "C-only consequence"),
            "w": Claim("w", "D-only consequence"),
        }
        self.established = {"base"}
        self.claim_impact = {"a": 2, "b": 2, "c": 1, "d": 1}

    def _solve(self, forced=frozenset()):
        forced = set(forced)
        est = set(self.established) | forced

        # a and b are individually inert but jointly unlock a bridge and goal:
        # exact positive/synthetic epistasis.
        if {"a", "b"} <= forced:
            est |= {"bridge", "goal"}

        # c and d each have a downstream consequence on their own, but the
        # combination suppresses both (standing in for obstruction firing).
        if "c" in forced and "d" not in forced:
            est.add("z")
        if "d" in forced and "c" not in forced:
            est.add("w")

        return est, set(), set(), {}, True


def main():
    graph = FakeGraph()
    payload = GF.analyze(
        FakeCairn(), graph, "goal", pool=4, pair_limit=6, panel_size=3)

    assert payload["status"] == "ok"
    assert payload["open_leaf_count"] == 4

    top = payload["positive_epistasis"][0]
    assert {top["a"], top["b"]} == {"a", "b"}
    assert top["synthetic_target"] is True
    assert set(top["emergent"]) == {"bridge", "goal"}
    assert top["synergy"] == 2

    bad = [row for row in payload["antagonistic_epistasis"]
           if {row["a"], row["b"]} == {"c", "d"}]
    assert len(bad) == 1
    assert set(bad[0]["suppressed"]) == {"w", "z"}
    assert bad[0]["synergy"] == -2

    panel = payload["balancing_panel"]
    assert len(panel) == 3
    assert len({row["id"] for row in panel}) == 3

    print("genetic_frontier smoke test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
