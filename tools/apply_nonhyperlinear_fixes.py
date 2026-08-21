#!/usr/bin/env python3
from pathlib import Path
import re

CORE = Path('cairn.py')
text = CORE.read_text(encoding='utf-8')


def replace_once(old, new, label):
    global text
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected exactly one match, got {n}')
    text = text.replace(old, new, 1)


def sub_once(pattern, repl, label, flags=0):
    global text
    new, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if n != 1:
        raise SystemExit(f'{label}: expected exactly one match, got {n}')
    text = new


replace_once('__version__ = "2.8.1"', '__version__ = "2.8.2"', 'core version')
replace_once(
'''STOPWORDS = {"the", "and", "for", "with", "from", "into", "are", "not",
             "its", "this", "that", "one", "two", "via", "under", "over"}
''',
'''TEXT_STOPWORDS = {"the", "and", "for", "with", "from", "into", "are",
                  "its", "this", "that", "one", "two", "via", "under", "over"}
''',
'search stopwords')
if text.count('w not in STOPWORDS') != 2:
    raise SystemExit(f'text stopword uses: expected 2, got {text.count("w not in STOPWORDS")}')
text = text.replace('w not in STOPWORDS', 'w not in TEXT_STOPWORDS')
replace_once(
'''STOPWORDS = set(
    "a an and are as at be been but by can does do each every for from has "
    "have if in into is it its no not of on or over so some that the then "
    "there these this to under up was were where which while with without "
    "iff only if_and_only_if all any".split()) - {"in", "iff", "to"}
''',
'''MATH_STOPWORDS = set(
    "a an and are as at be been but by can does do each every for from has "
    "have if in into is it its no not of on or over so some that the then "
    "there these this to under up was were where which while with without "
    "iff only if_and_only_if all any".split()) - {"in", "iff", "to"}
''',
'math stopwords')
replace_once('elif w.lower() in STOPWORDS:', 'elif w.lower() in MATH_STOPWORDS:',
             'math stopword use')
sub_once(
    r'''NEGATIONS = \{"not", "non", "never", "fails", "failure", "without", "false",\n             "counterexample", "refuted", "impossible", "obstruction"\}\n\n\ndef _negated\(text\):\n    """.*?\n    return bool\(NEGATIONS & set\(re\.findall\(r"\[a-z0-9\]\+", text\.lower\(\)\)\)\)\n''',
'''NEGATIONS = {"no", "not", "non", "never", "cannot", "neither", "nor",
             "fails", "failure", "without", "false", "counterexample",
             "refuted", "impossible", "obstruction"}


def _negation_signature(text):
    """Logical-negation markers, with multiplicity.

    A boolean is not enough: ``non-hyperlinear`` and ``no non-hyperlinear``
    both contain a negative-looking token, but the second adds a genuine
    proposition-level negation. Duplicate/restatement checks must preserve
    that difference rather than normalising it away.
    """
    norm = re.sub(r"n['’]t\\b", " not", text.lower())
    counts = {}
    for tok in re.findall(r"[a-z0-9]+", norm):
        if tok in NEGATIONS:
            counts[tok] = counts.get(tok, 0) + 1
    return tuple(sorted(counts.items()))
''',
    'negation signature', flags=re.S)
if text.count('_negated(') != 2:
    raise SystemExit(f'negation callers: expected 2, got {text.count("_negated(")}')
text = text.replace('_negated(', '_negation_signature(')
sub_once(
    r'''    def _cycle_check\(self\):\n.*?(?=    def to_json\(self\):)''',
'''    def _cycle_check(self):
        adj = {}
        for r in self.routes.values():
            if r.status == "INVALIDATED":
                continue
            tgt = r.meta.get("target")
            if tgt in self.claims:
                for q in r.get_list("requires"):
                    adj.setdefault(tgt, set()).add(q)
        color, cyc = {c: 0 for c in self.claims}, []

        def dfs(u, path):
            color[u] = 1
            for v in adj.get(u, ()):
                if color.get(v) == 1:
                    cyc.append(path[path.index(v):] + [v] if v in path else [u, v])
                elif color.get(v) == 0:
                    dfs(v, path + [v])
            color[u] = 2

        for c in self.claims:
            if color[c] == 0:
                dfs(c, [c])

        def live_unary(target, req):
            return any(r.status != "INVALIDATED"
                       and r.meta.get("target") == target
                       and r.get_list("requires") == [req]
                       for r in self.routes.values())

        seen = set()
        for path in cyc:
            ring = set(path)
            key = frozenset(ring)
            if key in seen:
                continue
            seen.add(key)
            if len(ring) == 2:
                a, b = sorted(ring)
                if live_unary(a, b) and live_unary(b, a):
                    continue
            self.errors.append(("warning", "cycle",
                                f"dependency cycle through claims: {' -> '.join(path)}"))

''',
    'cycle checker', flags=re.S)
replace_once(
'''def _lock_path(nid):
    return os.path.join(lock_dir(), f"{nid}.json")
''',
'''def _lock_path(nid):
    if not isinstance(nid, str) or ID_RE.fullmatch(nid) is None:
        raise SystemExit(f"malformed node id {nid!r}")
    return os.path.join(lock_dir(), f"{nid}.json")
''',
'lock path validation')
replace_once(
'''    for fn in sorted(os.listdir(lock_dir())):
        if fn.endswith(".json"):
            lock = read_lock(fn[:-5])
            if lock:
                out[fn[:-5]] = lock
''',
'''    for fn in sorted(os.listdir(lock_dir())):
        nid = fn[:-5] if fn.endswith(".json") else ""
        if ID_RE.fullmatch(nid) is not None:
            lock = read_lock(nid)
            if lock:
                out[nid] = lock
''',
'all_locks validation')
sub_once(
    r'''def undecomposed_open\(graph\):\n.*?(?=def lock_attempts\(\):)''',
'''def undecomposed_open(graph):
    """Open claims with no live route into them — holes, independent of
    root-reachability (a goal's cone may extend past the root cover)."""
    out = []
    for cid in sorted(graph.claims):
        c = graph.claims[cid]
        if c.status != "OPEN":
            continue
        if not any(graph.routes[rid].status != "INVALIDATED"
                   for rid in graph.routes_into.get(cid, [])):
            out.append(cid)
    return out


def actionable_frontier(graph):
    """Root frontier plus undecomposed holes in every open goal cone."""
    holes = set(graph.frontier)
    undecomposed = set(undecomposed_open(graph))
    for gid in graph.goals:
        if graph.claims[gid].status == "OPEN":
            holes.update(goal_cone(graph, gid) & undecomposed)
    return sorted(holes, key=lambda h: (-graph.claim_impact[h], h))


def frontier_view(graph, only_goal=None, with_necessity=True):
    """Group open holes by the goals they can serve.

    Necessity is an all-other-holes counterfactual only for monotone cones.
    If a live cone contains an invalidating claim, forcing every leaf can itself
    close a route; in that case Cairn lists the holes but deliberately does not
    label them necessary or claim that the mapped goal is disconnected.
    """
    holes = undecomposed_open(graph)
    gids = [only_goal] if only_goal else graph.goals
    goals, covered = [], set()
    for gid in gids:
        c = graph.claims[gid]
        g = {"id": gid, "node_status": c.status, "holes": [],
             "necessary": set(), "connected": None,
             "obstruction_sensitive": False,
             "counterfactual_unstable": False}
        covered.add(gid)
        if c.status == "OPEN":
            cone = goal_cone(graph, gid)
            cone_holes = [h for h in holes if h in cone and h != gid]
            covered.update(cone_holes)
            g["holes"] = sorted(cone_holes,
                                key=lambda h: (-graph.claim_impact[h], h))
            g["obstruction_sensitive"] = any(
                graph.claims[q].get_list("invalidates") for q in cone)
            if cone_holes and with_necessity and not g["obstruction_sensitive"]:
                cone_set = set(cone_holes)
                base, _, _, stable = graph._solve(forced=frozenset(cone_set))
                if not stable:
                    g["counterfactual_unstable"] = True
                else:
                    g["connected"] = gid in base
                    if g["connected"]:
                        for h in cone_holes:
                            est, _, _, stable = graph._solve(
                                forced=frozenset(cone_set - {h}))
                            if not stable:
                                g["counterfactual_unstable"] = True
                                continue
                            if gid not in est:
                                g["necessary"].add(h)
                g["holes"] = sorted(
                    cone_holes,
                    key=lambda h: (h not in g["necessary"],
                                   -graph.claim_impact[h], h))
        goals.append(g)
    elsewhere = sorted((h for h in graph.frontier if h not in covered),
                       key=lambda h: (-graph.claim_impact[h], h))
    return goals, elsewhere


''',
    'frontier helpers', flags=re.S)
sub_once(
    r'''def generate_frontier_md\(graph, locks\):\n.*?(?=\n\n# ---------------------------------------------------------------------------\n# Static site)''',
'''def generate_frontier_md(graph, locks):
    L = ["# Research frontier", "",
         "<!-- GENERATED by `bin/cairn check` — do not edit by hand. -->",
         "<!-- Source of truth: research/*.md -->", ""]
    est = sum(1 for c in graph.claims.values() if c.status == "ESTABLISHED")
    display_frontier = actionable_frontier(graph)
    L.append(f"{len(graph.claims)} claims ({est} established) · "
             f"{len(graph.routes)} routes "
             f"({len(graph.invalidated)} invalidated) · "
             f"{len(display_frontier)} frontier holes")
    L.append("")
    if graph.goals:
        L += ["## Goals (top-level human goals)", ""]
        for gid in graph.goals:
            c = graph.claims[gid]
            L.append(f"- **{gid}** [{c.status}] [{c.title}]({gid}.md)")
        L.append("")
    anchors = list(graph.roots) + [g for g in graph.goals if g not in graph.roots]
    for root in anchors:
        c = graph.claims[root]
        label = "goal" if root in graph.goals and root not in graph.roots else "root"
        L += [f"## {root} — {c.title}   [{c.status}] ({label})", "", "```text"]
        L += render_tree(graph, root, locks)
        L += ["```", ""]
    views, _ = frontier_view(graph)
    serves = {}
    for g in views:
        for h in g["holes"]:
            serves.setdefault(h, []).append((g["id"], h in g["necessary"]))
    L += ["## Frontier holes (open, undecomposed; roots + goal cones)", ""]
    if not display_frontier:
        L.append("*(none)*")
    for cid in sorted(display_frontier,
                      key=lambda q: (q not in serves, -graph.claim_impact[q], q)):
        c = graph.claims[cid]
        lock = locks.get(cid)
        who = f" — 🔒 claimed ({fmt_remaining(lock)})" if lock else " — unclaimed"
        if cid in serves:
            toward = "; toward: " + ", ".join(
                gid + (" **(necessary)**" if nec else "") for gid, nec in serves[cid])
        elif cid in graph.goals:
            toward = "; a goal with no routes yet — needs decomposition"
        else:
            toward = "; on no live path to a goal"
        last = last_missing_for(graph, cid)
        flag = "".join(
            f" — ⚑ last missing for {rid} → {graph.routes[rid].meta.get('target')}"
            for rid in last[:2])
        L.append(f"- **{cid}** [{graph.claim_impact[cid]} live route(s) need it"
                 f"{toward}] [{c.title}]({cid}.md){flag}{who}")
    internal = set(graph.internal_open)
    undecomp = set(undecomposed_open(graph))
    for gid in graph.goals:
        if graph.claims[gid].status == "OPEN":
            internal.update(q for q in goal_cone(graph, gid)
                            if graph.claims[q].status == "OPEN" and q not in undecomp)
    L += ["", "## Open internal claims (live decompositions exist)", ""]
    L += [f"- {cid} [{graph.claims[cid].title}]({cid}.md)" for cid in sorted(internal)]
    L += ["", "## Recently touched", ""]
    for day, n in recently_touched(graph):
        L.append(f"- {day} · {n.id} [{n.status}] {n.title}")
    L += ["", "## Active claims", ""]
    L += [f"- 🔒 {nid} — {fmt_remaining(lk)}"
          for nid, lk in locks.items()] or ["*(none)*"]
    L.append("")
    return "\\n".join(L)
''',
    'generated frontier', flags=re.S)
replace_once(
'''    data = {"claims": [], "links": [], "junctions": [], "dead": [], "affinity": [],
            "routes": {}, "maxDepth": max(depths.values(), default=0)}
    for cid, c in graph.claims.items():
''',
'''    data = {"claims": [], "links": [], "junctions": [], "dead": [], "affinity": [],
            "routes": {}, "maxDepth": max(depths.values(), default=0)}
    display_frontier = set(actionable_frontier(graph))
    for cid, c in graph.claims.items():
''',
'site frontier setup')
replace_once('"frontier": cid in graph.frontier,', '"frontier": cid in display_frontier,',
             'site frontier flag')
replace_once(
'''    # what an open claim would buy, from the same solver the CLI uses
    for rec in data["claims"]:
        if rec["status"] == "ESTABLISHED":
            continue
        est1, inv1, _, _ = graph._solve(forced=frozenset([rec["id"]]))
        rec["gives"] = {"claims": sorted(est1 - graph.established - {rec["id"]}),
                        "routes": sorted(inv1 - graph.invalidated)}
''',
'''    # what an open claim would buy, from the same solver the CLI uses.
    for rec in data["claims"]:
        if rec["status"] == "ESTABLISHED":
            continue
        est1, inv1, _, stable = graph._solve(forced=frozenset([rec["id"]]))
        if not stable:
            rec["gives"] = {"claims": [], "lost": [], "routes": [],
                            "reopened": [], "unstable": True}
            continue
        rec["gives"] = {
            "claims": sorted(est1 - graph.established - {rec["id"]}),
            "lost": sorted(graph.established - est1),
            "routes": sorted(inv1 - graph.invalidated),
            "reopened": sorted(graph.invalidated - inv1),
            "unstable": False,
        }
''',
'site counterfactual data')
replace_once(
''' const g=d.gives;
 if(g){
  const rows=g.claims.slice(0,12).map(c=>
    `<li><span class="mk ok">unlocks</span>${clink(c)}</li>`);
  if(g.claims.length>12)
   rows.push(`<li class="hint">…and ${g.claims.length-12} more</li>`);
  for(const r of g.routes.slice(0,8))
   rows.push(`<li><span class="mk dead">closes</span>${rlink(r)}</li>`);
  h+=sec('If established',g.claims.length+g.routes.length,rows);
 }
''',
''' const g=d.gives;
 if(g){
  const rows=[];
  if(g.unstable)
   rows.push('<li class="hint">no stable invalidation fixpoint for this counterfactual</li>');
  for(const c of g.claims.slice(0,12))
   rows.push(`<li><span class="mk ok">unlocks</span>${clink(c)}</li>`);
  if(g.claims.length>12)
   rows.push(`<li class="hint">…and ${g.claims.length-12} more unlocked</li>`);
  for(const c of (g.lost||[]).slice(0,12))
   rows.push(`<li><span class="mk dead">retracts</span>${clink(c)}</li>`);
  for(const r of g.routes.slice(0,8))
   rows.push(`<li><span class="mk dead">closes</span>${rlink(r)}</li>`);
  for(const r of (g.reopened||[]).slice(0,8))
   rows.push(`<li><span class="mk ok">reopens</span>${rlink(r)}</li>`);
  h+=sec('If established',rows.length,rows);
 }
''',
'site counterfactual panel')
replace_once(
'''function refreshVis(){
 const sd=document.getElementById('showdead').checked;
 const fold=foldBox.checked;
 // hidden first, degree second: a claim is not an orphan for having only
 // edges to things the fold is currently hiding
 nodes.forEach(d=>{d.hidden=d.type==='group'?(!fold||d.open)
  :d.region?(fold&&!byId[d.region].open):false});
 const shown=l=>{const a=byId[l.source.id||l.source],b=byId[l.target.id||l.target];
  return a&&b&&!a.hidden&&!b.hidden};
 const deg={};
 links.forEach(l=>{if(real(l)&&(!l.dead||sd)&&shown(l)){
  const a=l.source.id||l.source,b=l.target.id||l.target;
  deg[a]=(deg[a]||0)+1;deg[b]=(deg[b]||0)+1}});
 nodes.forEach(d=>{
  d.orphan=d.type==='claim'&&!d.root&&!d.goal&&!d.frontier&&!(deg[d.id]>0);
  d.gone=d.orphan||d.hidden;
 });
''',
'''function refreshVis(){
 const sd=document.getElementById('showdead').checked;
 const fold=foldBox.checked;
 // Every canonical node is drawable. Folding an established region is the
 // only reason to hide one; disconnected claims are data, not UI garbage.
 nodes.forEach(d=>{d.hidden=d.type==='group'?(!fold||d.open)
  :d.region?(fold&&!byId[d.region].open):false});
 nodes.forEach(d=>{d.gone=d.hidden});
''',
'site disconnected visibility')
replace_once('f"{len(graph.routes)} routes · {len(graph.frontier)} frontier holes")',
             'f"{len(graph.routes)} routes · {len(display_frontier)} frontier holes")',
             'site stats frontier')
replace_once(
'''    anchors = sorted(set(new.goals) | set(new.roots))
    if anchors:
''',
'''    anchors = sorted(set(new.goals) | set(new.roots))
    obstruction_sensitive = any(c.get_list("invalidates") for c in new.claims.values())
    if anchors and not obstruction_sensitive:
''',
'kinetic plan cost guard')
replace_once(
'''def cmd_check(args):
    graph, errors = compile_graph()
    changed = changed_research_files()
''',
'''def cmd_check(args):
    graph, errors = compile_graph()
    graph_valid = not any(sev == "error" for sev, _, _ in errors)
    changed = changed_research_files()
''',
'check graph validity')
replace_once('dups = duplicate_findings(graph, only_ids=only)',
             'dups = duplicate_findings(graph, only_ids=only) if graph_valid else []',
             'check duplicate guard')
replace_once('prev = previous_graph(changed)',
             'prev = previous_graph(changed) if graph_valid else None',
             'check previous graph guard')
replace_once('    if graph.detached_tops:\n', '    if graph_valid and graph.detached_tops:\n',
             'check detached guard')
replace_once('delta = kinetic_delta(prev, graph) if prev is not None else None',
             'delta = kinetic_delta(prev, graph) if graph_valid and prev is not None else None',
             'check delta guard')
replace_once(
'''    write_outputs(graph)
    print(f"compiled {len(graph.claims)} claims + {len(graph.routes)} routes -> "
          f".cairn/cache/graph.json, research/FRONTIER.md"
          + ("" if errors else " — check clean"))
''',
'''    if graph_valid:
        write_outputs(graph)
        print(f"compiled {len(graph.claims)} claims + {len(graph.routes)} routes -> "
              f".cairn/cache/graph.json, research/FRONTIER.md"
              + ("" if errors else " — check clean"))
    else:
        print("compile failed; derived outputs left untouched")
''',
'check atomic outputs')
sub_once(
    r'''def cmd_status\(args\):\n.*?(?=\n\ndef stakes_lines\()''',
'''def cmd_status(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    locks = all_locks()
    est = sum(1 for c in graph.claims.values() if c.status == "ESTABLISHED")
    actionable = actionable_frontier(graph)
    L = [f"{len(graph.claims)} claims ({est} established) · "
         f"{len(graph.routes)} routes ({len(graph.invalidated)} invalidated) · "
         f"{len(actionable)} frontier holes · {len(locks)} active claims"]
    if graph.goals:
        L.append("goals:")
        L += [f"  {gid} [{graph.claims[gid].status}] {graph.claims[gid].title}"
              for gid in graph.goals]
    views, _ = frontier_view(graph, with_necessity=False)
    toward_set = {h for g in views for h in g["holes"]}
    toward = sorted(toward_set, key=lambda q: (-graph.claim_impact[q], q))
    pool = toward or graph.frontier
    top = sorted(pool, key=lambda q: (-graph.claim_impact[q], q))[:5]
    if top:
        L.append("frontier (toward goals — `cairn frontier` for the full view):"
                 if toward else "frontier (top impact):")
        L += ["  " + claim_line(graph.claims[q], graph, locks) for q in top]
    if graph.goals and not toward and any(
            graph.claims[g].status == "OPEN" for g in graph.goals):
        L.append("no frontier hole sits on a live path to any open goal — "
                 "route-finding needed (`cairn frontier`)")
    if locks:
        L.append("active locks:")
        L += [f"  🔒 {nid} — {fmt_remaining(lk)}" for nid, lk in locks.items()]
    payload = {"status": "ok", "claims": len(graph.claims), "established": est,
               "routes": len(graph.routes), "invalidated": len(graph.invalidated),
               "frontier": len(actionable), "root_frontier": len(graph.frontier),
               "toward_goals": len(toward),
               "goals": [{"id": g, "node_status": graph.claims[g].status}
                         for g in graph.goals],
               "locks": sorted(locks)}
    return emit(args, payload, "\\n".join(L))
''',
    'status actionable frontier', flags=re.S)
sub_once(
    r'''def stakes_lines\(graph, cid, waiting\):\n.*?(?=\n\ndef cmd_why\()''',
'''def stakes_lines(graph, cid, waiting):
    """Consequences of granting an open claim under the real solver."""
    est2, inv2, _, stable = graph._solve(forced=frozenset({cid}))
    if not stable:
        return ["if established: counterfactual has no stable invalidation fixpoint"]
    live_after = set(graph.routes) - inv2
    completes = [rid for rid in waiting
                 if graph.routes[rid].blocked_on == [cid] and rid in live_after]
    comp_tgts = {graph.routes[rid].meta.get("target") for rid in completes}
    cascade = sorted(est2 - graph.established - {cid} - comp_tgts)
    lost = sorted(graph.established - est2)
    newly_dead = sorted(inv2 - graph.invalidated)
    reopened = sorted(graph.invalidated - inv2)
    gains = []
    if completes:
        gains.append("completes " + ", ".join(
            f"{rid} -> {graph.routes[rid].meta.get('target')}" for rid in completes))
    if cascade:
        gains.append("cascade also establishes: " + ", ".join(cascade))
    if lost:
        gains.append("retracts established: " + ", ".join(lost))
    if newly_dead:
        gains.append("invalidates routes: " + ", ".join(newly_dead))
    if reopened:
        gains.append("reactivates routes: " + ", ".join(reopened))
    if not any(c.get_list("invalidates") for c in graph.claims.values()):
        base = remaining_cost(graph)
        bumped = remaining_cost(graph, assume={cid})
        for gid in sorted(set(graph.goals) | set(graph.roots)):
            if gid == cid:
                continue
            a, b = base.get(gid), bumped.get(gid)
            if a != b and b is not None:
                kind = "goal" if gid in graph.goals else "root"
                was = "no finite mapped plan" if a is None else str(a)
                gains.append(f"{kind} {gid}: cheapest mapped plan {was} -> {b}")
    L = ["if established: " + "; ".join(gains)] if gains else []
    if waiting:
        parts = []
        for rid in waiting:
            tgt = graph.routes[rid].meta.get("target")
            others = [r2 for r2 in graph.routes_into.get(tgt, ())
                      if r2 != rid and graph.routes[r2].status != "INVALIDATED"]
            parts.append(f"{rid} ({tgt} keeps {len(others)} other live route(s))")
        L.append("if refuted (establish the negation): dead-ends "
                 + ", ".join(parts))
    return L
''',
    'non-monotone stakes', flags=re.S)
sub_once(
    r'''def cmd_impact\(args\):\n.*?(?=\n\ndef cmd_lock\()''',
'''def cmd_impact(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    n = graph.nodes.get(args.id)
    if n is None:
        unknown_node(graph, args.id)
    if n.kind != "claim":
        raise SystemExit(f"{args.id!r} is a route; impact takes a claim")
    est1, inv1, _, stable = graph._solve(forced=frozenset([args.id]))
    if not stable:
        raise SystemExit(f"forcing {args.id!r} has no stable invalidation fixpoint")
    newly = sorted(est1 - graph.established - {args.id})
    lost = sorted(graph.established - est1)
    newly_dead = sorted(inv1 - graph.invalidated)
    reopened = sorted(graph.invalidated - inv1)
    direct = [rid for rid in graph.required_by.get(args.id, [])
              if graph.routes[rid].status != "INVALIDATED"]
    L = [f"IF {args.id} WERE ESTABLISHED:"]
    L += [f"  claim flips OPEN -> ESTABLISHED: {c}" for c in newly] or ["  no claims establish downstream"]
    L += [f"  claim flips ESTABLISHED -> OPEN: {c}" for c in lost]
    L += [f"  route becomes INVALIDATED: {r}" for r in newly_dead]
    L += [f"  route becomes LIVE again: {r}" for r in reopened]
    L.append("  live routes directly waiting on it: " + (", ".join(direct) or "(none)"))
    payload = {"status": "ok", "id": args.id, "would_establish": newly,
               "would_unestablish": lost, "would_invalidate": newly_dead,
               "would_reactivate": reopened, "directly_needed_by": direct}
    return emit(args, payload, "\\n".join(L))
''',
    'impact delta', flags=re.S)
sub_once(
    r'''def cmd_lock\(args\):\n.*?(?=\n\ndef cmd_unlock\()''',
'''def cmd_lock(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    n = graph.nodes.get(args.id)
    if n is None:
        unknown_node(graph, args.id)
    if n.kind != "claim":
        raise SystemExit(f"{args.id!r} is a route; lock claims an open claim")
    if n.status != "OPEN":
        raise SystemExit(f"{args.id!r} is already established; lock claims an open claim")
    lock, holder = acquire_lock(args.id, parse_ttl(args.ttl))
    locks = all_locks()
    held = [{"id": nid, "expires_at": lk["expires_at"]}
            for nid, lk in locks.items()]
    roster = ("all active locks: "
              + ", ".join(f"{nid} ({fmt_remaining(lk)})" for nid, lk in locks.items()))
    if lock is None:
        return emit(args, {"status": "claimed", "id": args.id,
                           "expires_at": holder["expires_at"], "locks": held},
                    f"CLAIMED {args.id} — {fmt_remaining(holder)}\\n"
                    f"(locks are identity-free; if this is your own earlier "
                    f"lock it is still active)\\n" + roster,
                    EXIT_LEASE)
    return emit(args, {"status": "locked", "id": args.id,
                       "expires_at": lock["expires_at"], "locks": held},
                f"LOCKED {args.id} "
                f"expires={time.strftime('%H:%M:%S', time.localtime(lock['expires_at']))}"
                f"\\n" + roster)
''',
    'lock command validation', flags=re.S)
replace_once(
'''        gp = {"id": gid, "node_status": c.status, "connected": g["connected"],
              "holes": []}
''',
'''        gp = {"id": gid, "node_status": c.status, "connected": g["connected"],
              "obstruction_sensitive": g.get("obstruction_sensitive", False),
              "counterfactual_unstable": g.get("counterfactual_unstable", False),
              "holes": []}
''',
'frontier json diagnostics')
replace_once(
'''        else:
            if g["connected"] is False:
                L.append("  (no complete route-tree yet: resolving every hole below "
                         "still doesn't reach the goal — a route is missing somewhere)")
''',
'''        else:
            if g.get("obstruction_sensitive"):
                L.append("  (obstruction-sensitive cone: ★ necessity is not inferred "
                         "by forcing every hole at once)")
            elif g.get("counterfactual_unstable"):
                L.append("  (necessity counterfactual has no stable invalidation fixpoint)")
            elif g["connected"] is False:
                L.append("  (no complete route-tree yet: resolving every hole below "
                         "still doesn't reach the goal — a route is missing somewhere)")
''',
'frontier nonmonotone diagnostics')
if re.search(r'(?<![A-Z_])STOPWORDS\b', text):
    raise SystemExit('unqualified STOPWORDS remains after split')
if '_negated(' in text:
    raise SystemExit('_negated caller remains')
CORE.write_text(text, encoding='utf-8')

p = Path('pyproject.toml')
pt = p.read_text(encoding='utf-8')
if pt.count('version = "2.8.1"') != 1:
    raise SystemExit('pyproject version: expected one 2.8.1')
p.write_text(pt.replace('version = "2.8.1"', 'version = "2.8.2"', 1), encoding='utf-8')

tests = Path('tests')
tests.mkdir(exist_ok=True)
Path('tests/test_nonhyperlinear.py').write_text(r'''#!/usr/bin/env python3
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
        claim(self.root, "a-positive", "Every non-hyperlinear group is sofic", root=True)
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
        claim(self.root, "alpha", "Alpha conclusion", root=True)
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
        claim(self.root, "main-root", "Unrelated root", root=True)
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
        self.assertIn("nearest: side-leaf", r.stderr)
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
        claim(self.root, "target", "Target theorem", root=True)
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
        claim(self.root, "trigger", "Trigger", root=True)
        claim(self.root, "x", "X obstruction", invalidates=["r-y"])
        claim(self.root, "y", "Y obstruction", invalidates=["r-x"])
        route(self.root, "r-x", "Direct X", "x", [])
        route(self.root, "r-y", "Trigger yields Y", "y", ["trigger"])
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
''', encoding='utf-8')

ci = Path('.github/workflows/ci.yml')
ct = ci.read_text(encoding='utf-8')
needle = '''      - name: site --serve answers HTTP\n'''
insert = '''      - name: Non-hyperlinear and non-monotone regressions\n        run: python3 tests/test_nonhyperlinear.py\n\n'''
if ct.count(needle) != 1:
    raise SystemExit(f'ci insertion point: expected one match, got {ct.count(needle)}')
ci.write_text(ct.replace(needle, insert + needle, 1), encoding='utf-8')
