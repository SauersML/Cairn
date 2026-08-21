#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cairn


def write_node(root, nid, kind, title, **meta):
    lines = ["---", "rg: 2", f"id: {nid}", f"kind: {kind}", f"title: {title}"]
    for key, value in meta.items():
        key = key.rstrip("_")
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (list, tuple)):
            lines.append(f"{key}: [{', '.join(value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines += ["---", "", f"Canonical statement for {title}.", ""]
    (root / "research" / f"{nid}.md").write_text("\n".join(lines), encoding="utf-8")


def claim(root, nid, title, **meta):
    write_node(root, nid, "claim", title, **meta)


def route(root, nid, title, target, requires):
    write_node(root, nid, "route", title, target=target, requires=requires)


def compile_at(root):
    return cairn.compile_graph(str(root / "research"), str(root))[0]


def run_cli(root, *args):
    env = os.environ.copy()
    env["CAIRN_ROOT"] = str(root)
    env["CAIRN_STATE"] = str(root / ".state")
    return subprocess.run([sys.executable, str(ROOT / "cairn.py"), *args],
                          cwd=root, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class Project(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "research").mkdir()

    def tearDown(self):
        self.tmp.cleanup()


class NegationTests(Project):
    def test_non_hyperlinear_negation_survives_search_and_duplicate_checks(self):
        self.assertIn("not", cairn._tokens("not hyperlinear"))
        self.assertIn("no", cairn._tokens("no hyperlinear group"))
        self.assertNotEqual(
            cairn._negation_signature("Every non-hyperlinear group is sofic"),
            cairn._negation_signature("No non-hyperlinear group is sofic"))
        self.assertIn(r"\text{is}", cairn.house_to_tex("A is B"))
        claim(self.root, "a-positive", "Every non-hyperlinear group is sofic", root_=True)
        claim(self.root, "z-negative", "No non-hyperlinear group is sofic")
        claim(self.root, "c-copy", "Every non-hyperlinear group is sofic")
        graph = compile_at(self.root)
        pairs = {frozenset((a, b)) for a, b, _ in cairn.duplicate_findings(graph)}
        self.assertIn(frozenset(("a-positive", "c-copy")), pairs)
        self.assertNotIn(frozenset(("a-positive", "z-negative")), pairs)
        r = run_cli(self.root, "search", "no non-hyperlinear")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.splitlines()[0].startswith("z-negative"), r.stdout)


class CycleTests(Project):
    def test_two_claim_ring_is_equivalence_only_when_both_edges_are_unary(self):
        claim(self.root, "alpha", "Alpha conclusion", root_=True)
        claim(self.root, "beta", "Beta premise")
        claim(self.root, "gamma", "Gamma extra premise")
        route(self.root, "alpha-from-beta-gamma", "Alpha from beta and gamma",
              "alpha", ["beta", "gamma"])
        route(self.root, "beta-from-alpha", "Beta from alpha", "beta", ["alpha"])
        graph = compile_at(self.root)
        self.assertTrue(any(rule == "cycle" for _, rule, _ in graph.errors), graph.errors)
        claim(self.root, "killer", "The long alpha route fails",
              invalidates=["alpha-from-beta-gamma"])
        route(self.root, "prove-killer", "Proof of route failure", "killer", [])
        graph = compile_at(self.root)
        self.assertEqual(graph.routes["alpha-from-beta-gamma"].status, "INVALIDATED")
        self.assertFalse(any(rule == "cycle" for _, rule, _ in graph.errors), graph.errors)


class FrontierAndLockTests(Project):
    def setUp(self):
        super().setUp()
        claim(self.root, "main-root", "Unrelated root", root_=True)
        claim(self.root, "side-goal", "Side human goal", goal=True)
        claim(self.root, "side-leaf", "Non-hyperlinear side lemma")
        route(self.root, "side-via-leaf", "Reach side goal from its lemma",
              "side-goal", ["side-leaf"])
        claim(self.root, "outside-obstruction", "Unrelated obstruction",
              invalidates=["side-via-leaf"])
        claim(self.root, "settled", "Already settled claim")
        route(self.root, "prove-settled", "Proof of settled claim", "settled", [])

    def test_goal_only_hole_is_actionable_and_unrelated_obstruction_is_not_forced(self):
        graph = compile_at(self.root)
        views, _ = cairn.frontier_view(graph)
        side = next(g for g in views if g["id"] == "side-goal")
        self.assertEqual(side["holes"], ["side-leaf"])
        self.assertTrue(side["connected"])
        self.assertIn("side-leaf", side["necessary"])
        self.assertIn("side-leaf", cairn.actionable_frontier(graph))
        r = run_cli(self.root, "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("side-leaf", r.stdout)
        self.assertNotIn("route-finding needed", r.stdout)
        r = run_cli(self.root, "check")
        self.assertEqual(r.returncode, 0, r.stderr)
        frontier = (self.root / "research" / "FRONTIER.md").read_text(encoding="utf-8")
        self.assertIn("side-leaf", frontier)
        self.assertIn("side-via-leaf", frontier)
        before = frontier
        write_node(self.root, "bad-key", "claim", "Bad key", status="established")
        r = run_cli(self.root, "check")
        self.assertEqual(r.returncode, cairn.EXIT_INVALID, (r.stdout, r.stderr))
        after = (self.root / "research" / "FRONTIER.md").read_text(encoding="utf-8")
        self.assertEqual(after, before)

    def test_locks_only_reserve_real_open_claims_and_ids_cannot_escape_state_dir(self):
        r = run_cli(self.root, "lock", "side-leaf", "--ttl", "5m")
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        self.assertEqual(run_cli(self.root, "unlock", "side-leaf").returncode, 0)
        r = run_cli(self.root, "lock", "side-via-leaf")
        self.assertEqual(r.returncode, 1)
        self.assertIn("route", r.stderr)
        r = run_cli(self.root, "lock", "settled")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already established", r.stderr)
        r = run_cli(self.root, "lock", "side-lef")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nearest:", r.stderr)
        self.assertIn("side-leaf", r.stderr)
        r = run_cli(self.root, "unlock", "../escape")
        self.assertEqual(r.returncode, 1)
        self.assertIn("malformed node id", r.stderr)


class CounterfactualTests(Project):
    def _nonmonotone_fixture(self):
        claim(self.root, "seed", "Seed theorem")
        route(self.root, "seed-proof", "Proof of seed", "seed", [])
        claim(self.root, "killer", "Obstruction to the direct target route",
              invalidates=["blocked-proof"])
        route(self.root, "killer-from-seed", "Seed establishes obstruction",
              "killer", ["seed"])
        claim(self.root, "target", "Target theorem", root_=True)
        route(self.root, "blocked-proof", "Direct target proof", "target", [])
        claim(self.root, "antidote", "Antidote to the obstruction",
              invalidates=["killer-from-seed"])
        claim(self.root, "standalone", "Disconnected canonical claim")

    def test_impact_why_and_site_report_retractions_and_reactivations(self):
        self._nonmonotone_fixture()
        r = run_cli(self.root, "impact", "antidote", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertIn("target", d["would_establish"])
        self.assertIn("killer", d["would_unestablish"])
        self.assertIn("killer-from-seed", d["would_invalidate"])
        self.assertIn("blocked-proof", d["would_reactivate"])
        r = run_cli(self.root, "why", "antidote")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("retracts established: killer", r.stdout)
        self.assertIn("reactivates routes: blocked-proof", r.stdout)
        r = run_cli(self.root, "site")
        self.assertEqual(r.returncode, 0, r.stderr)
        index = (self.root / ".cairn" / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('"lost": ["killer"]', index)
        self.assertIn('"reopened": ["blocked-proof"]', index)
        self.assertIn("retracts", index)
        self.assertIn("reopens", index)
        self.assertIn("d.gone=d.hidden", index)
        self.assertNotIn("d.orphan=d.type==='claim'", index)

    def test_unstable_counterfactual_is_never_presented_as_a_fact(self):
        claim(self.root, "trigger", "Trigger", root_=True)
        claim(self.root, "xx", "X obstruction", invalidates=["r-y"])
        claim(self.root, "yy", "Y obstruction", invalidates=["r-x"])
        route(self.root, "r-x", "Direct X", "xx", [])
        route(self.root, "r-y", "Trigger yields Y", "yy", ["trigger"])
        graph = compile_at(self.root)
        self.assertFalse(any(rule == "stratification" for _, rule, _ in graph.errors))
        r = run_cli(self.root, "impact", "trigger", "--json")
        self.assertEqual(r.returncode, 1)
        self.assertIn("stable invalidation fixpoint", r.stdout)
        r = run_cli(self.root, "why", "trigger")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no stable invalidation fixpoint", r.stdout)
        r = run_cli(self.root, "site")
        self.assertEqual(r.returncode, 0, r.stderr)
        index = (self.root / ".cairn" / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('"unstable": true', index)


if __name__ == "__main__":
    unittest.main(verbosity=2)
