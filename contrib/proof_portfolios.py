#!/usr/bin/env python3
"""Compile a Cairn AND/OR research graph into minimal proof portfolios.

A portfolio is an inclusion-minimal set of currently open *leaf* claims such
that establishing every claim in the set is sufficient, through routes already
recorded in Cairn, to establish the requested target.

This is deliberately a compiler analysis rather than a new research ontology:

* established claims compile to the empty obligation;
* an open claim with no live incoming route compiles to the singleton
  obligation containing itself;
* a route is an AND gate, so prerequisite portfolios combine by set union;
* multiple routes into a claim are OR gates, so their portfolios are unioned;
* supersets are eliminated immediately (antichain minimization);
* the least fixed point prevents dependency cycles from manufacturing a
  circular proof;
* each candidate for the target is rechecked with Cairn's own ``Graph._solve``
  because forcing a leaf may establish an obstruction and invalidate a route.

The output is exact up to ``--max-size`` unless ``--state-limit`` is reached;
in that case every displayed portfolio is still valid, but enumeration is
explicitly labelled incomplete.

Usage from a Cairn checkout::

    python3 contrib/proof_portfolios.py --root /path/to/project TARGET
    python3 contrib/proof_portfolios.py --root /path/to/project TARGET --json

No third-party dependencies. Python 3.9+.
"""

import argparse
import json
import os
import sys
from pathlib import Path


def _load_cairn(root):
    if root:
        os.environ["CAIRN_ROOT"] = os.path.abspath(root)
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    import cairn  # pylint: disable=import-outside-toplevel
    return cairn


def _undecomposed_open(cairn, graph):
    # Use Cairn's helper when available; spelling the fallback here keeps this
    # contribution usable against older vendored copies too.
    helper = getattr(cairn, "undecomposed_open", None)
    if helper:
        return helper(graph)
    out = []
    for cid in sorted(graph.claims):
        claim = graph.claims[cid]
        if claim.status != "OPEN":
            continue
        live = any(graph.routes[rid].status != "INVALIDATED"
                   for rid in graph.routes_into.get(cid, []))
        if not live:
            out.append(cid)
    return out


def _portfolio_add(store, candidate, max_size, state_limit):
    """Insert ``candidate`` into an inclusion antichain.

    Returns ``(changed, truncated)``. A plan containing every assumption of
    another plan plus more is dead proof work, so it is discarded immediately.
    """
    candidate = frozenset(candidate)
    if len(candidate) > max_size:
        return False, False
    if any(old <= candidate for old in store):
        return False, False
    for old in [old for old in store if candidate < old]:
        store.remove(old)
    store.add(candidate)
    if len(store) <= state_limit:
        return True, False
    keep = sorted(store, key=lambda p: (len(p), tuple(sorted(p))))[:state_limit]
    store.clear()
    store.update(keep)
    return True, True


def proof_portfolios(cairn, graph, target, max_size=4, state_limit=4096):
    """Return ``(portfolios, truncated)`` for ``target``.

    ``portfolios`` is sorted first by cardinality and then lexicographically.
    It is an inclusion antichain of grounded open-leaf obligations.
    """
    if target not in graph.claims:
        raise KeyError(target)

    leaves = set(_undecomposed_open(cairn, graph))
    plans = {cid: set() for cid in graph.claims}
    for cid, claim in graph.claims.items():
        if claim.status == "ESTABLISHED":
            plans[cid].add(frozenset())
        elif cid in leaves:
            plans[cid].add(frozenset([cid]))

    truncated = False
    changed = True
    for _ in range(max(8, len(graph.claims) + 1)):
        if not changed:
            break
        changed = False
        for route in graph.routes.values():
            if route.status == "INVALIDATED":
                continue
            tgt = route.meta.get("target")
            if tgt not in plans:
                continue
            reqs = [q for q in route.get_list("requires") if q in plans]
            if any(not plans[q] for q in reqs):
                continue

            combos = {frozenset()}
            for q in reqs:
                nxt = set()
                for left in combos:
                    for right in plans[q]:
                        union = left | right
                        if len(union) <= max_size:
                            nxt.add(union)
                combos = nxt
                if not combos:
                    break
                # Keep the Cartesian product itself as an antichain before the
                # next prerequisite is multiplied in.
                small = set()
                for candidate in sorted(
                        combos, key=lambda p: (len(p), tuple(sorted(p)))):
                    if not any(old <= candidate for old in small):
                        small.add(candidate)
                combos = small

            for candidate in combos:
                did, cut = _portfolio_add(
                    plans[tgt], candidate, max_size, state_limit)
                changed |= did
                truncated |= cut
    else:
        # This should not happen for a finite monotone antichain iteration; if
        # it does, never present the result as exhaustive.
        truncated = True

    # Invalidation is non-monotone with respect to the route set: establishing
    # a planned leaf can establish an obstruction. Re-run Cairn's real solver
    # for every candidate, then antichain the survivors once more.
    verified = []
    for candidate in plans[target]:
        established, _, _, _, stable = graph._solve(forced=candidate)
        if stable and target in established:
            verified.append(candidate)

    out = []
    for candidate in sorted(
            verified, key=lambda p: (len(p), tuple(sorted(p)))):
        if not any(old <= candidate for old in out):
            out.append(candidate)
    return out, truncated


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Minimal grounded proof portfolios for a Cairn target")
    parser.add_argument("target", help="claim id to compile")
    parser.add_argument("--root", help="Cairn project root (sets CAIRN_ROOT)")
    parser.add_argument("--max-size", type=int, default=4,
                        help="maximum open leaf facts in a portfolio")
    parser.add_argument("--limit", type=int, default=12,
                        help="portfolios to display")
    parser.add_argument("--state-limit", type=int, default=4096,
                        help="antichain states retained per claim")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.max_size < 0 or args.limit < 1 or args.state_limit < 1:
        parser.error("--max-size must be nonnegative; limits must be positive")

    cairn = _load_cairn(args.root)
    graph, errors = cairn.compile_graph()
    cairn.report_errors(errors, brief=True)
    if args.target not in graph.claims:
        choices = sorted(graph.claims)
        near = [x for x in choices if args.target in x][:5]
        suffix = " — nearby: " + ", ".join(near) if near else ""
        parser.error(f"unknown claim {args.target!r}{suffix}")

    portfolios, truncated = proof_portfolios(
        cairn, graph, args.target, args.max_size, args.state_limit)
    shown = portfolios[:args.limit]
    rows = []
    for rank, plan in enumerate(shown, 1):
        established, _, _, _, _ = graph._solve(forced=plan)
        rows.append({
            "rank": rank,
            "leaves": sorted(plan),
            "size": len(plan),
            "would_establish": len(established - graph.established),
        })

    payload = {
        "status": "ok",
        "target": args.target,
        "target_status": graph.claims[args.target].status,
        "max_size": args.max_size,
        "truncated": truncated,
        "portfolio_count": len(portfolios),
        "portfolios": rows,
    }
    if args.json:
        print(json.dumps(payload, indent=1))
        return 0

    print(f"PORTFOLIOS FOR {args.target} [{payload['target_status']}]")
    print(f"inclusion-minimal open-leaf sets (size <= {args.max_size})")
    if truncated:
        print(f"NOTE: state cap {args.state_limit} reached; valid but incomplete")
    if not rows:
        print("(no grounded portfolio found within the size bound)")
    for row in rows:
        if not row["leaves"]:
            print(f"{row['rank']}. ∅  (already established)")
            continue
        print(f"{row['rank']}. {row['size']} leaf fact(s) · "
              f"{row['would_establish']} claim(s) would establish")
        for cid in row["leaves"]:
            print(f"     - {cid}: {graph.claims[cid].title}")
    if len(portfolios) > len(rows):
        print(f"... {len(portfolios) - len(rows)} more; raise --limit to show")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
