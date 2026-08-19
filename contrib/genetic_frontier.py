#!/usr/bin/env python3
"""Population-genetic diagnostics for a Cairn research frontier.

This contribution treats Cairn's *counterfactual solver* as a fitness assay.
The analogy is intentionally operational rather than decorative:

* a set of granted open leaf claims is a genotype;
* the re-solved Cairn closure is its phenotype;
* newly established target-cone claims are fitness gains;
* claims that become unproved because new obstructions fire are fitness losses;
* pairwise epistasis is measured by consequences that appear only jointly
  (positive) or disappear only jointly (negative);
* an ``outcross`` score rewards positive epistasis between lexically distant
  claims, a cheap proxy for recombining distinct proof niches;
* a balancing-selection panel greedily preserves diverse, nonredundant attack
  lanes instead of sending every worker to the same locally attractive hole.

The script never writes canonical research files and never declares a theorem.
It is a read-only analysis over Cairn's own Graph._solve semantics, so route
invalidations and non-monotone obstruction effects are respected exactly.

Usage from a Cairn checkout::

    python3 contrib/genetic_frontier.py --root /path/to/project TARGET
    python3 contrib/genetic_frontier.py --root /path/to/project TARGET --json

No third-party dependencies. Python 3.9+.
"""

import argparse
import itertools
import json
import math
import os
import re
import sys
from pathlib import Path

TOKEN_RE = re.compile(r"[a-z0-9]+")


def _load_cairn(root):
    if root:
        os.environ["CAIRN_ROOT"] = os.path.abspath(root)
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    import cairn  # pylint: disable=import-outside-toplevel
    return cairn


def _goal_cone(cairn, graph, target):
    helper = getattr(cairn, "goal_cone", None)
    if helper:
        return set(helper(graph, target))
    seen, stack = {target}, [target]
    while stack:
        current = stack.pop()
        for rid in graph.routes_into.get(current, []):
            route = graph.routes[rid]
            if route.status == "INVALIDATED":
                continue
            for req in route.get_list("requires"):
                if req in graph.claims and req not in seen:
                    seen.add(req)
                    stack.append(req)
    return seen


def _open_leaves(cairn, graph):
    helper = getattr(cairn, "undecomposed_open", None)
    if helper:
        return list(helper(graph))
    leaves = []
    for cid in sorted(graph.claims):
        claim = graph.claims[cid]
        if claim.status != "OPEN":
            continue
        if not any(graph.routes[rid].status != "INVALIDATED"
                   for rid in graph.routes_into.get(cid, [])):
            leaves.append(cid)
    return leaves


def _tokens(claim):
    text = f"{claim.id} {claim.title} {claim.body[:2400]}".lower()
    return set(TOKEN_RE.findall(text))


def _distance(tokens_a, tokens_b):
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return 1.0 - len(tokens_a & tokens_b) / len(union)


def _solve(graph, forced):
    established, _, _, stable = graph._solve(forced=frozenset(forced))
    if not stable:
        raise RuntimeError("Cairn solver did not reach a stable fixpoint")
    return set(established)


def _assay(graph, baseline, cone, forced):
    est = _solve(graph, forced)
    new = est - baseline
    lost = baseline - est
    new_cone = new & cone
    lost_cone = lost & cone
    # Remove the granted leaves themselves from the progress score. This makes
    # 'fitness' measure downstream proof consequences rather than rewarding a
    # genotype merely for containing more granted assumptions.
    granted_in_cone = len(set(forced) & cone)
    net_cascade = len(new_cone) - len(lost_cone) - granted_in_cone
    return {
        "established": est,
        "new": new,
        "lost": lost,
        "new_cone": new_cone,
        "lost_cone": lost_cone,
        "net_cascade": net_cascade,
    }


def _single_rows(graph, baseline, cone, candidates):
    rows = {}
    for cid in candidates:
        assay = _assay(graph, baseline, cone, {cid})
        rows[cid] = {
            **assay,
            "impact": int(graph.claim_impact.get(cid, 0)),
            "target_established": None,
        }
    return rows


def _pair_row(graph, baseline, cone, target, a, b, singles, token_cache):
    assay = _assay(graph, baseline, cone, {a, b})
    new_a = singles[a]["new_cone"]
    new_b = singles[b]["new_cone"]
    # Consequences that exist only when both claims are granted are the exact
    # Cairn analogue of positive epistasis. Consequences of either single that
    # disappear in the pair are negative epistasis / incompatibility and can
    # arise because a newly established obstruction invalidates a route.
    emergent = assay["new_cone"] - new_a - new_b
    suppressed = (new_a | new_b) - assay["new_cone"]
    distance = _distance(token_cache[a], token_cache[b])
    target_joint = target in assay["established"]
    target_single = target in singles[a]["established"] or target in singles[b]["established"]
    synthetic_target = target_joint and not target_single
    synergy = len(emergent) - len(suppressed)
    # A ranking, not a theorem: synthetic completion dominates, then exact
    # emergent consequences, then diversity and ordinary impact.
    score = (10000.0 if synthetic_target else 0.0)
    score += 100.0 * synergy
    score += 8.0 * distance
    score += math.log1p(singles[a]["impact"] + singles[b]["impact"])
    score += 0.2 * assay["net_cascade"]
    return {
        "a": a,
        "b": b,
        "score": score,
        "outcross_distance": distance,
        "synthetic_target": synthetic_target,
        "joint_target": target_joint,
        "emergent": sorted(emergent),
        "suppressed": sorted(suppressed),
        "synergy": synergy,
        "joint_net_cascade": assay["net_cascade"],
        "joint_new_cone": len(assay["new_cone"]),
        "joint_lost_cone": len(assay["lost_cone"]),
    }


def _panel(candidates, singles, token_cache, size):
    """Greedy diversity-preserving attack panel (balancing selection)."""
    if not candidates or size <= 0:
        return []
    remaining = set(candidates)
    panel = []
    covered = set()
    while remaining and len(panel) < size:
        best = None
        for cid in sorted(remaining):
            row = singles[cid]
            unique = len(row["new_cone"] - covered)
            novelty = 1.0 if not panel else min(
                _distance(token_cache[cid], token_cache[x["id"]]) for x in panel)
            value = (2.5 * unique + 1.5 * row["net_cascade"] +
                     2.0 * novelty + math.log1p(row["impact"]))
            key = (value, novelty, unique, row["impact"], cid)
            if best is None or key > best[0]:
                best = (key, cid, novelty, unique)
        _, cid, novelty, unique = best
        panel.append({
            "id": cid,
            "novelty": novelty,
            "unique_consequences": unique,
            "net_cascade": singles[cid]["net_cascade"],
            "impact": singles[cid]["impact"],
        })
        covered |= singles[cid]["new_cone"]
        remaining.remove(cid)
    return panel


def analyze(cairn, graph, target, pool=32, pair_limit=15, panel_size=6):
    if target not in graph.claims:
        raise KeyError(target)
    cone = _goal_cone(cairn, graph, target)
    leaves = [cid for cid in _open_leaves(cairn, graph)
              if cid in cone and cid != target]
    leaves.sort(key=lambda cid: (-graph.claim_impact.get(cid, 0), cid))
    candidates = leaves[:pool]
    baseline = set(graph.established)
    singles = _single_rows(graph, baseline, cone, candidates)
    for cid in candidates:
        singles[cid]["target_established"] = target in singles[cid]["established"]
    token_cache = {cid: _tokens(graph.claims[cid]) for cid in candidates}

    pairs = []
    for a, b in itertools.combinations(candidates, 2):
        pairs.append(_pair_row(
            graph, baseline, cone, target, a, b, singles, token_cache))
    pairs.sort(key=lambda row: (
        -int(row["synthetic_target"]), -row["synergy"],
        -row["outcross_distance"], -row["joint_net_cascade"],
        -row["score"], row["a"], row["b"]))

    single_rows = []
    for cid in candidates:
        row = singles[cid]
        single_rows.append({
            "id": cid,
            "title": graph.claims[cid].title,
            "impact": row["impact"],
            "net_cascade": row["net_cascade"],
            "new_cone": len(row["new_cone"]),
            "lost_cone": len(row["lost_cone"]),
            "target_established": row["target_established"],
        })
    single_rows.sort(key=lambda row: (
        -int(row["target_established"]), -row["net_cascade"],
        -row["impact"], row["id"]))

    # Reciprocal sign-epistasis analogue: both singles have nonnegative
    # downstream effect, but together suppress at least one consequence and
    # end up worse than the better single.
    antagonistic = [row for row in pairs
                    if row["suppressed"] and
                    row["joint_net_cascade"] < max(
                        singles[row["a"]]["net_cascade"],
                        singles[row["b"]]["net_cascade"])]
    return {
        "status": "ok",
        "target": target,
        "target_status": graph.claims[target].status,
        "cone_claims": len(cone),
        "open_leaf_count": len(leaves),
        "pool": len(candidates),
        "singles": single_rows,
        "positive_epistasis": pairs[:pair_limit],
        "antagonistic_epistasis": antagonistic[:pair_limit],
        "balancing_panel": _panel(candidates, singles, token_cache, panel_size),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Population-genetic epistasis scan for a Cairn target")
    parser.add_argument("target", help="claim id whose live cone is the landscape")
    parser.add_argument("--root", help="Cairn project root (sets CAIRN_ROOT)")
    parser.add_argument("--pool", type=int, default=32,
                        help="highest-impact open leaves to assay pairwise")
    parser.add_argument("--pairs", type=int, default=15,
                        help="epistatic pairs to display")
    parser.add_argument("--panel", type=int, default=6,
                        help="diverse attack lanes in balancing panel")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.pool < 2 or args.pairs < 1 or args.panel < 1:
        parser.error("--pool >= 2, --pairs >= 1 and --panel >= 1 are required")

    cairn = _load_cairn(args.root)
    graph, errors = cairn.compile_graph()
    cairn.report_errors(errors, brief=True)
    if args.target not in graph.claims:
        parser.error(f"unknown claim {args.target!r}")

    payload = analyze(cairn, graph, args.target, args.pool, args.pairs, args.panel)
    if args.json:
        print(json.dumps(payload, indent=1))
        return 0

    print(f"GENETIC FRONTIER FOR {args.target} [{payload['target_status']}]")
    print(f"{payload['open_leaf_count']} open leaf claims in cone; "
          f"pairwise pool {payload['pool']}")
    print("\nPositive epistasis / outcross candidates")
    if not payload["positive_epistasis"]:
        print("  (none in pool)")
    for rank, row in enumerate(payload["positive_epistasis"], 1):
        tags = []
        if row["synthetic_target"]:
            tags.append("SYNTHETIC TARGET")
        if row["emergent"]:
            tags.append(f"+{len(row['emergent'])} emergent")
        if row["suppressed"]:
            tags.append(f"-{len(row['suppressed'])} suppressed")
        tag = " · " + ", ".join(tags) if tags else ""
        print(f"{rank:2d}. {row['a']} x {row['b']}"
              f"  epistasis={row['synergy']:+d}"
              f"  outcross={row['outcross_distance']:.2f}{tag}")
        for cid in row["emergent"][:4]:
            print(f"      emerges: {cid}")
        for cid in row["suppressed"][:3]:
            print(f"      suppressed: {cid}")

    print("\nBalancing-selection attack panel")
    for rank, row in enumerate(payload["balancing_panel"], 1):
        print(f"{rank:2d}. {row['id']}  novelty={row['novelty']:.2f} "
              f"unique={row['unique_consequences']} "
              f"cascade={row['net_cascade']:+d} impact={row['impact']}")

    if payload["antagonistic_epistasis"]:
        print("\nAntagonistic/sign-epistasis warnings")
        for row in payload["antagonistic_epistasis"][:5]:
            print(f"  {row['a']} x {row['b']}: "
                  f"suppresses {', '.join(row['suppressed'][:4])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
