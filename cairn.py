#!/usr/bin/env python3
"""cairn — a build system whose build targets are unknown facts.

THE KERNEL (schema rg: 2) is two persistent objects and one relation:

  Claim   a mathematical proposition. Unresolved -> a hole/`sorry`;
          established -> a reusable theorem. NOT different object types:
          today's open question is tomorrow's lemma.
  Route   a justified implication  AND(requires) => target. Its existence
          asserts the implication is valid; the body carries the argument.
          requires: [] asserts a COMPLETE DIRECT PROOF of the target.
  invalidates   an ESTABLISHED claim can invalidate routes (obstructions).
  goal: true    marks a claim as a TOP-LEVEL HUMAN GOAL. Pure metadata —
                no effect on compilation — but surfaced everywhere agents
                look (graph.json, FRONTIER.md, context packets, the site).

Everything else falls out:
  Solved(Q) = OR over routes into Q of AND over their requires.
  A reduction is a route with one prerequisite. An equivalence is two
  routes. A proof is a route with no prerequisites. No further ontology.

CANONICAL vs NONCANONICAL:
  research/*.md          claims + routes (flat; `kind:` says which) — the
                         authoritative graph. Agents edit these DIRECTLY
                         with their normal tools; this CLI never writes them.
  research/artifacts/    substantial proof artifacts routes may cite.
  notes/                 scratch, session logs, thinking out loud —
                         searchable, but can NEVER change compiled state,
                         and canonical files may not cite it as justification.

THE CLI is read-only over canonical files and deliberately small —
twelve commands: check (compile+lint+dups, refreshes FRONTIER.md; alias:
build), preview (state delta vs HEAD), status (one-screen program
state), frontier (holes grouped by the goals they serve, necessity
first; --goal for one cone, --flat for the ungrouped list), why
(derivation if established; decomposition + why-it-matters if open),
context --budget (statement, derivation, routes, reusable claims, dead
space in one bounded packet), search [--notes|--similar] (alias:
relevant = search --similar), impact, lock/unlock (advisory TTL claims —
identity-free: everyone is one team), site [--serve], telemetry. Claims
are scheduler state, never committed into mathematical history.

AGENT ERGONOMICS (each of these exists because transcripts showed the
lack of it costing real work): line 1 of `why` is always
`<id> [STATUS] — …` so `| head -1` learns something; query commands
collapse graph warnings to one line (spam trains agents into
`2>/dev/null`, which then eats real errors — `check` prints them all);
`frontier` marks holes on EVERY live path to a goal with ★, prints the
claim-path each hole unblocks, warns when a goal has no route-tree at
all (that means route-finding, not lemma-proving), and annotates holes
that resisted prior locked attempts.

MOMENTUM (the tool's job is to make continuing the default, not a
decision): `check` ends by printing what the change UNLOCKED — new
establishments, routes now one prerequisite from complete, fresh
invalidations, plan-cost movement at goals — because the person who just
placed a stone is the best-positioned to place the next one, and the
moment after a green check is when their context is fully loaded.
Naming a hole is not finishing it: a NEW open claim with no nonempty
`## Attempts` section (one attempted approach and where it dies, or one
line on why the attack is deferred) is a warning, and an error under
`--changed` — writing down where the obvious attack fails is where the
next one usually comes from. New open claims also print their nearest
ESTABLISHED neighbours: a fresh hole adjacent to proved claims is often
already decided by composing them, and only the author, right then, is
positioned to notice. `why` on an open claim prints the stakes both
ways (what establishing completes and cascades; what refuting
dead-ends), so a hole reads as a fork with two prizes. `frontier`,
`status` and FRONTIER.md flag holes that are the LAST missing
prerequisite of some route with ⚑.

Exit categories (stable, for agents): 0 ok, 2 policy findings
(duplicate candidates, new holes parked without an Attempts section),
3 already claimed, 4 invalid graph, 64 usage error, 1 runtime error
(unknown node, bad ttl). All query commands take --json, and with
--json every outcome — including errors — is a JSON envelope on stdout.

ROOT DISCOVERY: the project root is $CAIRN_ROOT if set, else the nearest
ancestor of the working directory containing a research/ directory, else
the working directory itself.

No third-party dependencies. Python 3.9+.
"""

import argparse
import fcntl
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

def _find_root():
    env = os.environ.get("CAIRN_ROOT")
    if env:
        return os.path.abspath(env)
    d = os.getcwd()
    while True:
        if os.path.isdir(os.path.join(d, "research")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.getcwd()
        d = parent


REPO = _find_root()
RESEARCH_DIR = os.path.join(REPO, "research")
NOTES_DIR = os.path.join(REPO, "notes")
STATE_DIR = os.path.join(REPO, ".cairn")
LOCK_DIR = os.path.join(STATE_DIR, "locks")
CACHE_DIR = os.path.join(STATE_DIR, "cache")
SITE_DIR = os.path.join(STATE_DIR, "site")
TELEMETRY = os.path.join(STATE_DIR, "telemetry.jsonl")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
NON_NODE_FILES = {"README.md", "FRONTIER.md"}
KINDS = ("claim", "route")

__version__ = "2.4.0"

EXIT_OK, EXIT_DUP, EXIT_LEASE, EXIT_INVALID, EXIT_USAGE = 0, 2, 3, 4, 64

ALLOWED_KEYS = {
    "claim": {"rg", "id", "kind", "title", "root", "goal",
              "invalidates", "distinct_from", "artifacts"},
    "route": {"rg", "id", "kind", "title", "target", "requires", "artifacts"},
}


def emit(args, payload, human, code=EXIT_OK):
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=1))
    else:
        print(human)
    return code


# ---------------------------------------------------------------------------
# Restricted-YAML frontmatter parser (scalars, inline/block lists, one
# nested map level). Anything fancier is an error, never a guess.
# ---------------------------------------------------------------------------

class FrontmatterError(Exception):
    def __init__(self, path, line, msg):
        super().__init__(f"{path}:{line}: {msg}")


def _scalar(tok):
    tok = tok.strip()
    if tok in ("", "null", "~"):
        return None
    if tok in ("true", "True"):
        return True
    if tok in ("false", "False"):
        return False
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    if re.fullmatch(r"-?\d+", tok):
        return int(tok)
    return tok


def _inline_list(tok):
    inner = tok.strip()[1:-1].strip()
    if not inner:
        return []
    parts, cur, q = [], "", None
    for ch in inner:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "\"'":
            q, cur = ch, cur + ch
        elif ch == ",":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [_scalar(x) for x in parts]


def parse_frontmatter(text, path):
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError(path, 1, "file must start with '---' frontmatter")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise FrontmatterError(path, 1, "unterminated frontmatter")
    body = "\n".join(lines[end + 1:]).strip("\n")
    meta, i = {}, 1
    while i < end:
        raw, ln = lines[i], i + 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw.startswith((" ", "\t")):
            raise FrontmatterError(path, ln, f"unexpected indentation: {raw!r}")
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$", raw)
        if not m:
            raise FrontmatterError(path, ln, f"expected 'key: value', got {raw!r}")
        key, rest = m.group(1), m.group(2).strip()
        if key in meta:
            raise FrontmatterError(path, ln, f"duplicate key {key!r}")
        if rest:
            if rest.startswith("["):
                while not rest.endswith("]") and i + 1 < end:
                    i += 1
                    rest += " " + lines[i].strip()
                if not rest.endswith("]"):
                    raise FrontmatterError(path, ln, f"unterminated flow list for {key!r}")
                meta[key] = _inline_list(rest)
            elif rest.startswith(("{", "|", ">", "&", "*")):
                raise FrontmatterError(path, ln, f"unsupported YAML syntax: {rest!r}")
            else:
                meta[key] = _scalar(rest)
            i += 1
            continue
        items, sub, mode, j = [], {}, None, i + 1
        while j < end:
            r2, ln2 = lines[j], j + 1
            if not r2.strip():
                j += 1
                continue
            if not r2.startswith((" ", "\t")):
                break
            t = r2.strip()
            if t.startswith("- "):
                if mode == "map":
                    raise FrontmatterError(path, ln2, "mixed list and map")
                mode = "list"
                items.append(_scalar(t[2:]))
            else:
                m2 = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$", t)
                if not m2 or mode == "list":
                    raise FrontmatterError(path, ln2, f"expected '- item' or 'sub: value', got {t!r}")
                mode = "map"
                sk, sr = m2.group(1), m2.group(2).strip()
                if sk in sub:
                    raise FrontmatterError(path, ln2, f"duplicate key {sk!r}")
                if sr.startswith("[") and sr.endswith("]"):
                    sub[sk] = _inline_list(sr)
                elif sr == "":
                    raise FrontmatterError(path, ln2, "nesting deeper than two levels")
                else:
                    sub[sk] = _scalar(sr)
            j += 1
        meta[key] = items if mode == "list" else (sub if mode == "map" else None)
        i = j
    return meta, body


# ---------------------------------------------------------------------------
# Loading + linting
# ---------------------------------------------------------------------------

class Node:
    def __init__(self, meta, body, path, kind, relroot):
        self.meta = meta
        self.body = body
        self.path = path
        self.relpath = os.path.relpath(path, relroot)
        self.id = meta.get("id")
        self.kind = kind
        self.title = meta.get("title") or "(untitled)"
        self.status = None
        self.status_reasons = []
        self.blocked_on = []
        self.reachable = False

    def get_list(self, key):
        v = self.meta.get(key)
        return v if isinstance(v, list) else ([] if v is None else [v])


def load_nodes(errors, research_dir=RESEARCH_DIR, relroot=REPO):
    nodes = {}
    if not os.path.isdir(research_dir):
        return nodes
    for fn in sorted(os.listdir(research_dir)):
        if not fn.endswith(".md") or fn in NON_NODE_FILES:
            continue
        path = os.path.join(research_dir, fn)
        rel = os.path.relpath(path, relroot)
        try:
            with open(path, encoding="utf-8") as f:
                meta, body = parse_frontmatter(f.read(), rel)
        except FrontmatterError as e:
            errors.append(("error", str(e)))
            continue
        kind = meta.get("kind")
        if kind not in KINDS:
            errors.append(("error", f"{rel}: kind must be claim|route, got {kind!r}"))
            continue
        node = Node(meta, body, path, kind, relroot)
        nid = meta.get("id")
        if not isinstance(nid, str) or not ID_RE.match(nid):
            errors.append(("error", f"{rel}: missing or malformed id (want a kebab-case slug), got {nid!r}"))
            continue
        if os.path.splitext(fn)[0] != nid:
            errors.append(("error", f"{rel}: filename must equal id ({nid}.md)"))
            continue
        if nid in nodes:
            errors.append(("error", f"{rel}: duplicate id {nid} (also {nodes[nid].relpath})"))
            continue
        if not meta.get("title"):
            errors.append(("error", f"{rel}: missing title"))
        if meta.get("rg") != 2:
            errors.append(("error", f"{rel}: missing or unsupported schema version (want 'rg: 2')"))
        nodes[nid] = node
    return nodes


def lint_nodes(nodes, errors, repo=REPO):
    def ref(node, key, val, want_kind):
        if not isinstance(val, str) or not ID_RE.match(val):
            errors.append(("error", f"{node.relpath}: {key}: malformed id {val!r}"))
        elif val not in nodes:
            errors.append(("error", f"{node.relpath}: {key}: unknown node {val}"))
        elif val == node.id:
            errors.append(("error", f"{node.relpath}: {key}: self-reference"))
        elif nodes[val].kind != want_kind:
            errors.append(("error", f"{node.relpath}: {key}: {val} is a {nodes[val].kind}, want a {want_kind}"))

    for node in nodes.values():
        extra = set(node.meta) - ALLOWED_KEYS[node.kind]
        if extra:
            errors.append(("error", f"{node.relpath}: unknown keys for {node.kind}: {sorted(extra)}"))
        for p in node.get_list("artifacts"):
            if not isinstance(p, str):
                errors.append(("error", f"{node.relpath}: malformed artifact entry: {p!r}"))
                continue
            path_part = p
            if not os.path.exists(os.path.join(repo, p)):
                # a <rev>:<path> entry pins a file that left the working tree
                pinned = (":" in p and subprocess.run(
                    ["git", "-C", repo, "cat-file", "-e", p],
                    capture_output=True).returncode == 0)
                if not pinned:
                    errors.append(("error", f"{node.relpath}: artifact not found: {p} "
                                   "(want a working-tree path or a <rev>:<path> pin)"))
                    continue
                path_part = p.split(":", 1)[1]
            if path_part.startswith("notes/") or "/notes/" in path_part:
                errors.append(("error", f"{node.relpath}: canonical node cites noncanonical justification: {p}"))
        if node.kind == "claim":
            if node.meta.get("root") not in (None, True, False):
                errors.append(("error", f"{node.relpath}: root must be true/false"))
            if node.meta.get("goal") not in (None, True, False):
                errors.append(("error", f"{node.relpath}: goal must be true/false"))
            for r in node.get_list("invalidates"):
                ref(node, "invalidates", r, "route")
            df = node.meta.get("distinct_from")
            if df is not None:
                if not isinstance(df, dict):
                    errors.append(("error", f"{node.relpath}: distinct_from must be a map {{claim-id: why}}"))
                else:
                    for k, why in df.items():
                        if k not in nodes or nodes[k].kind != "claim":
                            errors.append(("error", f"{node.relpath}: distinct_from: unknown claim {k!r}"))
                        if not why:
                            errors.append(("error", f"{node.relpath}: distinct_from: {k} needs a reason"))
        else:
            tgt = node.meta.get("target")
            if not isinstance(tgt, str) or tgt not in nodes or nodes[tgt].kind != "claim":
                errors.append(("error", f"{node.relpath}: target must name an existing claim, got {tgt!r}"))
            if "requires" not in node.meta:
                errors.append(("error", f"{node.relpath}: requires is mandatory "
                               "(requires: [] asserts a complete direct proof)"))
            reqs = node.get_list("requires")
            for q in reqs:
                ref(node, "requires", q, "claim")
            if len(reqs) != len(set(reqs)):
                errors.append(("error", f"{node.relpath}: duplicate entries in requires"))
            if isinstance(tgt, str) and tgt in reqs:
                errors.append(("error", f"{node.relpath}: target appears in its own requires"))
            # restatement dressed as reduction: a single-prerequisite route
            # whose prerequisite reads like its target renames the problem
            if (len(reqs) == 1 and reqs[0] in nodes and isinstance(tgt, str)
                    and tgt in nodes):
                a, b = nodes[reqs[0]], nodes[tgt]
                t = _tokens(a.title + " " + a.id.replace("-", " "))
                u = _tokens(b.title + " " + b.id.replace("-", " "))
                inter = len(t & u)
                if t and u and inter >= 3 and inter / min(len(t), len(u)) >= 0.75:
                    errors.append(("warning", f"{node.relpath}: prerequisite {reqs[0]} "
                                   f"reads like a restatement of target {tgt}; "
                                   "if the route only renames the problem, replace the "
                                   "prerequisite with one that can independently fail"))


# ---------------------------------------------------------------------------
# The compiler: Solved(Q) = OR_routes AND_requires, minus invalidated
# routes; invalidation active only once the invalidating claim is
# ESTABLISHED. Iterated to a mutually consistent fixpoint.
# ---------------------------------------------------------------------------

class Graph:
    def __init__(self, nodes, errors, repo=REPO):
        self.nodes = nodes
        self.errors = errors
        self.claims = {i: n for i, n in nodes.items() if n.kind == "claim"}
        self.routes = {i: n for i, n in nodes.items() if n.kind == "route"}
        self.routes_into = {}     # claim -> [route ids]
        self.required_by = {}     # claim -> [route ids]
        self.invalidated_by = {}  # route -> [established claim ids]
        self.compile()

    def _solve(self, forced=frozenset()):
        """Return (established, invalidated, provenance, stable)."""
        inv_map = {}
        for c in self.claims.values():
            for r in c.get_list("invalidates"):
                if r in self.routes:
                    inv_map.setdefault(c.id, []).append(r)
        prev_inv, seen = set(), []
        for _ in range(64):
            est, prov = set(forced), {}
            changed = True
            while changed:
                changed = False
                for rid, r in self.routes.items():
                    if rid in prev_inv:
                        continue
                    tgt = r.meta.get("target")
                    if tgt not in self.claims or tgt in est:
                        continue
                    reqs = [q for q in r.get_list("requires") if q in self.claims]
                    if all(q in est for q in reqs):
                        est.add(tgt)
                        prov[tgt] = rid
                        changed = True
            inv = {r for c, rs in inv_map.items() if c in est for r in rs}
            if inv == prev_inv:
                return est, inv, prov, True
            if inv in seen:
                return est, inv, prov, False
            seen.append(prev_inv)
            prev_inv = inv
        return est, prev_inv, prov, False

    def compile(self):
        for rid, r in self.routes.items():
            tgt = r.meta.get("target")
            if isinstance(tgt, str) and tgt in self.claims:
                self.routes_into.setdefault(tgt, []).append(rid)
            for q in r.get_list("requires"):
                if q in self.claims:
                    self.required_by.setdefault(q, []).append(rid)

        est, inv, prov, stable = self._solve()
        if not stable:
            self.errors.append(("error", "invalidation is not stratified: establishment and "
                                "invalidation oscillate; break the cycle between an obstruction "
                                "claim and a route it depends on"))
        self.established, self.invalidated, self.provenance = est, inv, prov

        for cid, c in self.claims.items():
            for r in c.get_list("invalidates"):
                if r in self.routes and cid in est:
                    self.invalidated_by.setdefault(r, []).append(cid)

        for cid, c in self.claims.items():
            if cid in est:
                c.status = "ESTABLISHED"
                c.status_reasons = [f"via route {prov[cid]}"] if cid in prov else []
            else:
                c.status = "OPEN"
        for rid, r in self.routes.items():
            if rid in inv:
                r.status = "INVALIDATED"
                r.status_reasons = [f"invalidated by established claim {c}"
                                    for c in self.invalidated_by.get(rid, [])]
            else:
                reqs = [q for q in r.get_list("requires") if q in self.claims]
                r.blocked_on = [q for q in reqs if q not in est]
                r.status = "COMPLETE" if not r.blocked_on else "OPEN"

        # reachability from root claims through non-invalidated routes
        self.roots = [c for c, n in self.claims.items() if n.meta.get("root") is True]
        # human goals: pure metadata, surfaced to agents, no compile effect
        self.goals = sorted(c for c, n in self.claims.items()
                            if n.meta.get("goal") is True)
        stack, seen = list(self.roots), set()
        while stack:
            q = stack.pop()
            if q in seen or q not in self.claims:
                continue
            seen.add(q)
            self.claims[q].reachable = True
            for rid in self.routes_into.get(q, []):
                r = self.routes[rid]
                if r.status == "INVALIDATED":
                    continue
                r.reachable = True
                stack.extend(r.get_list("requires"))

        # frontier: reachable open claims with no live decomposition
        self.frontier, self.internal_open = [], []
        for cid in sorted(self.claims):
            c = self.claims[cid]
            if c.status != "OPEN" or not c.reachable:
                continue
            live_in = [rid for rid in self.routes_into.get(cid, [])
                       if self.routes[rid].status != "INVALIDATED"]
            (self.internal_open if live_in else self.frontier).append(cid)

        # impact metric: how many live routes need this claim
        self.claim_impact = {
            cid: len([rid for rid in self.required_by.get(cid, [])
                      if self.routes[rid].status != "INVALIDATED"])
            for cid in self.claims}

        self.unreachable_open = []
        for cid, c in self.claims.items():
            if c.status == "OPEN" and not c.reachable:
                self.unreachable_open.append(cid)
                self.errors.append(("warning", f"{c.relpath}: {cid} is open but unreachable from any root claim"))
        self._cycle_check()

    def _cycle_check(self):
        adj = {}
        for r in self.routes.values():
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
        for c in cyc:
            self.errors.append(("warning", f"dependency cycle through claims: {' -> '.join(c)}"))

    def to_json(self):
        out = {"generated_by": "cairn build", "rg": 2, "nodes": {}, "derived": {}}
        for i, n in sorted(self.nodes.items()):
            out["nodes"][i] = {"kind": n.kind, "title": n.title, "path": n.relpath,
                               "status": n.status, "status_reasons": n.status_reasons,
                               "blocked_on": n.blocked_on, "reachable": n.reachable,
                               "meta": n.meta}
        out["derived"] = {"roots": self.roots, "goals": self.goals,
                          "frontier": self.frontier,
                          "internal_open": self.internal_open,
                          "claim_impact": self.claim_impact,
                          "provenance": self.provenance}
        return out


def compile_graph(research_dir=RESEARCH_DIR, repo=REPO):
    errors = []
    nodes = load_nodes(errors, research_dir, repo)
    lint_nodes(nodes, errors, repo)
    return Graph(nodes, errors, repo), errors


def report_errors(errors, fail_on_warning=False, brief=False):
    # brief (query commands): errors in full, warnings collapsed to one
    # line. Re-printing every warning on every invocation trains agents
    # to append 2>/dev/null, which then swallows real errors too.
    n = 0
    warnings = [m for s, m in errors if s == "warning"]
    collapse = brief and not fail_on_warning
    for sev, msg in errors:
        if sev == "warning" and collapse:
            continue
        print(f"{sev.upper()}: {msg}", file=sys.stderr)
        n += sev == "error" or (fail_on_warning and sev == "warning")
    if collapse and warnings:
        print(f"({len(warnings)} graph warning(s) — `cairn check` for details)",
              file=sys.stderr)
    return n


# ---------------------------------------------------------------------------
# Similarity / search — transparent token overlap, no magic
# ---------------------------------------------------------------------------

STOPWORDS = {"the", "and", "for", "with", "from", "into", "are", "not",
             "its", "this", "that", "one", "two", "via", "under", "over"}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower())
            if len(w) > 2 and w not in STOPWORDS}


def similar_nodes(text, nodes, kinds=None, limit=5, threshold=0.5, exclude=(),
                  min_overlap=2):
    t = _tokens(text)
    out = []
    for n in nodes.values():
        if (kinds and n.kind not in kinds) or n.id in exclude:
            continue
        u = _tokens(n.title + " " + n.id.replace("-", " "))
        if not t or not u:
            continue
        inter = len(t & u)
        score = inter / min(len(t), len(u))
        if inter >= min_overlap and score >= threshold:
            out.append((round(score, 2), n))
    return sorted(out, key=lambda x: (-x[0], x[1].id))[:limit]


def semantic_vectors(claims):
    """TF-IDF vectors (unigrams + bigrams) over title+body, shared by the
    site layout and the near-established hints so both see one geometry."""
    import math

    def feats(text):
        words = [w for w in re.findall(r"[a-z0-9]+", text.lower())
                 if len(w) > 2 and w not in STOPWORDS]
        return set(words) | {a + "_" + b for a, b in zip(words, words[1:])}

    docs = {cid: feats(c.title + " " + c.body) for cid, c in claims.items()}
    df = {}
    for toks in docs.values():
        for t in toks:
            df[t] = df.get(t, 0) + 1
    N = max(1, len(docs))
    cutoff = 0.35 * N   # program-wide jargon carries no signal
    return {cid: {t: math.log(N / df[t]) for t in toks if df[t] <= cutoff}
            for cid, toks in docs.items()}


def cosine(u, v):
    import math
    if len(v) < len(u):
        u, v = v, u
    num = sum(w * v.get(t, 0.0) for t, w in u.items())
    du = math.sqrt(sum(w * w for w in u.values()))
    dv = math.sqrt(sum(w * w for w in v.values()))
    return num / (du * dv) if du and dv else 0.0


def duplicate_findings(graph, only_ids=None):
    """(claim, candidate, score) triples not answered by distinct_from."""
    out = []
    for cid, c in graph.claims.items():
        if only_ids is not None and cid not in only_ids:
            continue
        df = c.meta.get("distinct_from") or {}
        for score, cand in similar_nodes(c.title + " " + c.id.replace("-", " "),
                                         graph.claims, kinds=("claim",),
                                         threshold=0.5, exclude={cid}):
            if cand.id in df or cid in (cand.meta.get("distinct_from") or {}):
                continue
            if cand.id < cid and (only_ids is None or cand.id in only_ids):
                continue  # report each unordered pair once
            out.append((cid, cand.id, score))
    return out


# ---------------------------------------------------------------------------
# TTL work locks. Scheduler state — never committed, never in the DSL.
# Filesystem backend here; the interface is the contract, the backend can
# become SQLite/service for distributed agents without touching semantics.
# ---------------------------------------------------------------------------

def _lock_path(nid):
    return os.path.join(LOCK_DIR, f"{nid}.json")


def parse_ttl(s):
    m = re.fullmatch(r"(\d+)\s*([smhd])", s.strip())
    if not m:
        raise SystemExit(f"ambiguous ttl {s!r} — give a unit: 900s, 45m, 2h, 1d")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def read_lock(nid):
    try:
        with open(_lock_path(nid), encoding="utf-8") as f:
            lock = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if lock.get("expires_at", 0) <= time.time():
        try:
            os.unlink(_lock_path(nid))
        except FileNotFoundError:
            pass
        return None
    return lock


def all_locks():
    if not os.path.isdir(LOCK_DIR):
        return {}
    out = {}
    for fn in sorted(os.listdir(LOCK_DIR)):
        if fn.endswith(".json"):
            lock = read_lock(fn[:-5])
            if lock:
                out[fn[:-5]] = lock
    return out


def acquire_lock(nid, ttl_seconds):
    # Claims are identity-free and advisory: one team; TTL handles crashes.
    os.makedirs(LOCK_DIR, exist_ok=True)
    now = time.time()
    payload = {"node": nid, "acquired_at": now,
               "ttl_seconds": ttl_seconds, "expires_at": now + ttl_seconds}
    with open(os.path.join(LOCK_DIR, ".mutex"), "w") as mtx:
        fcntl.flock(mtx, fcntl.LOCK_EX)
        existing = read_lock(nid)
        if existing:
            return None, existing
        tmp = _lock_path(nid) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        os.replace(tmp, _lock_path(nid))
    return payload, None


def fmt_remaining(lock):
    rem = int(lock["expires_at"] - time.time())
    return "expired" if rem < 0 else f"{rem // 60}m{rem % 60:02d}s remaining"


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

MARK = {"OPEN": "OPEN", "ESTABLISHED": "✓", "COMPLETE": "✓",
        "INVALIDATED": "✗"}


def render_tree(graph, cid, locks, depth=0, seen=None, lines=None, max_depth=6):
    if lines is None:
        lines, seen = [], set()
    c = graph.claims[cid]
    ind = "  " * depth
    lockmark = " 🔒" if cid in locks else ""
    lines.append(f"{ind}{cid} [{MARK.get(c.status, c.status)}]{lockmark} {c.title}")
    if cid in seen or depth >= max_depth:
        if cid in seen:
            lines.append(f"{ind}  (…already shown)")
        return lines
    seen.add(cid)
    for rid in graph.routes_into.get(cid, []):
        r = graph.routes[rid]
        lines.append(f"{ind}  ├ {rid} [{MARK.get(r.status, r.status)}] {r.title}")
        if r.status == "INVALIDATED":
            for reason in r.status_reasons:
                lines.append(f"{ind}      {reason}")
        else:
            for q in r.get_list("requires"):
                if q in graph.claims:
                    render_tree(graph, q, locks, depth + 2, seen, lines, max_depth)
    return lines


def last_missing_for(graph, cid):
    """Live routes for which this claim is the single open prerequisite."""
    return [rid for rid in graph.required_by.get(cid, [])
            if graph.routes[rid].status != "INVALIDATED"
            and graph.routes[rid].blocked_on == [cid]]


def claim_line(c, graph, locks, width=52):
    lock = locks.get(c.id)
    lockmark = f"  🔒 {fmt_remaining(lock)}" if lock else ""
    last = last_missing_for(graph, c.id) if c.status == "OPEN" else []
    lastmark = ""
    if last:
        more = f" +{len(last) - 1}" if len(last) > 1 else ""
        lastmark = f"  ⚑ last missing for {last[0]}{more}"
    title = c.title if len(c.title) <= width else c.title[:width - 1] + "…"
    return (f"{c.id:<36}  {title:<{width}}  "
            f"{MARK.get(c.status, c.status)}{lastmark}{lockmark}")


def why_chain(graph, cid, limit=8):
    from collections import deque
    anchors = sorted(set(graph.roots) | set(graph.goals))
    prev, dq = {a: None for a in anchors}, deque(anchors)
    while dq:
        u = dq.popleft()
        if u == cid:
            break
        for rid in graph.routes_into.get(u, []):
            r = graph.routes[rid]
            if r.status == "INVALIDATED":
                continue
            for v in r.get_list("requires"):
                if v not in prev:
                    prev[v] = (u, rid)
                    dq.append(v)
    if cid not in prev:
        return None
    chain, cur = [], cid
    while prev.get(cur):
        parent, rid = prev[cur]
        chain.append((parent, rid, cur))
        cur = parent
    chain.reverse()
    return chain[:limit]


def derivation_lines(graph, cid, depth=0, seen=None):
    """Recursive `why established` tree."""
    if seen is None:
        seen = set()
    ind = "  " * depth
    c = graph.claims[cid]
    out = []
    if c.status != "ESTABLISHED":
        out.append(f"{ind}{cid} OPEN")
        return out
    rid = graph.provenance.get(cid)
    if rid is None:
        out.append(f"{ind}{cid} ✓ (assumed)")
        return out
    r = graph.routes[rid]
    reqs = [q for q in r.get_list("requires") if q in graph.claims]
    out.append(f"{ind}{cid} ✓ via {rid}" + ("" if reqs else "  (direct proof)"))
    if cid in seen:
        return out
    seen.add(cid)
    for q in reqs:
        out.extend(derivation_lines(graph, q, depth + 1, seen))
    return out


def goal_cone(graph, gid):
    """Claims on some live route-tree under `gid` — the set whose
    resolution can move `gid` through the routes recorded so far."""
    from collections import deque
    seen, dq = {gid}, deque([gid])
    while dq:
        u = dq.popleft()
        for rid in graph.routes_into.get(u, []):
            r = graph.routes[rid]
            if r.status == "INVALIDATED":
                continue
            for q in r.get_list("requires"):
                if q in graph.claims and q not in seen:
                    seen.add(q)
                    dq.append(q)
    return seen


def chain_to(graph, gid, cid):
    """Shortest live-route claim path cid -> ... -> gid, or None."""
    from collections import deque
    prev, dq = {gid: None}, deque([gid])
    while dq:
        u = dq.popleft()
        if u == cid:
            break
        for rid in graph.routes_into.get(u, []):
            r = graph.routes[rid]
            if r.status == "INVALIDATED":
                continue
            for v in r.get_list("requires"):
                if v in graph.claims and v not in prev:
                    prev[v] = u
                    dq.append(v)
    if cid not in prev:
        return None
    path, cur = [cid], cid
    while prev[cur] is not None:
        cur = prev[cur]
        path.append(cur)
    return path


def undecomposed_open(graph):
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


def frontier_view(graph, only_goal=None, with_necessity=True):
    """Group the open holes by the goals they can serve.

    Per goal: the holes on its live route-trees (its cone), which are
    NECESSARY (granting every other hole still doesn't reach the goal —
    the forced-solve run in reverse), and whether any complete
    route-tree exists at all. Holes in no cone are 'elsewhere': real
    work, but on no recorded path to a goal.
    """
    holes = undecomposed_open(graph)
    hole_set = set(holes)
    gids = [only_goal] if only_goal else graph.goals
    goals, covered = [], set()
    for gid in gids:
        c = graph.claims[gid]
        g = {"id": gid, "node_status": c.status, "holes": [],
             "necessary": set(), "connected": None}
        covered.add(gid)  # a goal is never 'elsewhere'; it gets its own section
        if c.status == "OPEN":
            cone = goal_cone(graph, gid)
            cone_holes = [h for h in holes if h in cone and h != gid]
            covered.update(cone_holes)
            if cone_holes and with_necessity:
                base, _, _, _ = graph._solve(forced=frozenset(holes))
                g["connected"] = gid in base
                if g["connected"]:
                    for h in cone_holes:
                        est, _, _, _ = graph._solve(forced=frozenset(hole_set - {h}))
                        if gid not in est:
                            g["necessary"].add(h)
            g["holes"] = sorted(
                cone_holes,
                key=lambda h: (h not in g["necessary"], -graph.claim_impact[h], h))
        goals.append(g)
    elsewhere = sorted((h for h in graph.frontier if h not in covered),
                       key=lambda h: (-graph.claim_impact[h], h))
    return goals, elsewhere


def lock_attempts():
    """Successful lock acquisitions per node id, from telemetry. Advisory
    color only — telemetry is uncommitted machine state and must never
    affect compiled status."""
    counts = {}
    for e in read_telemetry():
        if e.get("cmd") != "lock" or e.get("exit") != 0:
            continue
        argv, nid, i = e.get("argv", []), None, 1
        while i < len(argv):
            a = argv[i]
            if a == "--ttl":
                i += 2
                continue
            if isinstance(a, str) and not a.startswith("-"):
                nid = a
                break
            i += 1
        if nid:
            counts[nid] = counts.get(nid, 0) + 1
    return counts


def notes_mentioning(nid, limit=5):
    hits = []
    if not os.path.isdir(NOTES_DIR):
        return hits
    for base, _, files in os.walk(NOTES_DIR):
        for fn in files:
            if not fn.endswith((".md", ".txt")):
                continue
            p = os.path.join(base, fn)
            try:
                if nid in open(p, encoding="utf-8", errors="ignore").read():
                    hits.append(os.path.relpath(p, REPO))
            except OSError:
                pass
    return sorted(hits)[:limit]


def unknown_node(graph, nid):
    """Exit with near-miss suggestions instead of a bare 'unknown node'."""
    sugg = [n.id for _, n in similar_nodes(nid.replace("-", " "), graph.nodes,
                                           limit=3, threshold=0.25, min_overlap=1)]
    msg = f"unknown node {nid!r}"
    if sugg:
        msg += " — nearest: " + ", ".join(sugg)
    raise SystemExit(msg)


def context_packet(graph, nid, locks, budget_tokens=8000):
    n = graph.nodes.get(nid)
    if n is None:
        unknown_node(graph, nid)
    budget = budget_tokens * 4  # rough chars
    sections = []

    def sec(title, lines):
        if lines:
            sections.append((title, lines))

    head = [f"=== CONTEXT: {nid} ===",
            f"KIND: {n.kind}   STATUS: {n.status}"
            + (f"   ({'; '.join(n.status_reasons)})" if n.status_reasons else "")]
    if n.meta.get("goal") is True:
        head.append("GOAL: this claim is a top-level human goal of the program")
    lock = locks.get(nid)
    if lock:
        head.append(f"LOCK: 🔒 claimed ({fmt_remaining(lock)}) — someone is on this")
    sec("", head)
    sec("STATEMENT", [n.body or "(no body)"])

    if n.kind == "claim":
        if n.status == "ESTABLISHED":
            sec("DERIVATION", derivation_lines(graph, nid))
        chain = why_chain(graph, nid)
        if chain:
            sec("WHY THIS MATTERS", [" -> ".join([chain[0][0]] + [c for _, _, c in chain])])
        rin = []
        for rid in graph.routes_into.get(nid, []):
            r = graph.routes[rid]
            rin.append(f"{rid} [{r.status}] {r.title}")
            if r.status == "INVALIDATED":
                rin += [f"    {x}" for x in r.status_reasons]
            else:
                for q in r.get_list("requires"):
                    rin.append(f"    requires {q} [{graph.claims[q].status}]")
        sec("ROUTES INTO THIS CLAIM", rin)
        rneed = [f"{rid} -> {graph.routes[rid].meta.get('target')} "
                 f"[{graph.routes[rid].status}] {graph.routes[rid].title}"
                 for rid in graph.required_by.get(nid, [])]
        sec("ROUTES THAT NEED THIS CLAIM", rneed)
        reusable = [f"{c.id}  {c.title}" for _, c in
                    similar_nodes(n.title + " " + n.body[:400], graph.claims,
                                  kinds=("claim",), threshold=0.34, exclude={nid})
                    if c.status == "ESTABLISHED"]
        sec("REUSABLE ESTABLISHED CLAIMS (similarity)", reusable)
        dead = []
        for rid in graph.routes_into.get(nid, []):
            r = graph.routes[rid]
            if r.status == "INVALIDATED":
                dead.append(f"{rid}: {'; '.join(r.status_reasons)}")
        sec("NEARBY FAILED SPACE (do not redo)", dead)
    else:
        tgt = n.meta.get("target")
        body = [f"target {tgt} [{graph.claims[tgt].status}]"] if tgt in graph.claims else []
        body += [f"requires {q} [{graph.claims[q].status}]"
                 for q in n.get_list("requires") if q in graph.claims]
        sec("IMPLICATION", body)
        if n.status == "INVALIDATED":
            sec("INVALIDATED", n.status_reasons)

    files = [n.relpath] + [p for p in n.get_list("artifacts")]
    if n.kind == "claim":
        files += [graph.routes[rid].relpath for rid in graph.routes_into.get(nid, [])
                  if graph.routes[rid].status != "INVALIDATED"]
    sec("CANONICAL MATERIAL TO READ", files)
    sec("OPTIONAL OLD NOTES", notes_mentioning(nid))

    out, used = [], 0
    for title, lines in sections:
        chunk = ("\n" + title + "\n" if title else "") + "\n".join(lines)
        if used + len(chunk) > budget and out:
            out.append(f"\n[... truncated to --budget {budget_tokens} tokens]")
            break
        out.append(chunk)
        used += len(chunk)
    return "\n".join(out)


def generate_frontier_md(graph, locks):
    L = ["# Research frontier", "",
         "<!-- GENERATED by `bin/cairn check` — do not edit by hand. -->",
         "<!-- Source of truth: research/*.md -->", ""]
    est = sum(1 for c in graph.claims.values() if c.status == "ESTABLISHED")
    L.append(f"{len(graph.claims)} claims ({est} established) · "
             f"{len(graph.routes)} routes "
             f"({len(graph.invalidated)} invalidated) · "
             f"{len(graph.frontier)} frontier holes")
    L.append("")
    if graph.goals:
        L += ["## Goals (top-level human goals)", ""]
        for gid in graph.goals:
            c = graph.claims[gid]
            L.append(f"- **{gid}** [{c.status}] [{c.title}]({gid}.md)")
        L.append("")
    for root in graph.roots:
        c = graph.claims[root]
        L += [f"## {root} — {c.title}   [{c.status}]", "", "```text"]
        L += render_tree(graph, root, locks)
        L += ["```", ""]
    serves = {}  # hole -> [(goal, necessary)] — which goals each hole can move
    for g in frontier_view(graph)[0]:
        for h in g["holes"]:
            serves.setdefault(h, []).append((g["id"], h in g["necessary"]))
    L += ["## Frontier holes (open, reachable, undecomposed)", ""]
    if not graph.frontier:
        L.append("*(none)*")
    for cid in sorted(graph.frontier,
                      key=lambda q: (q not in serves, -graph.claim_impact[q])):
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
    L += ["", "## Open internal claims (live decompositions exist)", ""]
    L += [f"- {cid} [{graph.claims[cid].title}]({cid}.md)" for cid in graph.internal_open]
    L += ["", "## Recently touched", ""]
    recent = sorted(graph.nodes.values(), key=lambda n: -os.path.getmtime(n.path))[:8]
    for n in recent:
        day = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(n.path)))
        L.append(f"- {day} · {n.id} [{n.status}] {n.title}")
    L += ["", "## Active claims", ""]
    L += [f"- 🔒 {nid} — {fmt_remaining(lk)}"
          for nid, lk in locks.items()] or ["*(none)*"]
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Static site (human display is downstream of the kernel)
# ---------------------------------------------------------------------------

STATUS_COLOR = {"OPEN": "#c08a00", "ESTABLISHED": "#178a5e",
                "COMPLETE": "#178a5e", "INVALIDATED": "#c43c2e"}
GOAL_COLOR = "#4f46e5"
SANS = 'Arial,Helvetica,ui-sans-serif,system-ui,sans-serif'
MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,monospace'
# One ink on pure paper: every non-status colour in the site is this hue at
# some alpha, which is what keeps a dense graph from turning into confetti.
INK = "#171714"
PALETTE = """--paper:#fff;--ink:#171714;--panel:#fcfcfb;
--line:#17171426;--line2:#17171433;--rule:#1717144d;
--mut:#171714a8;--mut2:#17171473;--accent:#a33a1c;
--est:#178a5e;--open:#c08a00;--dead:#c43c2e;--goal:#4f46e5;--edge:#17171459"""
SITE_CSS = """
:root{PALETTE;color-scheme:light}
html{background:var(--paper)}
body{font:15px/1.6 SANS;max-width:52em;margin:0 auto;padding:2.6em 1.4em 6em;
color:var(--ink);background:var(--paper);font-synthesis:none;
text-rendering:geometricPrecision;-webkit-font-smoothing:antialiased}
a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--rule)}
a:hover{color:var(--accent);border-bottom-color:var(--accent)}
code,pre{font:12.5px/1.5 MONO;background:var(--panel)}
code{border:1px solid var(--line);padding:.05em .3em}
pre{padding:.9em 1.1em;overflow-x:auto;border:1px solid var(--line)}
pre code{border:0;padding:0;background:none}
.badge{display:inline-block;padding:.15em .6em;color:#fff;font:700 10px SANS;
letter-spacing:.08em}
.node{font:12.5px MONO}
h1{font-size:clamp(1.7rem,3.4vw,2.5rem);font-weight:500;letter-spacing:-.035em;
line-height:1.06;margin:0 0 .5em;max-width:22ch}
h1 .node{display:block;font-size:.5em;letter-spacing:0;color:var(--mut);
margin-bottom:.5em}
h2{font-size:.72rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
color:var(--mut2);margin:2.6em 0 .6em;padding-bottom:.4em;
border-bottom:1px solid var(--line)}
h3,h4{font-size:1rem;font-weight:700;letter-spacing:-.01em;margin:1.8em 0 .4em}
p{max-width:78ch;text-wrap:pretty}
ul.rel{list-style:none;padding-left:0}ul.rel li{margin:.45em 0;max-width:78ch}
.muted{color:var(--mut)}
.tree{white-space:pre;font:12.5px/1.55 MONO;background:var(--panel);
border:1px solid var(--line);padding:1.1em;overflow-x:auto}
table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid var(--line);padding:.45em .7em;font-size:.88em;
text-align:left}
th{font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:var(--mut2)}
tr:hover td{background:var(--panel)}
nav.top{display:flex;gap:1.4em;align-items:baseline;margin-bottom:3em;
font-size:.72rem;letter-spacing:.14em;text-transform:uppercase}
nav.top a{border:0;color:var(--mut2)}nav.top a:hover{color:var(--accent)}
.art{font:12.5px MONO;word-break:break-all}
.katex{font-size:1.03em}
.katex-display{overflow-x:auto;overflow-y:hidden;padding:.25em 0;margin:.5em 0;
text-align:left}
.katex-display>.katex{text-align:left}
.mathblock{overflow-x:auto;margin:1.2em 0;padding:.7em 1.1em;
background:var(--panel);border:1px solid var(--line)}
.texd{display:block}
.texfail{font:12.5px MONO;background:var(--panel);border:1px solid var(--line);
padding:.05em .3em}
a.fileref{color:var(--ink);border-bottom:1px solid var(--rule);
text-decoration:none;word-break:break-word}
a.fileref:hover{color:var(--accent);border-bottom-color:var(--accent)}
code a{color:inherit;border-bottom:1px solid var(--rule)}
code a:hover{color:var(--accent)}
pre.src{font:12px/1.55 MONO;background:var(--panel);border:1px solid var(--line);
padding:1em 0;overflow-x:auto;counter-reset:none}
pre.src .ln{display:inline-block;width:4.2em;padding-right:1.1em;text-align:right;
color:var(--mut2);user-select:none}
pre.src .ln:target{color:var(--accent);font-weight:700}
""".replace("SANS", SANS).replace("MONO", MONO).replace("PALETTE", PALETTE)
# Typeset on demand: `cairnTypeset(el)` is safe to call before KaTeX has
# loaded (it re-runs on the script's load event) and never throws on a bad
# expression -- a malformed formula in one node must not blank the page.
KATEX_OPTS_JS = """
window.KATEX_DELIMS=[{left:"$$",right:"$$",display:true},
 {left:"\\\\[",right:"\\\\]",display:true},
 {left:"\\\\(",right:"\\\\)",display:false},
 {left:"$",right:"$",display:false}];
window.cairnTypeset=function(el){
 if(!el)return;
 // Pre-translated shorthand: each span carries TeX, and its original text in
 // data-src so a refusal degrades to exactly what the author typed.
 if(typeof katex!=='undefined'){
  var ns=el.querySelectorAll('.tex,.texd');
  for(var i=0;i<ns.length;i++){
   var n=ns[i];
   if(n.getAttribute('data-done'))continue;
   n.setAttribute('data-done','1');
   try{katex.render(n.textContent,n,{displayMode:n.classList.contains('texd'),
    throwOnError:true,strict:false,trust:false});}
   catch(e){n.textContent=n.getAttribute('data-src')||n.textContent;
    n.className='texfail';}
  }
 }
 // Anyone who does write real TeX delimiters still gets them rendered.
 if(typeof renderMathInElement==='function'){
  try{renderMathInElement(el,{delimiters:window.KATEX_DELIMS,throwOnError:false,
   ignoredTags:["script","noscript","style","textarea","pre","code","option"],
   errorColor:"#a33a1c"});}catch(e){}
 }
};
window.addEventListener('DOMContentLoaded',function(){
 var go=function(){cairnTypeset(document.body)};
 if(typeof renderMathInElement==='function')go();else window.addEventListener('load',go);
});
"""
# Search is over ids, titles and rendered statements at once, ranked so that
# an exact id beats a prefix beats a title hit beats a body hit -- typing a
# slug goes straight there, typing a phrase finds the node that argues it.
SEARCH_JS = r"""
(function(){
var $=function(i){return document.getElementById(i)};
var pal=$('pal'),q=$('palq'),hits=$('palhits'),scrim=$('scrim'),cnt=$('palcount');
var CORPUS=null,rows=[],cur=0;
function plain(h){var d=document.createElement('div');d.innerHTML=h||'';
 return (d.textContent||'').replace(/\s+/g,' ').trim()}
function corpus(){
 if(CORPUS)return CORPUS;
 CORPUS=[];
 for(var i=0;i<DATA.claims.length;i++){var c=DATA.claims[i];
  CORPUS.push({id:c.id,kind:'claim',status:c.status,goal:c.goal,
   title:c.title,text:plain(c.html)})}
 var R=DATA.routes||{};
 for(var k in R)CORPUS.push({id:k,kind:'route',status:R[k].dead?'INVALIDATED':'',
   title:R[k].title||k,text:plain(R[k].html)});
 return CORPUS;
}
function score(o,ql,ws){
 var id=o.id.toLowerCase(),ti=o.title.toLowerCase(),s=0,i;
 if(id===ql)return 1000;
 if(id.indexOf(ql)===0)s=Math.max(s,600);
 else if((i=id.indexOf(ql))>=0)s=Math.max(s,420-i);
 if(ti.indexOf(ql)===0)s=Math.max(s,500);
 else if((i=ti.indexOf(ql))>=0)s=Math.max(s,360-Math.min(i,120));
 if(ws.length>1){
  var all=true;for(i=0;i<ws.length;i++)if(ti.indexOf(ws[i])<0){all=false;break}
  if(all)s=Math.max(s,300);
 }
 if(!s){var j=o.text.toLowerCase().indexOf(ql);
  if(j>=0)s=150-Math.min(j/60,60);
  else if(ws.length>1){var a2=true;
   for(i=0;i<ws.length;i++)if(o.text.toLowerCase().indexOf(ws[i])<0){a2=false;break}
   if(a2)s=80}}
 if(s){if(o.goal)s+=45;if(o.kind==='claim')s+=12}
 return s;
}
function mark(str,ql){
 var out=esc(str),i=str.toLowerCase().indexOf(ql);
 if(i<0||!ql)return out;
 return esc(str.slice(0,i))+'<mark>'+esc(str.slice(i,i+ql.length))+'</mark>'
  +esc(str.slice(i+ql.length));
}
function snippet(o,ql){
 if(!o.text)return '';
 var i=o.text.toLowerCase().indexOf(ql);
 if(i<0)return '';
 var a=Math.max(0,i-60),b=Math.min(o.text.length,i+ql.length+90);
 return (a?'…':'')+mark(o.text.slice(a,b),ql)+(b<o.text.length?'…':'');
}
function render(){
 var ql=q.value.trim().toLowerCase();
 rows=[];
 if(ql){
  var ws=ql.split(/\s+/).filter(Boolean),C=corpus(),scored=[];
  for(var i=0;i<C.length;i++){var s=score(C[i],ql,ws);if(s>0)scored.push([s,C[i]])}
  scored.sort(function(a,b){return b[0]-a[0]||a[1].id.localeCompare(b[1].id)});
  rows=scored.slice(0,40).map(function(p){return p[1]});
 }
 cur=0;
 if(!ql){hits.innerHTML='';cnt.textContent='';return}
 if(!rows.length){
  hits.innerHTML='<li class="sel"><span class="ttl">No match for &ldquo;'
   +esc(q.value.trim())+'&rdquo;</span></li>';cnt.textContent='0 results';return}
 hits.innerHTML=rows.map(function(o,i){
  var chip=o.kind==='route'
   ?'<span class="chip route">'+(o.status==='INVALIDATED'?'failed':'route')+'</span>'
   :'<span class="chip '+o.status+'">'+o.status+'</span>';
  var sn=snippet(o,ql);
  return '<li class="'+(i===cur?'sel':'')+'" data-i="'+i+'">'+chip
   +'<span class="ttl">'+mark(o.title,ql)+'<span class="sub">'+mark(o.id,ql)
   +'</span>'+(sn?'<span class="snip">'+sn+'</span>':'')+'</span></li>'}).join('');
 cnt.textContent=rows.length+(rows.length===40?'+ results':' results');
 Array.prototype.forEach.call(hits.children,function(li){
  li.onmouseenter=function(){sel(+li.dataset.i)};
  li.onclick=function(){go(+li.dataset.i)}});
}
function sel(i){
 if(!rows.length)return;
 cur=(i+rows.length)%rows.length;
 Array.prototype.forEach.call(hits.children,function(li,j){
  li.classList.toggle('sel',j===cur)});
 var el=hits.children[cur];if(el&&el.scrollIntoView)el.scrollIntoView({block:'nearest'});
}
function go(i){
 if(!rows.length)return;
 var o=rows[i===undefined?cur:i];
 close();
 if(typeof d3==='undefined'){location.href=o.id+'.html';return}
 selectById(o.id);
 if(window.focusNode&&window.__byId&&window.__byId[o.id])
  focusNode(window.__byId[o.id]);
}
function open_(){pal.classList.add('on');scrim.classList.add('on');
 q.value='';render();setTimeout(function(){q.focus()},20)}
function close(){pal.classList.remove('on');scrim.classList.remove('on');q.blur()}
$('openSearch').onclick=open_;
scrim.onclick=close;
q.addEventListener('input',render);
q.addEventListener('keydown',function(e){
 if(e.key==='ArrowDown'){e.preventDefault();sel(cur+1)}
 else if(e.key==='ArrowUp'){e.preventDefault();sel(cur-1)}
 else if(e.key==='Enter'){e.preventDefault();go()}
 else if(e.key==='Escape'){e.preventDefault();close()}});
document.addEventListener('keydown',function(e){
 var t=e.target,tag=t&&t.tagName;
 if(tag==='INPUT'||tag==='TEXTAREA')return;
 if(e.key==='/'||((e.metaKey||e.ctrlKey)&&e.key==='k')){e.preventDefault();open_()}
 else if(e.key==='Escape')close()});
if(location.hash==='#search')open_();
})();
"""
# KaTeX renders to real glyphs and boxes rather than to a font-substituted
# approximation, and it is fast enough to typeset a panel on every click.
KATEX = (
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/'
    'dist/katex.min.css" crossorigin="anonymous">'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/'
    'katex.min.js" crossorigin="anonymous"></script>'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/'
    'contrib/auto-render.min.js" crossorigin="anonymous"></script>'
    '<script>' + KATEX_OPTS_JS + '</script>')


# ---------------------------------------------------------------------------
# Math as people actually write it
#
# Notes are written with the mathematics inside ordinary backticks and plain
# fenced blocks, in a mixed ASCII/Unicode shorthand -- `L_1 = t L t^{-1}`,
# `A^(X) semidirect H`, `‖pi(s) - I‖₂ <= eps`.  Almost nobody writes LaTeX and
# nobody should have to, so the site translates that shorthand into TeX and
# renders it, rather than asking authors to adopt a delimiter.
#
# The translation is deliberately closed: it emits only commands from the
# tables below and builds its own groups, so the output cannot fail to parse.
# Anything it does not recognise makes it decline (return None) and the span
# stays exactly as written, as code.  Declining is always safe; guessing is
# not, which is why the classifier below is a wall of exclusions -- ids, paths,
# identifiers and command lines vastly outnumber the formulas in some notes and
# must never be italicised into nonsense.
# ---------------------------------------------------------------------------
TEX_UNICODE = {
    "‖": r"\Vert", "|": r"\vert", "⟨": r"\langle", "⟩": r"\rangle",
    "≤": r"\le", "≥": r"\ge", "≠": r"\ne", "≅": r"\cong", "≃": r"\simeq",
    "≈": r"\approx", "≡": r"\equiv", "≔": r":=", "∼": r"\sim",
    "⊗": r"\otimes", "⊕": r"\oplus", "⊞": r"\boxplus", "⋊": r"\rtimes",
    "⋉": r"\ltimes", "≀": r"\wr", "∗": r"*", "×": r"\times", "·": r"\cdot",
    "∘": r"\circ", "±": r"\pm", "∓": r"\mp",
    "⊆": r"\subseteq", "⊂": r"\subset", "⊇": r"\supseteq", "⊃": r"\supset",
    "∈": r"\in", "∉": r"\notin", "∅": r"\emptyset",
    "∩": r"\cap", "∪": r"\cup", "∧": r"\wedge", "∨": r"\vee", "¬": r"\neg",
    "∀": r"\forall", "∃": r"\exists", "∑": r"\sum", "∏": r"\prod",
    "∫": r"\int", "√": r"\surd", "∞": r"\infty", "∂": r"\partial",
    "→": r"\to", "←": r"\leftarrow", "↦": r"\mapsto", "⟶": r"\longrightarrow",
    "⟹": r"\implies", "⟸": r"\impliedby", "⇒": r"\Rightarrow",
    "⇔": r"\iff", "↔": r"\leftrightarrow", "↷": r"\curvearrowright",
    "↪": r"\hookrightarrow", "↠": r"\twoheadrightarrow", "⇉": r"\rightrightarrows",
    "≪": r"\ll", "≫": r"\gg", "≺": r"\prec", "≻": r"\succ",
    "⊴": r"\trianglelefteq", "◁": r"\triangleleft", "⊥": r"\perp", "⊤": r"\top",
    "ℓ": r"\ell", "ℂ": r"\mathbb{C}", "ℝ": r"\mathbb{R}", "ℤ": r"\mathbb{Z}",
    "ℕ": r"\mathbb{N}", "ℚ": r"\mathbb{Q}", "𝔽": r"\mathbb{F}",
    "†": r"\dagger", "′": r"'", "″": r"''", "…": r"\ldots", "⋯": r"\cdots",
    "−": "-", "–": "-", "—": "-", "⁄": "/", "≟": r"\overset{?}{=}",
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\varepsilon", "ϵ": r"\epsilon", "ζ": r"\zeta", "η": r"\eta",
    "θ": r"\theta", "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda",
    "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi",
    "ϕ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi", "Ψ": r"\Psi",
    "Ω": r"\Omega", "ℵ": r"\aleph",
}
SUB_DIGITS = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
              "₆": "6", "₇": "7", "₈": "8", "₉": "9", "₊": "+", "₋": "-",
              "ₙ": "n", "ᵢ": "i", "ⱼ": "j", "ₖ": "k", "ₘ": "m", "ₚ": "p"}
SUP_DIGITS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
              "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-",
              "ⁿ": "n", "ᵀ": "T", "ᵃ": "a"}
GREEK_WORDS = {w: "\\" + w for w in (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
    "rho sigma tau upsilon phi chi psi omega Gamma Delta Theta Lambda Xi Pi "
    "Sigma Phi Psi Omega".split())}
GREEK_WORDS["pi"] = r"\pi"
GREEK_WORDS["varepsilon"] = r"\varepsilon"
GREEK_WORDS["eps"] = r"\varepsilon"
OP_WORDS = {
    "tensor": r"\otimes", "otimes": r"\otimes", "oplus": r"\oplus",
    "semidirect": r"\rtimes", "rtimes": r"\rtimes", "ltimes": r"\ltimes",
    "directSum": r"\bigoplus", "bigoplus": r"\bigoplus", "wr": r"\wr",
    "in": r"\in", "notin": r"\notin", "subset": r"\subset",
    "subseteq": r"\subseteq", "supset": r"\supset", "cap": r"\cap",
    "cup": r"\cup", "circ": r"\circ", "times": r"\times", "cdot": r"\cdot",
    "iff": r"\iff", "implies": r"\implies", "forall": r"\forall",
    "exists": r"\exists", "infty": r"\infty", "emptyset": r"\emptyset",
    "to": r"\to", "mapsto": r"\mapsto", "cong": r"\cong", "sim": r"\sim",
    "leq": r"\le", "geq": r"\ge", "neq": r"\ne", "pm": r"\pm", "mp": r"\mp",
    "sqrt": r"\sqrt", "sum": r"\sum", "prod": r"\prod", "int": r"\int",
    "ker": r"\ker", "dim": r"\dim", "deg": r"\deg", "det": r"\det",
    "exp": r"\exp", "log": r"\log", "inf": r"\inf", "sup": r"\sup",
    "lim": r"\lim", "max": r"\max", "min": r"\min", "gcd": r"\gcd",
    "mod": r"\bmod", "tr": r"\operatorname{tr}", "Tr": r"\operatorname{Tr}",
    "im": r"\operatorname{im}", "coker": r"\operatorname{coker}",
    "rank": r"\operatorname{rank}", "span": r"\operatorname{span}",
    "supp": r"\operatorname{supp}", "id": r"\operatorname{id}",
    "Aut": r"\operatorname{Aut}", "End": r"\operatorname{End}",
    "Hom": r"\operatorname{Hom}", "Ind": r"\operatorname{Ind}",
    "Res": r"\operatorname{Res}", "Ad": r"\operatorname{Ad}",
}
ASCII_OPS = sorted(
    [("<->", r"\leftrightarrow"), ("|->", r"\mapsto"),
     ("|-->", r"\longmapsto"), ("==>", r"\implies"), ("<=>", r"\iff"),
     ("<==", r"\impliedby"), ("-->", r"\longrightarrow"),
     ("<=", r"\le"), (">=", r"\ge"), ("!=", r"\ne"), ("~=", r"\cong"), ("=~", r"\cong"),
     ("->", r"\to"), ("=>", r"\Rightarrow"), ("<<", r"\ll"),
     (">>", r"\gg"), ("::", r"::"), (":=", r":="), ("||", r"\Vert")],
    key=lambda kv: -len(kv[0]))
STOPWORDS = set(
    "a an and are as at be been but by can does do each every for from has "
    "have if in into is it its no not of on or over so some that the then "
    "there these this to under up was were where which while with without "
    "iff only if_and_only_if all any".split()) - {"in", "iff", "to"}
_MATH_UNI = set(TEX_UNICODE) | set(SUB_DIGITS) | set(SUP_DIGITS)
_SLUGRE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")
_PATHRE = re.compile(r"[\w./-]+\.(?:md|py|lean|tex|json|ya?ml|sh|toml|txt|cff|g)\b")
_LEANRE = re.compile(r"^[A-Za-z][\w']*(?:\.[A-Za-z][\w']*)+$")


def _tex_atoms(src):
    """Translate the shorthand into (kind, tex) atoms, or None to decline."""
    atoms, i, n = [], 0, len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            atoms.append(("sp", " "))
            i += 1
            continue
        if ch in "\\@`\u00a0":
            return None
        for a, t in ASCII_OPS:
            if src.startswith(a, i):
                atoms.append(("rel", t))
                i += len(a)
                break
        else:
            if ch in ("_", "^"):
                j = i + 1
                if j < n and src[j] in "({":
                    close = ")" if src[j] == "(" else "}"
                    depth, k = 1, j + 1
                    while k < n and depth:
                        if src[k] == src[j]:
                            depth += 1
                        elif src[k] == close:
                            depth -= 1
                        k += 1
                    if depth:
                        return None
                    inner = _tex_atoms(src[j + 1:k - 1])
                    if inner is None:
                        return None
                    atoms.append(("script", ch + "{" + _join_atoms(inner) + "}"))
                    i = k
                    continue
                m = re.match(r"[-+]?[A-Za-z0-9]+|[*']", src[j:])
                if not m:
                    return None
                tok = m.group(0)
                if re.fullmatch(r"[A-Za-z]{2,}", tok):
                    tok = (GREEK_WORDS.get(tok) or r"\mathrm{" + tok + "}")
                atoms.append(("script", ch + "{" + tok + "}"))
                i = j + m.end()
                continue
            if ch in SUB_DIGITS:
                run = ""
                while i < n and src[i] in SUB_DIGITS:
                    run += SUB_DIGITS[src[i]]
                    i += 1
                atoms.append(("script", "_{" + run + "}"))
                continue
            if ch in SUP_DIGITS:
                run = ""
                while i < n and src[i] in SUP_DIGITS:
                    run += SUP_DIGITS[src[i]]
                    i += 1
                atoms.append(("script", "^{" + run + "}"))
                continue
            if ch in TEX_UNICODE:
                atoms.append(("op", TEX_UNICODE[ch]))
                i += 1
                continue
            if ch == "\u0304":  # combining macron: bar the previous atom
                for k in range(len(atoms) - 1, -1, -1):
                    if atoms[k][0] in ("var", "word"):
                        atoms[k] = (atoms[k][0], r"\bar{" + atoms[k][1] + "}")
                        break
                i += 1
                continue
            if ch.isalpha():
                # a hyphenated lowercase phrase is English, not a product
                hy = re.match(r"[a-z]+(?:-[a-z]+)+", src[i:])
                if hy and not any(p in GREEK_WORDS or p in OP_WORDS
                                  for p in hy.group(0).split("-")):
                    atoms.append(("word", r"\text{" + hy.group(0) + "}"))
                    i += hy.end()
                    continue
                m = re.match(r"[A-Za-z]+", src[i:])
                if not m:
                    return None  # a letter with no translation: stay verbatim
                w = m.group(0)
                i += m.end()
                if w in GREEK_WORDS:
                    atoms.append(("var", GREEK_WORDS[w]))
                elif w in OP_WORDS:
                    atoms.append(("op", OP_WORDS[w]))
                elif len(w) == 1:
                    atoms.append(("var", w))
                elif w.lower() in STOPWORDS:
                    atoms.append(("word", r"\text{" + w + "}"))
                else:
                    atoms.append(("var", r"\mathrm{" + w + "}"))
                continue
            if ch.isdigit():
                m = re.match(r"[0-9]+(?:\.[0-9]+)?", src[i:])
                atoms.append(("num", m.group(0)))
                i += m.end()
                continue
            if ch in "+-*/=<>()[]|,.:;!?'":
                atoms.append(("rel" if ch in "=<>" else "punct", ch))
                i += 1
                continue
            if ch in "{}":
                atoms.append(("punct", "\\" + ch))
                i += 1
                continue
            if ch in "%#&$":
                atoms.append(("punct", "\\" + ch))
                i += 1
                continue
            if ch == "~":
                atoms.append(("op", r"\sim"))
                i += 1
                continue
            return None
    return atoms


def _join_atoms(atoms):
    out = []
    for k, (kind, tex) in enumerate(atoms):
        if kind == "sp":
            prev = nxt = None
            for j in range(k - 1, -1, -1):
                if atoms[j][0] != "sp":
                    prev = atoms[j][0]
                    break
            for j in range(k + 1, len(atoms)):
                if atoms[j][0] != "sp":
                    nxt = atoms[j][0]
                    break
            if prev in ("word", "var", "num") and nxt in ("word", "var", "num"):
                out.append(r"\ ")
            continue
        out.append(tex)
    # `\to R` not `\toR`: a command that runs into a letter is a different,
    # undefined command.  Done at token boundaries, where the split is known --
    # a regex over the joined string backtracks into `\thet`+`a`.
    joined = []
    for k, piece in enumerate(out):
        joined.append(piece)
        nxt = out[k + 1] if k + 1 < len(out) else ""
        if re.search(r"\\[A-Za-z]+$", piece) and nxt[:1].isalpha():
            joined.append(" ")
    return "".join(joined)


def house_to_tex(src):
    """TeX for a shorthand formula, or None if it should stay verbatim."""
    if not src.strip() or len(src) > 400:
        return None
    atoms = _tex_atoms(src)
    if atoms is None:
        return None
    tex = _join_atoms(atoms).strip()
    return tex or None


def is_math_source(s, ids=()):
    """Should this verbatim span be typeset as mathematics?"""
    t = s.strip()
    if not t or t in ids or len(t) > 400:
        return False
    if _PATHRE.search(t) or _LEANRE.match(t) or _SLUGRE.match(t):
        return False
    if re.match(r"^(?:bin/|\./|python3?\s|git\s|lake\s|grep\s|cairn\s|msi\s|"
                r"sed\s|awk\s|rsync\s|gh\s)", t):
        return False
    if re.match(r"^[A-Z][A-Z0-9_]{3,}$", t) or re.search(r"arXiv|doi:", t):
        return False
    if re.match(r"^[a-z_]+:\s*($|\[|\{)", t) or t.endswith(":"):
        return False
    if re.search(r"(?:[A-Za-z0-9_]{3,}|/)\*", t):
        return False
    if re.search(r"[A-Za-z0-9_]{3,}/[A-Za-z0-9_]{3,}", t) \
            and not any(c in _MATH_UNI for c in t):
        return False
    if any(w in ids for w in re.findall(r"[a-z0-9][a-z0-9-]{3,}", t)):
        return False
    signal = (any(c in _MATH_UNI for c in t)
              or re.search(r"[_^][({A-Za-z0-9]", t)
              or re.search(r"\b(" + "|".join(GREEK_WORDS) + r")\b", t)
              or re.search(r"\b(" + "|".join(re.escape(w) for w in OP_WORDS) + r")\b", t)
              or re.search(r"(<=|>=|!=|->|=>|\|->|~=|<=>)", t)
              or re.search(r"[=<>]", t)
              or re.fullmatch(r"[A-Za-z][A-Za-z0-9_^{}()'-]{0,3}", t)
              or re.fullmatch(r"[0-9]+(?:[/^][0-9]+)+", t))
    return bool(signal)


def tex_span(src, ids=(), display=False):
    """Rendered element for a formula, or None to keep it verbatim."""
    if not is_math_source(src, ids):
        return None
    tex = house_to_tex(src)
    if not tex:
        return None
    cls = "texd" if display else "tex"
    return (f'<span class="{cls}" data-src="{html.escape(src, quote=True)}">'
            f"{html.escape(tex)}</span>")


def math_block(body, ids=()):
    """Display math for a fenced block, or None to keep it preformatted."""
    lines = [ln for ln in body.split("\n")]
    live = [ln for ln in lines if ln.strip()]
    if not live or len(live) > 24:
        return None
    for ln in live:
        # drawings, listings, tables and prose are not formulas
        if re.search(r"[│├└┌┬┴┼─┐╭╰•]|\[(?:OPEN|✓|✗)\]|^\s*[-*+]\s|\]\(|^#|"
                     r"^\s{0,3}\w[\w .]{0,40}:\s*$", ln):
            return None
        if len(ln) > 200:
            return None
        if not is_math_source(ln, ids):
            return None
    out = []
    for ln in live:
        tex = house_to_tex(ln)
        if not tex:
            return None
        out.append(f'<span class="texd" data-src="{html.escape(ln, quote=True)}">'
                   f"{html.escape(tex)}</span>")
    return '<div class="mathblock">' + "".join(out) + "</div>"


REFERENCED_FILES = set()
_FILE_MENTION = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:md|lean|py|tex|json|ya?ml|toml|sh|txt|cff|g))"
    r"((?::\d+(?:[-–]\d+)?)?)")
_URL = re.compile(r"(?<![\w\"'=])(https?://[^\s<>\"')\]]+)")


def file_page_name(path):
    return "f_" + re.sub(r"[^A-Za-z0-9._-]", "-", path.strip("/")) + ".html"


def _repo_has(path):
    try:
        full = os.path.join(REPO, path)
        return os.path.isfile(full) and os.path.getsize(full) <= 4_000_000
    except OSError:
        return False


def linkify_prose(html_str):
    """Make URLs and file mentions clickable in already-escaped HTML.

    File mentions resolve to this site's own rendered page for the file, not
    to a forge: a reader following a reference should land somewhere the
    mathematics is typeset, and should not need an account to read it."""
    parts = re.split(r"(<[^>]+>)", html_str)
    out, in_a = [], 0
    for part in parts:
        if part.startswith("<"):
            if part.startswith("<a"):
                in_a += 1
            elif part.startswith("</a"):
                in_a = max(0, in_a - 1)
            out.append(part)
            continue
        if in_a:
            out.append(part)
            continue
        part = _URL.sub(
            lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener">'
                      f"{m.group(1)}</a>", part)

        def fileref(m):
            path, pin = m.group(1), m.group(2)
            if not _repo_has(path):
                return m.group(0)
            REFERENCED_FILES.add(path)
            frag = ""
            if pin:
                frag = "#L" + re.split(r"[-–]", pin[1:])[0]
            return (f'<a class="fileref" href="{file_page_name(path)}{frag}">'
                    f"{path}{pin}</a>")

        out.append(_FILE_MENTION.sub(fileref, part))
    return "".join(out)


def md_to_html(md, ids=()):
    out, in_code, in_list, para = [], False, False, []
    fence = [False]  # is the open fence a math fence?
    fence_buf = []

    def inline(s):
        # Verbatim spans come out before escaping: a formula must keep its
        # own `<`, `&` and braces to be translatable at all.
        held = []

        def hold(m):
            raw = m.group(1)
            el = tex_span(raw, ids)
            held.append(el or "<code>"
                        + linkify_prose(html.escape(raw, quote=False))
                        + "</code>")
            return "\x00%d\x00" % (len(held) - 1)

        s = re.sub(r"`([^`]+)`", hold, s)
        s = html.escape(s, quote=False)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<em>\1</em>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
        s = linkify_prose(s)
        return re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], s)

    def flush_para():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    def flush_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in md.split("\n"):
        if line.strip().startswith("```"):
            flush_para()
            flush_list()
            if not in_code:
                in_code = True
                fence[0] = line.strip()[3:].strip().lower() in ("math", "latex", "tex")
                fence_buf.clear()
            else:
                in_code = False
                body = "\n".join(fence_buf)
                blk = math_block(body, ids)
                if blk is None and fence[0]:
                    # an explicit math fence is trusted even when the
                    # shorthand translator declines it
                    blk = ('<div class="mathblock"><span class="texd">'
                           + html.escape(body.strip()) + "</span></div>")
                out.append(blk or "<pre>" + html.escape(body) + "</pre>")
                fence_buf.clear()
                fence[0] = False
            continue
        if in_code:
            fence_buf.append(line)
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush_para()
            flush_list()
            lvl = len(m.group(1)) + 1
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            continue
        if re.match(r"^\s*[-*]\s+", line):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + inline(re.sub(r"^\s*[-*]\s+", "", line)) + "</li>")
            continue
        if not line.strip():
            flush_para()
            flush_list()
            continue
        para.append(line.strip())
    flush_para()
    flush_list()
    if in_code:
        out.append("<pre>" + html.escape("\n".join(fence_buf)) + "</pre>")
    return "\n".join(out)


def badge(status):
    return (f'<span class="badge" style="background:'
            f'{STATUS_COLOR.get(status, "#888")}">{html.escape(str(status))}</span>')


def node_link(graph, nid):
    n = graph.nodes.get(nid)
    if not n:
        return html.escape(str(nid))
    return f'<a class="node" href="{nid}.html">{nid}</a> {badge(n.status)} {html.escape(n.title)}'


def page(title, body_html):
    nav = ('<nav class="top"><a href="index.html">graph</a>'
           '<a href="nodes.html">all nodes</a>'
           '<a href="index.html#search">search</a></nav>')
    return ("<!doctype html><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title>"
            f"<style>{SITE_CSS}</style>{KATEX}<body>{nav}{body_html}</body>")


INDEX_TMPL = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cairn</title>
__KATEX__
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>
:root{__PALETTE__;color-scheme:light}
html,body{height:100%;margin:0}
body{background:var(--paper);color:var(--ink);font:14px/1.55 __SANS__;
font-synthesis:none;text-rendering:geometricPrecision;
-webkit-font-smoothing:antialiased}
header{display:flex;align-items:center;gap:1.4em;padding:.7em 1.3em;
border-bottom:1px solid var(--line);background:var(--paper)}
.wordmark{font-size:11px;font-weight:700;letter-spacing:.34em}
.stats{color:var(--mut2);font-size:11px;font-variant-numeric:tabular-nums;
letter-spacing:.02em}
#openSearch{margin-left:auto;display:flex;align-items:center;gap:.7em;
border:1px solid var(--line);background:var(--paper);color:var(--mut2);
font:inherit;font-size:12px;padding:.4em .8em .4em 1em;cursor:pointer;
min-width:15em;text-align:left}
#openSearch:hover{border-color:var(--rule);color:var(--ink)}
#openSearch kbd{margin-left:auto;font:10px __MONO__;color:var(--mut2);
border:1px solid var(--line);padding:.1em .4em}
header label{color:var(--mut2);font-size:11px;cursor:pointer;user-select:none;
letter-spacing:.02em}
header button.lnk{border:0;background:none;color:var(--mut2);font:inherit;
font-size:11px;letter-spacing:.14em;text-transform:uppercase;cursor:pointer;
padding:0}
header button.lnk:hover{color:var(--accent)}
header a{color:var(--mut2);text-decoration:none;font-size:11px;
letter-spacing:.14em;text-transform:uppercase}
header a:hover{color:var(--accent)}
main{position:relative;height:calc(100% - 45px);overflow:hidden}
#view{display:block;width:100%;height:100%;cursor:grab;background:var(--paper)}
aside{position:absolute;top:0;right:0;bottom:0;width:27em;max-width:92vw;
background:var(--paper);border-left:1px solid var(--line);
padding:1.4em 1.6em 3em;overflow-y:auto;box-sizing:border-box;
transform:translateX(103%);transition:transform .18s ease}
aside.open{transform:none}
aside .x{position:absolute;top:.6em;right:.8em;border:0;background:none;
color:var(--mut2);font-size:20px;cursor:pointer;line-height:1}
aside h2{font-size:1.32rem;font-weight:500;letter-spacing:-.025em;
line-height:1.15;margin:.5em 1.2em .55em 0;max-width:24ch}
.chip{display:inline-block;padding:.2em .7em;color:#fff;font-size:9.5px;
font-weight:700;letter-spacing:.1em}
.chip.ESTABLISHED{background:var(--est)}.chip.OPEN{background:var(--open)}
.chip.INVALIDATED{background:var(--dead)}
.chip.route{background:var(--paper);color:var(--mut);border:1px solid var(--rule)}
.chip.goal{background:var(--goal)}
aside code{font:11.5px __MONO__;color:var(--mut)}
.stmt{font-size:13px;line-height:1.6;background:var(--panel);
border:1px solid var(--line);padding:.9em 1.1em;max-height:42vh;overflow-y:auto}
.stmt code{font:11px __MONO__;background:var(--paper);border:1px solid var(--line);
padding:.03em .28em}
.stmt pre{font:11px/1.5 __MONO__;background:var(--paper);
border:1px solid var(--line);padding:.7em;overflow-x:auto}
.stmt a{color:var(--ink);border-bottom:1px solid var(--rule);text-decoration:none}
.stmt a:hover{color:var(--accent);border-bottom-color:var(--accent)}
.stmt p{margin:.55em 0}
.stmt .mathblock{overflow-x:auto;margin:.9em 0;padding:.5em .7em;
background:var(--paper);border:1px solid var(--line)}
.stmt .katex{font-size:1em}
.stmt .texd{display:block}
.stmt .texfail{font:11px __MONO__;background:var(--paper);
border:1px solid var(--line);padding:.03em .28em}
.stmt a.fileref{color:var(--ink);border-bottom:1px solid var(--rule)}
h3.sec{font-size:10px;font-weight:700;letter-spacing:.16em;color:var(--mut2);
text-transform:uppercase;margin:1.9em 0 .4em}
.fr{list-style:none;padding:0;margin:.3em 0}
.fr li{padding:.5em 0;border-bottom:1px solid var(--line);font-size:13px;
cursor:pointer;line-height:1.4}
.fr li:hover{color:var(--accent)}
.fr .imp{color:var(--mut2);font:10.5px __MONO__}
.hint{color:var(--mut);font-size:12px}
details summary{cursor:pointer;font-size:10px;font-weight:700;letter-spacing:.16em;
text-transform:uppercase;margin:1.9em 0 .4em;color:var(--mut2)}
svg text{font:10px __MONO__;fill:var(--mut);pointer-events:none;
paint-order:stroke;stroke:var(--paper);stroke-width:3.5px;stroke-linejoin:round}
svg text.goalcap{fill:var(--goal);stroke-width:4px}
.lk{stroke:var(--edge);stroke-width:1.5}
.lk.kill,.lk.dead{stroke:var(--dead);stroke-dasharray:5 3;stroke-width:1.2;
opacity:.75}
g.deadbit,line.dead{visibility:hidden}
.showdead g.deadbit,.showdead line.dead{visibility:visible}
g.orphan{display:none}
.dim{opacity:.13}
g.n,line.lk{transition:opacity .1s ease}
text.hidelabel{display:none}
g.n.hot text{display:block}
g.n.hot circle{stroke-width:3}
line.lk.hot{stroke:var(--ink);stroke-width:1.9}
line.lk.kill.hot,line.lk.dead.hot{stroke:var(--dead);stroke-width:1.7}
a.open-page{color:var(--ink);font-size:12.5px;letter-spacing:.02em;
border-bottom:1px solid var(--rule);text-decoration:none}
a.open-page:hover{color:var(--accent);border-bottom-color:var(--accent)}
ul.arts{list-style:none;padding:0;margin:.3em 0}
ul.arts li{padding:.3em 0;font:11.5px __MONO__;word-break:break-all}
ul.arts a{color:var(--ink);text-decoration:none;
border-bottom:1px solid var(--rule)}
ul.arts a:hover{color:var(--accent);border-bottom-color:var(--accent)}
#key{position:absolute;left:16px;bottom:14px;display:flex;flex-direction:column;
gap:.3em;font-size:10.5px;color:var(--mut2);pointer-events:none}
#key svg{vertical-align:-3px;margin-right:.45em}
#scrim{position:absolute;inset:0;background:#17171412;opacity:0;
pointer-events:none;transition:opacity .14s}
#scrim.on{opacity:1;pointer-events:auto}
#pal{position:absolute;top:9vh;left:50%;transform:translateX(-50%) scale(.985);
width:min(46em,92vw);background:var(--paper);border:1px solid var(--ink);
display:none;flex-direction:column;max-height:74vh;opacity:0;
transition:opacity .14s,transform .14s}
#pal.on{display:flex;opacity:1;transform:translateX(-50%) scale(1)}
#pal input{border:0;border-bottom:1px solid var(--line);background:none;
font:400 1.35rem/1.2 __SANS__;letter-spacing:-.02em;color:var(--ink);
padding:.85em 1em;outline:none;width:100%;box-sizing:border-box}
#pal input::placeholder{color:var(--mut2)}
#palhits{overflow-y:auto;margin:0;padding:0;list-style:none}
#palhits li{padding:.7em 1.15em;border-bottom:1px solid var(--line);
cursor:pointer;display:flex;gap:.9em;align-items:baseline}
#palhits li:last-child{border-bottom:0}
#palhits li.sel{background:var(--panel);box-shadow:inset 3px 0 0 var(--accent)}
#palhits .ttl{font-size:13.5px;line-height:1.35;flex:1;min-width:0}
#palhits .sub{display:block;color:var(--mut2);font:10.5px __MONO__;
margin-top:.25em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#palhits mark{background:#a33a1c1f;color:var(--accent);font-weight:700}
#palhits .snip{display:block;color:var(--mut);font-size:11.5px;margin-top:.3em;
line-height:1.4}
#palfoot{border-top:1px solid var(--line);padding:.5em 1.15em;
color:var(--mut2);font-size:10.5px;display:flex;gap:1.4em}
#palfoot b{font-weight:400;color:var(--ink);font-family:__MONO__}
</style>
<body>
<header><span class="wordmark">CAIRN</span><span class="stats">__STATS__</span>
<button id="openSearch">search the graph<kbd>/</kbd></button>
<label><input type="checkbox" id="showdead" checked> failed routes</label>
<button class="lnk" id="frontierbtn">frontier</button>
<a href="nodes.html">all nodes</a></header>
<main><svg id="view"></svg>
<div id="key">
<span><svg width="22" height="16"><circle cx="9" cy="8" r="7.6" fill="none" stroke="#4f46e5" stroke-width="1.8"/><circle cx="9" cy="8" r="4.6" fill="#fff" stroke="#c08a00" stroke-width="2"/></svg>goal</span>
<span><svg width="22" height="16"><circle cx="9" cy="8" r="6" fill="#178a5e"/></svg>established</span>
<span><svg width="22" height="16"><circle cx="9" cy="8" r="6" fill="#fff" stroke="#c08a00" stroke-width="2.2"/></svg>open</span>
<span><svg width="22" height="16"><rect x="4" y="3" width="10" height="10" fill="#8b8f86"/></svg>&and; multi-premise route</span>
<span><svg width="27" height="16"><line x1="1" y1="8" x2="20" y2="8" stroke="#17171459" stroke-width="1.5"/><path d="M19,4.5L25,8L19,11.5z" fill="#17171459"/></svg>premises &#10230; target</span>
<span><svg width="27" height="16"><line x1="1" y1="8" x2="20" y2="8" stroke="#c43c2e" stroke-width="1.3" stroke-dasharray="5,3"/><path d="M19,4.5L25,8L19,11.5z" fill="#c43c2e"/></svg>failed / invalidated</span>
</div>
<div id="scrim"></div>
<div id="pal" role="dialog" aria-label="Search the graph">
<input id="palq" type="text" autocomplete="off" spellcheck="false"
 placeholder="Search titles, ids and statements&hellip;">
<ul id="palhits"></ul>
<div id="palfoot"><span><b>&uarr;&darr;</b> move</span><span><b>&crarr;</b> open</span>
<span><b>esc</b> close</span><span id="palcount"></span></div>
</div>
<aside id="panel"><button class="x" id="closepanel">&times;</button><div id="panelbody"></div></aside></main>
<script>
const DATA=__DATA__;
const panel=document.getElementById('panel');
const pbody=document.getElementById('panelbody');
const esc=t=>{const d=document.createElement('i');d.textContent=t;return d.innerHTML};
const openPanel=()=>panel.classList.add('open');
const closePanel=()=>panel.classList.remove('open');
document.getElementById('closepanel').onclick=closePanel;
function frontierHome(){
 let h='';
 const goals=DATA.claims.filter(c=>c.goal);
 if(goals.length){
  h+='<h3 class="sec">Goals</h3><ul class="fr">';
  for(const c of goals)
   h+=`<li data-id="${c.id}"><span class="chip ${c.status}">${c.status}</span> ${esc(c.title)}<br><span class="imp">${c.id}</span></li>`;
  h+='</ul>';
 }
 h+='<h3 class="sec">Frontier</h3><ul class="fr">';
 for(const c of DATA.claims.filter(c=>c.frontier).sort((a,b)=>b.impact-a.impact))
  h+=`<li data-id="${c.id}">${esc(c.title)}<br><span class="imp">${c.id} &middot; ${c.impact} live route(s)${c.lock?' &middot; claimed ('+esc(c.lock)+')':''}</span></li>`;
 h+='</ul>';
 const lib=DATA.claims.filter(c=>c.status==='ESTABLISHED').sort((a,b)=>a.title.localeCompare(b.title));
 h+=`<details><summary>Library &mdash; ${lib.length} established</summary><ul class="fr">`;
 for(const c of lib)h+=`<li data-id="${c.id}">${esc(c.title)}<br><span class="imp">${c.id}</span></li>`;
 h+='</ul></details>';
 pbody.innerHTML=h;
 pbody.querySelectorAll('li').forEach(li=>li.onclick=()=>selectById(li.dataset.id));
 if(window.cairnTypeset)cairnTypeset(pbody);
 openPanel();
}
document.getElementById('frontierbtn').onclick=frontierHome;
let selectById=id=>{};
if(typeof d3==='undefined'){
 document.getElementById('view').outerHTML='<div style="padding:2em">d3 CDN unreachable &mdash; use <a href="nodes.html">all nodes</a>.</div>';
 frontierHome();
}else{
const nodes=[],links=[],byId={};
for(const c of DATA.claims){c.type='claim';nodes.push(c);byId[c.id]=c}
window.__byId=byId;
for(const l of DATA.links)links.push({source:l.source,target:l.target,kind:'arrow',route:l.route,dead:l.dead});
for(const j of DATA.junctions){
 const jn={id:'j:'+j.route,type:'junction',route:j.route,rtitle:j.title,
  requires:j.requires,tgt:j.target,dead:j.dead};
 nodes.push(jn);byId[jn.id]=jn;
 for(const q of j.requires)links.push({source:q,target:jn.id,kind:'in',dead:j.dead});
 links.push({source:jn.id,target:j.target,kind:'arrow',dead:j.dead});
}
for(const d of DATA.dead){
 const st={id:'x:'+d.route,type:'stub',route:d.route,rtitle:d.title,
  tgt:d.target,killers:d.killers,dead:true};
 nodes.push(st);byId[st.id]=st;
 links.push({source:st.id,target:d.target,kind:'arrow',dead:true});
 for(const k of d.killers)if(byId[k])links.push({source:k,target:st.id,kind:'kill',dead:true});
}
for(const a of DATA.affinity)links.push({source:a.a,target:a.b,kind:'aff',w:a.w});
// hierarchy: goals at depth 0, each claim at its derivation distance;
// junctions and dead stubs sit mid-band, obstructions beside their kill,
// anything unreachable parks in the bottom band
const maxD=DATA.maxDepth||0;
for(const n of nodes)
 if(n.type==='junction'||n.type==='stub')
  n.depth=(byId[n.tgt]&&byId[n.tgt].depth!=null?byId[n.tgt].depth:maxD)+0.5;
for(const n of nodes)if(n.type==='stub')
 for(const k of n.killers||[])if(byId[k]&&byId[k].depth==null)byId[k].depth=n.depth+0.5;
for(const n of nodes)if(n.depth==null)n.depth=maxD+1;
const real=l=>l.kind!=='aff';
const svg=d3.select('#view'),W=svg.node().clientWidth,H=svg.node().clientHeight;
const bandY=d=>70+(H-150)*(d.depth/(maxD+2));
nodes.forEach(n=>{n.y=bandY(n);n.x=W/2+(Math.random()-.5)*W*.7});
svg.append('defs').html('<marker id="m" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="7.5" markerHeight="7.5" orient="auto"><path d="M0,0L8,4L0,8z" fill="#17171459"/></marker><marker id="mr" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="7.5" markerHeight="7.5" orient="auto"><path d="M0,0L8,4L0,8z" fill="#c43c2e"/></marker>');
const g=svg.append('g');
const zoom=d3.zoom().scaleExtent([.2,3.5])
 .on('zoom',e=>{g.attr('transform',e.transform)});
svg.call(zoom).on('dblclick.zoom',null);
const linkForce=d3.forceLink(links).id(d=>d.id)
 .distance(l=>l.kind==='aff'?150:(l.kind==='in'?60:115))
 .strength(l=>l.kind==='aff'?.03+.1*l.w:.55);
const sim=d3.forceSimulation(nodes)
 .force('link',linkForce)
 .force('charge',d3.forceManyBody().strength(-430))
 .force('x',d3.forceX(W/2).strength(.04))
 .force('y',d3.forceY(bandY).strength(.5))
 .force('collide',d3.forceCollide(d=>d.type==='claim'?(d.goal?48:38):13));
const line=g.selectAll('line').data(links.filter(real)).join('line')
 .attr('class',l=>'lk'+(l.kind==='kill'?' kill':'')+(l.dead?' dead':''))
 .attr('marker-end',l=>l.kind==='in'?null:(l.dead||l.kind==='kill'?'url(#mr)':'url(#m)'))
 .style('cursor',l=>l.route?'pointer':null)
 .on('click',(e,l)=>{if(l.route){e.stopPropagation();showRoute(l.route)}});
line.filter(l=>l.route).append('title').text(l=>l.title||l.route);
const node=g.selectAll('g.n').data(nodes).join('g')
 .attr('class',d=>'n'+(d.dead?' deadbit':''))
 .style('cursor','pointer')
 .call(d3.drag()
   .on('start',(e,d)=>{if(!e.active)sim.alphaTarget(.25).restart();d.fx=d.x;d.fy=d.y})
   .on('drag',(e,d)=>{d.fx=e.x;d.fy=e.y})
   .on('end',(e,d)=>{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null}));
node.filter(d=>d.type==='claim'&&d.goal).append('circle')
 .attr('r',23).attr('fill','none').attr('stroke','var(--goal)').attr('stroke-width',2.2);
node.filter(d=>d.type==='claim').append('circle')
 .attr('r',d=>d.goal?15:10+Math.min(d.impact*1.5,4))
 .attr('fill',d=>d.status==='ESTABLISHED'?'var(--est)':'#fff')
 .attr('stroke',d=>d.status==='ESTABLISHED'?'#0f6b47':'var(--open)')
 .attr('stroke-width',2.2);
node.filter(d=>d.type==='claim'&&d.goal).append('text')
 .attr('text-anchor','middle').attr('dy',-31)
 .style('fill','var(--goal)').style('font-weight','700')
 .style('letter-spacing','.2em').text('GOAL');
node.filter(d=>d.type==='junction').append('rect')
 .attr('x',-6).attr('y',-6).attr('width',12).attr('height',12)
 .attr('fill',d=>d.dead?'var(--dead)':'#9a9e94');
node.filter(d=>d.type==='junction').append('text')
 .attr('text-anchor','middle').attr('dy',3.5)
 .style('fill','#fff').style('font-size','9px').text('\u2227');
node.filter(d=>d.type==='stub').append('circle')
 .attr('r',6.5).attr('fill','#fff').attr('stroke','var(--dead)')
 .attr('stroke-width',1.7).attr('stroke-dasharray','3 2');
// Labels are the real estate that runs out first, so they are placed by
// priority and any that would collide with one already placed is dropped:
// the graph stays readable at every zoom instead of turning into a hedge.
const LBL=[];
const prio=d=>(d.goal?1e6:0)+(d.frontier?1e4:0)+(d.impact||0)*10
 +(d.status==='ESTABLISHED'?1:0);
node.filter(d=>d.type==='claim').each(function(d){
 let l1='',l2='';
 for(const w of d.title.split(' ')){
  if(!l2&&(l1+' '+w).trim().length<=26)l1=(l1+' '+w).trim();
  else l2=(l2+' '+w).trim();
 }
 if(l2.length>28)l2=l2.slice(0,27)+'\\u2026';
 const txt=d3.select(this).append('text').attr('text-anchor','middle');
 txt.append('tspan').attr('x',0).attr('dy',d.goal?36:27).text(l1);
 if(l2)txt.append('tspan').attr('x',0).attr('dy',11).text(l2);
 LBL.push({d:d,el:txt.node(),w:Math.max(l1.length,l2.length)*5.9+8,
  h:l2?25:14,top:d.goal?28:19});
});
LBL.sort((a,b)=>prio(b.d)-prio(a.d));
function relabel(){
 const sd=document.getElementById('showdead').checked;
 const kept=[];
 for(const o of LBL){
  const d=o.d;
  if(d.orphan||(d.dead&&!sd)){o.el.classList.add('hidelabel');continue}
  const x=d.x-o.w/2,y=d.y+o.top,b=[x,y,x+o.w,y+o.h];
  let hit=false;
  for(let i=0;i<kept.length;i++){const k=kept[i];
   if(b[0]<k[2]&&k[0]<b[2]&&b[1]<k[3]&&k[1]<b[3]){hit=true;break}}
  if(hit)o.el.classList.add('hidelabel');
  else{o.el.classList.remove('hidelabel');kept.push(b)}
 }
}
node.append('title').text(d=>d.type==='claim'?`${d.id} [${d.status}]`:(d.rtitle||d.route));
// Focus: hover previews, a click sticks, clicking the background clears.
// A route is highlighted whole -- reaching a junction or a stub pulls in its
// other endpoints, so a multi-premise route never lights up half-drawn.
let selected=null;
function nbrs(d){
 const keep=new Set([d.id]),ends=l=>[l.source.id||l.source,l.target.id||l.target];
 const rl=links.filter(real);
 rl.forEach(l=>{const[a,b]=ends(l);
  if(a===d.id)keep.add(b);if(b===d.id)keep.add(a)});
 rl.forEach(l=>{const[a,b]=ends(l),A=byId[a],B=byId[b];
  const hub=x=>x&&(x.type==='junction'||x.type==='stub');
  if(hub(A)&&keep.has(a))keep.add(b);
  if(hub(B)&&keep.has(b))keep.add(a)});
 return keep;
}
function highlight(d){
 if(!d){g.classed('focus',false);
  node.classed('dim',false).classed('hot',false);
  line.classed('dim',false).classed('hot',false);return}
 const keep=nbrs(d);
 g.classed('focus',true);
 node.classed('dim',n=>!keep.has(n.id)).classed('hot',n=>n.id===d.id);
 line.classed('dim',l=>!(keep.has(l.source.id)&&keep.has(l.target.id)))
     .classed('hot',l=>l.source.id===d.id||l.target.id===d.id);
}
node.on('mouseenter',(e,d)=>{if(!selected)highlight(d)})
 .on('mouseleave',()=>{if(!selected)highlight(null)});
// Every id in a panel is a link into the graph, and every artifact is a link
// out to the file it names -- nothing in the panel is a dead end.
const idlink=id=>byId[id]
 ?`<a href="#" data-goto="${esc(id)}">${esc(id)}</a>`
 :`<a href="${esc(id)}.html">${esc(id)}</a>`;
const artlist=arts=>!arts||!arts.length?''
 :'<h3 class="sec">Artifacts</h3><ul class="arts">'+arts.map(a=>
   a[1]?`<li><a href="${esc(a[1])}" target="_blank" rel="noopener">${esc(a[0])}</a></li>`
       :`<li>${esc(a[0])}</li>`).join('')+'</ul>';
function showRoute(rid){
 const r=(DATA.routes||{})[rid];
 if(!r){location.href=rid+'.html';return}
 const imp=(r.requires&&r.requires.length
   ?r.requires.map(idlink).join(' \\u2227 '):'\\u22a4')+' \\u27f9 '+idlink(r.target);
 pbody.innerHTML=`<span class="chip route">route${r.dead?' &middot; failed':''}</span>
  <h2>${esc(r.title||rid)}</h2><code>${esc(rid)}</code>
  <h3 class="sec">Implication</h3><p style="font-size:12.5px">${imp}</p>
  ${r.killers&&r.killers.length?`<p class="hint">invalidated by `+
    r.killers.map(idlink).join(', ')+`</p>`:''}
  ${r.html?`<div class="stmt">${r.html}</div>`:''}
  ${artlist(r.arts)}
  <p><a class="open-page" href="${esc(rid)}.html">open page &#8594;</a></p>`;
 afterPanel();
}
function afterPanel(){
 pbody.querySelectorAll('a[data-goto]').forEach(a=>a.onclick=e=>{
  e.preventDefault();selectById(a.dataset.goto)});
 openPanel();
 if(window.cairnTypeset)cairnTypeset(pbody);
}
function show(d){
 if(d.type==='claim'){
  pbody.innerHTML=`${d.goal?'<span class="chip goal">GOAL</span> ':''}<span class="chip ${d.status}">${d.status}</span>
   <h2>${esc(d.title)}</h2><code>${d.id}</code>
   ${d.lock?`<p class="hint">claimed (${esc(d.lock)})</p>`:''}
   <div class="stmt">${d.html||'(no statement)'}</div>
   ${artlist(d.arts)}
   <p><a class="open-page" href="${d.id}.html">open page &#8594;</a></p>`;
  afterPanel();
 }else{
  showRoute(d.route);
 }
}
selectById=id=>{const d=byId[id];if(d){selected=d;highlight(d);show(d)}};
node.on('click',(e,d)=>{e.stopPropagation();selected=d;highlight(d);show(d)});
svg.on('click',()=>{selected=null;highlight(null);closePanel()});
function refreshVis(){
 const sd=document.getElementById('showdead').checked;
 const deg={};
 links.forEach(l=>{if(real(l)&&(!l.dead||sd)){
  const a=l.source.id||l.source,b=l.target.id||l.target;
  deg[a]=(deg[a]||0)+1;deg[b]=(deg[b]||0)+1}});
 nodes.forEach(d=>{d.orphan=d.type==='claim'&&!d.root&&!d.goal&&!d.frontier&&!(deg[d.id]>0)});
 node.classed('orphan',d=>d.orphan);
 g.classed('showdead',sd);
 sim.force('charge',d3.forceManyBody().strength(d=>d.orphan?-10:-560));
 linkForce.strength(l=>l.kind==='aff'
  ?((l.source.orphan||l.target.orphan)?0:.03+.1*l.w):.5);
 sim.alpha(.5).restart();
 relabel();
}
document.getElementById('showdead').onchange=refreshVis;
let tk=0;
sim.on('tick',()=>{
 line.attr('x1',l=>l.source.x).attr('y1',l=>l.source.y)
     .attr('x2',l=>l.target.x).attr('y2',l=>l.target.y);
 node.attr('transform',d=>`translate(${d.x},${d.y})`);
 if((++tk%7)===0)relabel();
});
sim.on('end',relabel);
// Centre on a node without losing the reader's zoom level.
window.focusNode=function(d){
 const t=d3.zoomTransform(svg.node());
 svg.transition().duration(420).call(zoom.transform,
  d3.zoomIdentity.translate(W/2-d.x*t.k,H/2-d.y*t.k).scale(t.k));
};
refreshVis();
}
__SEARCH_JS__
</script>
"""


def autolink(html_str, ids):
    """Hyperlink every mention of a known node id in already-rendered HTML."""
    pat = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
    parts = re.split(r"(<[^>]+>)", html_str)
    out, in_a, in_math = [], 0, False
    for part in parts:
        if part.startswith("<"):
            if part.startswith("<a"):
                in_a += 1
            elif part.startswith("</a"):
                in_a = max(0, in_a - 1)
            elif 'class="mathblock"' in part:
                in_math = True
            elif part.startswith("</div") and in_math:
                in_math = False
            out.append(part)
            continue
        # an anchor inside a formula would split the text node KaTeX needs
        if in_a or in_math:
            out.append(part)
            continue
        out.append(pat.sub(
            lambda m: (f'<a href="{m.group(0)}.html">{m.group(0)}</a>'
                       if m.group(0) in ids else m.group(0)), part))
    return "".join(out)


def goal_depths(graph):
    """Derivation distance of each claim from the goal/root anchors,
    through live routes only — the vertical hierarchy of the site."""
    from collections import deque
    anchors = sorted(set(graph.goals) | set(graph.roots))
    depth = {a: 0 for a in anchors}
    dq = deque(anchors)
    while dq:
        q = dq.popleft()
        for rid in graph.routes_into.get(q, []):
            r = graph.routes[rid]
            if r.status == "INVALIDATED":
                continue
            for req in r.get_list("requires"):
                if req in graph.claims and req not in depth:
                    depth[req] = depth[q] + 1
                    dq.append(req)
    return depth


def _web_root():
    """`https://host/owner/repo` for the origin remote, or None.

    Artifacts name files in the repository, so on a published site they should
    be one click from the node that cites them.  Derived from the remote rather
    than configured, so it is right by default and absent when there is no
    remote to be right about."""
    r = _git("remote", "get-url", "origin")
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    m = re.match(r"^(?:git@|ssh://git@)([^:/]+)[:/](.+?)(?:\.git)?$", url)
    if not m:
        m = re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?$", url)
    if not m:
        return None
    return f"https://{m.group(1)}/{m.group(2)}"


def _web_ref():
    r = _git("rev-parse", "--abbrev-ref", "HEAD")
    ref = r.stdout.strip() if r.returncode == 0 else ""
    return ref if ref and ref != "HEAD" else "main"


def artifact_links(paths, root, ref):
    """[(label, href|None)] for an `artifacts:` list.

    Prefer this site's own rendered page for the file, so a reader stays where
    the mathematics is typeset; fall back to the forge only for files that are
    not in the working tree (revision pins) or too large to publish."""
    out = []
    for p in paths:
        p = str(p)
        if p.startswith(("http://", "https://")):
            out.append((p, p))
        elif ":" in p and not os.path.exists(os.path.join(REPO, p)):
            rev, _, path = p.partition(":")
            out.append((p, f"{root}/blob/{rev}/{path}" if root else None))
        elif _repo_has(p):
            REFERENCED_FILES.add(p)
            out.append((p, file_page_name(p)))
        else:
            out.append((p, f"{root}/blob/{ref}/{p}" if root else None))
    return out


FILE_PAGE_CAP = 5000


def write_file_pages(ids, web, ref):
    """Render every referenced repository file as a page on this site.

    Markdown is rendered, so a note reached from a claim gets the same typeset
    mathematics as the claim did; everything else is shown verbatim with a
    line anchor per line, so a `file.ext:123` mention can land on the line."""
    written, seen = 0, set()
    while True:
        todo = sorted(REFERENCED_FILES - seen)
        if not todo:
            break
        for path in todo:
            seen.add(path)
            if written >= FILE_PAGE_CAP:
                continue
            full = os.path.join(REPO, path)
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            src = (f'<a href="{web}/blob/{ref}/{path}" target="_blank" '
                   f'rel="noopener">view source on the forge</a>' if web else "")
            head = (f"<h1><span class='node'>file</span>{html.escape(path)}</h1>"
                    f"<p class='muted'>{src}</p>")
            if path.lower().endswith(".md"):
                body = text.split("---", 2)[-1] if text.startswith("---") else text
                rendered = autolink(md_to_html(body, ids), ids)
            else:
                rows = []
                for k, ln in enumerate(text.split("\n"), 1):
                    rows.append(f'<span class="ln" id="L{k}">{k}</span>'
                                + linkify_prose(html.escape(ln, quote=False)))
                rendered = '<pre class="src">' + "\n".join(rows) + "</pre>"
            with open(os.path.join(SITE_DIR, file_page_name(path)), "w",
                      encoding="utf-8") as fh:
                fh.write(page(path, head + rendered))
            written += 1
    dropped = len(REFERENCED_FILES) - written
    if dropped > 0:
        print(f"note: {dropped} referenced file(s) not published "
              f"(cap {FILE_PAGE_CAP} or unreadable); those links fall back to the forge")
    return written


def generate_site(graph, locks):
    os.makedirs(SITE_DIR, exist_ok=True)
    REFERENCED_FILES.clear()
    web, ref = _web_root(), _web_ref()
    # index = the graph, full viewport
    idset = set(graph.nodes)
    depths = goal_depths(graph)
    data = {"claims": [], "links": [], "junctions": [], "dead": [], "affinity": [],
            "routes": {}, "maxDepth": max(depths.values(), default=0)}
    for cid, c in graph.claims.items():
        data["claims"].append({
            "id": cid, "status": c.status, "root": bool(c.meta.get("root")),
            "goal": bool(c.meta.get("goal")),
            "title": c.title, "impact": graph.claim_impact.get(cid, 0),
            "frontier": cid in graph.frontier,
            "depth": depths.get(cid),
            "lock": fmt_remaining(locks[cid]) if cid in locks else None,
            "arts": artifact_links(c.get_list("artifacts"), web, ref),
            "html": autolink(md_to_html(c.body, idset), idset)})
    for rid, r in graph.routes.items():
        tgt = r.meta.get("target")
        if tgt not in graph.claims:
            continue
        reqs = [q for q in r.get_list("requires") if q in graph.claims]
        dead = r.status == "INVALIDATED"
        killers = graph.invalidated_by.get(rid, [])
        # every route is panel-renderable by id, whether it draws as an edge,
        # a junction or a stub
        data["routes"][rid] = {
            "title": r.title, "target": tgt, "requires": reqs, "dead": dead,
            "killers": killers,
            "arts": artifact_links(r.get_list("artifacts"), web, ref),
            "html": autolink(md_to_html(r.body, idset), idset)}
        if not reqs:
            if dead:
                data["dead"].append({"route": rid, "target": tgt,
                                     "title": r.title, "killers": killers})
            continue  # live direct proofs render as the claim's fill
        rec = {"route": rid, "target": tgt, "title": r.title, "dead": dead}
        if len(reqs) == 1:
            data["links"].append({**rec, "source": reqs[0]})
        else:
            data["junctions"].append({**rec, "requires": reqs})
    # semantic affinity: TF-IDF cosine over statements -> invisible
    # attraction links, so conceptually close claims sit close on screen
    vecs = semantic_vectors(graph.claims)
    _cos = cosine
    cids = list(vecs)
    pairs = []
    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            w = _cos(vecs[cids[i]], vecs[cids[j]])
            if w >= 0.16:
                pairs.append((w, cids[i], cids[j]))
    pairs.sort(reverse=True)
    percap = {}
    for w, x, y in pairs:
        if percap.get(x, 0) < 3 and percap.get(y, 0) < 3:
            data["affinity"].append({"a": x, "b": y, "w": round(min(1.0, w), 2)})
            percap[x] = percap.get(x, 0) + 1
            percap[y] = percap.get(y, 0) + 1
    est = sum(1 for c in graph.claims.values() if c.status == "ESTABLISHED")
    stats = (f"{len(graph.claims)} claims · {est} established · "
             f"{len(graph.routes)} routes · {len(graph.frontier)} frontier holes")
    idx = (INDEX_TMPL.replace("__KATEX__", KATEX)
                     .replace("__PALETTE__", PALETTE)
                     .replace("__SANS__", SANS).replace("__MONO__", MONO)
                     .replace("__SEARCH_JS__", SEARCH_JS)
                     .replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
                     .replace("__STATS__", html.escape(stats)))
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(idx)
    # secondary: plain listing
    B = ["<h1>All nodes</h1>",
         "<table><tr><th>id</th><th>kind</th><th>status</th><th>title</th></tr>"]
    for nid, n in sorted(graph.nodes.items()):
        B.append(f"<tr><td class='art'><a href='{nid}.html'>{nid}</a></td>"
                 f"<td>{n.kind}</td><td>{badge(n.status)}</td>"
                 f"<td><a href='{nid}.html'>{html.escape(n.title)}</a></td></tr>")
    B.append("</table>")
    with open(os.path.join(SITE_DIR, "nodes.html"), "w", encoding="utf-8") as f:
        f.write(page("Cairn — all nodes", "\n".join(B)))
    for nid, n in graph.nodes.items():
        goalmark = (f'<span class="badge" style="background:{GOAL_COLOR}">GOAL</span> '
                    if n.meta.get("goal") else "")
        src = html.escape(n.relpath)
        if _repo_has(n.relpath):
            REFERENCED_FILES.add(n.relpath)
            srclink = f"<a class='fileref' href='{file_page_name(n.relpath)}'>{src}</a>"
        else:
            srclink = (f"<a href='{web}/blob/{ref}/{src}' target='_blank' "
                       f"rel='noopener'>{src}</a>" if web else src)
        B = [f"<h1><span class='node'>{nid}</span> {html.escape(n.title)}</h1>",
             f"<p>{goalmark}{badge(n.status)} <span class='muted'>{n.kind} · "
             f"<span class='art'>{srclink}</span></span></p>"]
        if n.status_reasons:
            B.append("<p class='muted'>" + html.escape("; ".join(n.status_reasons)) + "</p>")
        lock = locks.get(nid)
        if lock:
            B.append(f"<p>🔒 claimed ({fmt_remaining(lock)})</p>")
        def rel(title_, ids):
            ids = [i for i in ids if i in graph.nodes]
            if ids:
                B.append(f"<h2>{title_}</h2><ul class='rel'>")
                B.extend(f"<li>{node_link(graph, i)}</li>" for i in ids)
                B.append("</ul>")

        if n.kind == "claim":
            rel("Routes into this claim", graph.routes_into.get(nid, []))
            rel("Needed by routes", graph.required_by.get(nid, []))
            rel("Invalidates", n.get_list("invalidates"))
            df = n.meta.get("distinct_from") or {}
            if df:
                B.append("<h2>Distinct from</h2><ul class='rel'>")
                for k, why in df.items():
                    B.append(f"<li>{node_link(graph, k)}<br>"
                             f"<span class='muted'>{html.escape(str(why))}</span></li>")
                B.append("</ul>")
        else:
            rel("Target", [n.meta.get("target")])
            rel("Requires", n.get_list("requires"))
            rel("Invalidated by", graph.invalidated_by.get(nid, []))
        arts = artifact_links(n.get_list("artifacts"), web, ref)
        if arts:
            B.append("<h2>Artifacts</h2><ul class='rel'>")
            for label, href in arts:
                lab = html.escape(label)
                B.append(f"<li class='art'><a href='{html.escape(href)}' "
                         f"target='_blank' rel='noopener'>{lab}</a></li>"
                         if href else f"<li class='art'>{lab}</li>")
            B.append("</ul>")
        B.append("<h2>Statement</h2>")
        B.append(autolink(md_to_html(n.body, idset), idset)
                 if n.body else "<p class='muted'>(no body)</p>")
        with open(os.path.join(SITE_DIR, f"{nid}.html"), "w", encoding="utf-8") as f:
            f.write(page(f"{nid}", "\n".join(B)))
    write_file_pages(idset, web, ref)
    return SITE_DIR


# ---------------------------------------------------------------------------
# git helpers (for check/preview --changed)
# ---------------------------------------------------------------------------

def _git(*argv):
    return subprocess.run(["git", "-C", REPO] + list(argv),
                          capture_output=True, text=True)


def changed_research_files():
    """Ids of research/*.md changed vs HEAD (staged, unstaged, untracked)."""
    out = set()
    r = _git("status", "--porcelain", "--", "research")
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        p = line[3:].split(" -> ")[-1].strip().strip('"')
        if (p.startswith("research/") and p.endswith(".md")
                and "/" not in p[len("research/"):-3]
                and os.path.basename(p) not in NON_NODE_FILES):
            out.add(os.path.basename(p)[:-3])
    return out


def head_graph():
    """Compile the graph as of HEAD (empty if research/ not committed yet)."""
    r = _git("ls-tree", "-r", "--name-only", "HEAD", "--", "research")
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="head-", dir=STATE_DIR)
    if r.returncode == 0:
        for p in r.stdout.splitlines():
            if (not p.endswith(".md") or os.path.basename(p) in NON_NODE_FILES
                    or "/" in p[len("research/"):]):
                continue
            show = _git("show", f"HEAD:{p}")
            if show.returncode == 0:
                with open(os.path.join(tmp, os.path.basename(p)), "w", encoding="utf-8") as f:
                    f.write(show.stdout)
    graph, errors = compile_graph(research_dir=tmp, repo=REPO)
    return graph, errors, tmp


def previous_graph(changed_ids):
    """Compile the graph as of HEAD, cheaply: seed a scratch dir from the
    working tree and re-fetch only the changed files from HEAD (one
    `git show` per changed file instead of one per node). Returns None
    when git is unavailable or the change set is degenerate."""
    if changed_ids is None or not changed_ids or len(changed_ids) > 200:
        return None
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="prev-", dir=STATE_DIR)
    try:
        try:
            names = os.listdir(RESEARCH_DIR)
        except OSError:
            return None
        for f in names:
            if (not f.endswith(".md") or f in NON_NODE_FILES
                    or f[:-3] in changed_ids):
                continue
            src = os.path.join(RESEARCH_DIR, f)
            if os.path.isfile(src):
                shutil.copy(src, os.path.join(tmp, f))
        for cid in changed_ids:
            show = _git("show", f"HEAD:research/{cid}.md")
            if show.returncode == 0:
                with open(os.path.join(tmp, cid + ".md"), "w", encoding="utf-8") as f:
                    f.write(show.stdout)
        graph, _ = compile_graph(research_dir=tmp, repo=REPO)
        return graph
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remaining_cost(graph, assume=frozenset()):
    """Open holes on the cheapest MAPPED plan for each claim.

    Established (or assumed) claims cost 0; an open claim with no live
    routes into it is itself one hole; a decomposed claim costs its best
    route's sum. None = no finite mapped plan (route-finding needed, not
    lemma-proving). This measures the mapped decomposition only — any
    claim can still be attacked directly, but that is not a plan the
    graph knows about."""
    INF = float("inf")
    done = graph.established | set(assume)
    plans = {}
    for r in graph.routes.values():
        if r.status == "INVALIDATED":
            continue
        tgt = r.meta.get("target")
        if tgt in graph.claims and tgt not in done:
            plans.setdefault(tgt, []).append(
                [q for q in r.get_list("requires") if q in graph.claims])
    cost = {}
    for c in graph.claims:
        cost[c] = 0 if c in done else (INF if c in plans else 1)
    for _ in range(len(graph.claims) + 1):
        changed = False
        for c, ps in plans.items():
            best = min((sum(cost[q] for q in reqs) for reqs in ps), default=INF)
            if best < cost[c]:
                cost[c] = best
                changed = True
        if not changed:
            break
    return {c: (None if v == INF else v) for c, v in cost.items()}


def kinetic_delta(old, new):
    """What the working tree changed in derived state, phrased forward:
    establishments, routes now one prerequisite from complete, fresh
    invalidations, and plan-cost movement at the goals and roots. This
    is the build-system moment — 'three targets just became buildable' —
    printed while the author's context is still loaded."""
    d = {"established": sorted(new.established - old.established),
         "last_missing": [], "invalidated": [], "plan_cost": []}
    for rid, r in sorted(new.routes.items()):
        if r.status != "OPEN" or len(r.blocked_on) != 1:
            continue
        o = old.routes.get(rid)
        if o is None:
            d["last_missing"].append(
                (rid, r.meta.get("target"), r.blocked_on[0], None))
        elif o.status == "OPEN" and len(o.blocked_on) > 1:
            d["last_missing"].append(
                (rid, r.meta.get("target"), r.blocked_on[0], len(o.blocked_on)))
    for rid in sorted(new.invalidated - old.invalidated):
        if rid in old.routes:
            d["invalidated"].append(
                (rid, ", ".join(new.invalidated_by.get(rid, ()))))
    anchors = sorted(set(new.goals) | set(new.roots))
    if anchors:
        oc, nc = remaining_cost(old), remaining_cost(new)
        for gid in anchors:
            if gid not in old.claims:
                continue
            a, b = oc.get(gid), nc.get(gid)
            if a != b and b is not None:
                kind = "goal" if gid in new.goals else "root"
                d["plan_cost"].append((kind, gid, a, b))
    return d


ATTEMPTS_HEADING = re.compile(r"^\s{0,3}#{2,6}\s*(attempts?|attack log)\b.*$",
                              re.I | re.M)


def missing_attempts(body):
    """True when the body has no nonempty '## Attempts' section."""
    m = ATTEMPTS_HEADING.search(body)
    if not m:
        return True
    rest = body[m.end():]
    nxt = re.search(r"^\s{0,3}#{1,6}\s", rest, flags=re.M)
    content = rest[:nxt.start()] if nxt else rest
    return not content.strip()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def write_outputs(graph):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, "graph.json"), "w", encoding="utf-8") as f:
        json.dump(graph.to_json(), f, indent=1)
    with open(os.path.join(RESEARCH_DIR, "FRONTIER.md"), "w", encoding="utf-8") as f:
        f.write(generate_frontier_md(graph, all_locks()))


def cmd_check(args):
    graph, errors = compile_graph()
    changed = changed_research_files()
    only = None
    if args.changed:
        only = changed
        if only is None:
            print("WARNING: git unavailable; checking everything", file=sys.stderr)
    dups = duplicate_findings(graph, only_ids=only)
    policy_sev = "error" if args.changed else "warning"
    for cid, cand, score in dups:
        errors.append((policy_sev, f"possible duplicate claim: {cid} vs {cand} "
                       f"(similarity {score}); if genuinely distinct, add to {cid}:\n"
                       f"  distinct_from:\n    {cand}: <why this is not that>"))
    # naming a hole is not finishing it: a NEW open claim must record at
    # least one attack (or say why the attack is deferred) before parking
    prev = previous_graph(changed)
    new_open = []
    if prev is not None:
        new_open = sorted(cid for cid in graph.claims
                          if cid not in prev.nodes
                          and graph.claims[cid].status == "OPEN")
    parked = [cid for cid in new_open
              if not graph.claims[cid].meta.get("goal")
              and missing_attempts(graph.claims[cid].body)]
    for cid in parked:
        errors.append((policy_sev,
                       f"{graph.claims[cid].relpath}: new open claim {cid} parks a "
                       "hole with no recorded attack — add an '## Attempts' section: "
                       "at least one approach and where it dies, or one line on why "
                       "the attack is deferred. Writing down where the obvious "
                       "attack fails is where the next one usually comes from"))
    nerr = report_errors(errors, fail_on_warning=args.strict)
    if graph.unreachable_open:
        print("to reconnect an unreachable claim: add a route from a reachable "
              "claim to it, or mark it root: true if it is a genuine program "
              "target. nearest reachable claims by similarity:", file=sys.stderr)
        for cid in graph.unreachable_open:
            near = [m.id for _, m in similar_nodes(
                graph.claims[cid].title, graph.claims, limit=4, threshold=0.2,
                exclude={cid}, min_overlap=1) if m.reachable][:2]
            print(f"  {cid}" + (f" ~ {', '.join(near)}" if near else " ~ (none)"),
                  file=sys.stderr)
    # momentum, printed while the author's context is still loaded
    delta = kinetic_delta(prev, graph) if prev is not None else None
    n_unlocked = 0
    if delta and any(delta.values()):
        n_unlocked = sum(len(v) for v in delta.values())
        print("unlocked by this change:")
        if delta["established"]:
            print("  established: " + ", ".join(delta["established"]))
        for rid, tgt, miss, was in delta["last_missing"]:
            tail = "(new route)" if was is None else f"(was {was} open)"
            print(f"  route {rid} -> {tgt}: missing only {miss} {tail}")
        for rid, by in delta["invalidated"]:
            print(f"  route {rid}: invalidated" + (f" by {by}" if by else ""))
        for kind, gid, a, b in delta["plan_cost"]:
            was_s = "no finite mapped plan" if a is None else str(a)
            print(f"  {kind} {gid}: cheapest mapped plan {was_s} -> {b} open hole(s)")
    # the compose check: a fresh hole next to established claims is often
    # already decided by them — the author is the one person positioned
    # to notice, right now
    hints = 0
    if new_open:
        vecs = semantic_vectors(graph.claims)
        for cid in new_open:
            near = sorted(((cosine(vecs[cid], vecs[oid]), oid)
                           for oid in graph.established if oid != cid),
                          reverse=True)[:3]
            near = [(s, o) for s, o in near if s >= 0.12]
            if near:
                hints += 1
                print(f"note: {cid} is near established "
                      + ", ".join(f"{o} ({s:.2f})" for s, o in near)
                      + " — check whether they already decide it")
    TELEMETRY_EXTRA.update({"unlocked": n_unlocked, "parked": len(parked),
                            "hints": hints})
    write_outputs(graph)
    print(f"compiled {len(graph.claims)} claims + {len(graph.routes)} routes -> "
          f".cairn/cache/graph.json, research/FRONTIER.md"
          + ("" if errors else " — check clean"))
    if not nerr:
        return EXIT_OK
    n_policy = (len(dups) + len(parked)) if (args.changed or args.strict) else 0
    return EXIT_INVALID if nerr - n_policy > 0 else EXIT_DUP


def cmd_preview(args):
    old, _, tmp = head_graph()
    shutil.rmtree(tmp, ignore_errors=True)
    new, errors = compile_graph()
    L = ["PROPOSED GRAPH CHANGE (working tree vs HEAD)", ""]
    delta = {"added": [], "removed": [], "status_changed": [], "dup_warnings": [],
             "direct_proof_assertions": [], "frontier_added": [], "frontier_removed": []}
    for nid in sorted(set(new.nodes) - set(old.nodes)):
        n = new.nodes[nid]
        delta["added"].append(nid)
        L.append(f"+ {nid}  [{n.kind}] {n.title}")
        if n.kind == "route" and not n.get_list("requires"):
            delta["direct_proof_assertions"].append(nid)
            L.append(f"    NOTE: requires: [] — asserts a COMPLETE PROOF of {n.meta.get('target')}")
        elif (n.kind == "claim" and n.status == "OPEN"
                and not n.meta.get("goal") and missing_attempts(n.body)):
            L.append("    NOTE: parks a hole with no '## Attempts' section "
                     "(an approach and where it dies)")
    for nid in sorted(set(old.nodes) - set(new.nodes)):
        delta["removed"].append(nid)
        L.append(f"- {nid}  [{old.nodes[nid].kind}] {old.nodes[nid].title}")
    L += ["", "Derived consequences:"]
    for nid in sorted(set(new.nodes) & set(old.nodes)):
        a, b = old.nodes[nid].status, new.nodes[nid].status
        if a != b:
            delta["status_changed"].append({"id": nid, "from": a, "to": b})
            L.append(f"  {nid}: {a} -> {b}")
    delta["frontier_added"] = sorted(set(new.frontier) - set(old.frontier))
    delta["frontier_removed"] = sorted(set(old.frontier) - set(new.frontier))
    L += [f"  new frontier hole: {c}" for c in delta["frontier_added"]]
    L += [f"  frontier hole resolved/absorbed: {c}" for c in delta["frontier_removed"]]
    if not (delta["status_changed"] or delta["frontier_added"] or delta["frontier_removed"]):
        L.append("  (no derived state changes)")
    dups = duplicate_findings(new, only_ids=set(delta["added"]))
    if dups:
        L += ["", "Potential duplicates:"]
        for cid, cand, score in dups:
            delta["dup_warnings"].append({"new": cid, "existing": cand, "score": score})
            L.append(f"  {cid} strongly overlaps {cand} (similarity {score})")
    errs = [m for s, m in errors if s == "error"]
    if errs:
        delta["errors"] = errs
        L += ["", "Errors in working tree:"] + [f"  {e}" for e in errs]
    L += ["", "No canonical state committed."]
    return emit(args, {"status": "ok", **delta}, "\n".join(L),
                EXIT_INVALID if errs else (EXIT_DUP if dups else EXIT_OK))


def cmd_frontier(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    locks = all_locks()
    only_goal = getattr(args, "goal", None)
    if only_goal and only_goal not in graph.claims:
        unknown_node(graph, only_goal)
    flat_holes = sorted(graph.frontier, key=lambda q: -graph.claim_impact[q])
    payload = {"status": "ok", "frontier": [
        {"id": q, "title": graph.claims[q].title, "impact": graph.claim_impact[q],
         "claimed": q in locks} for q in flat_holes]}
    if getattr(args, "flat", False) or (not graph.goals and not only_goal):
        human = "\n".join(claim_line(graph.claims[q], graph, locks) for q in flat_holes)
        return emit(args, payload, human or "(no open holes)")

    attempts = lock_attempts()
    goals, elsewhere = frontier_view(graph, only_goal=only_goal)
    L, payload["goals"] = [], []
    for g in goals:
        gid = g["id"]
        c = graph.claims[gid]
        L.append(f"TOWARD {gid} [{c.status}] — {c.title}")
        gp = {"id": gid, "node_status": c.status, "connected": g["connected"],
              "holes": []}
        if c.status == "ESTABLISHED":
            L.append("  ✓ established — nothing further needed")
        elif not g["holes"]:
            L += [f"  no live route-tree under it — no known path exists yet.",
                  f"  The needed work is route-finding, not lemma-proving: "
                  f"decompose it (`cairn why {gid}`)."]
        else:
            if g["connected"] is False:
                L.append("  (no complete route-tree yet: resolving every hole below "
                         "still doesn't reach the goal — a route is missing somewhere)")
            for h in g["holes"]:
                L.append("  " + claim_line(graph.claims[h], graph, locks))
                notes = []
                if h in g["necessary"]:
                    notes.append(f"★ on every live path to {gid}")
                path = chain_to(graph, gid, h)
                if path and len(path) > 1:
                    seg = path if len(path) <= 6 else path[:5] + ["…", path[-1]]
                    notes.append("path: " + " -> ".join(seg))
                prior = attempts.get(h, 0) - (1 if h in locks else 0)
                if prior > 0:
                    notes.append(f"{prior} prior attempt(s)"
                                 + (" — consider decomposing instead of another "
                                    "direct attack" if prior >= 2 else ""))
                L += ["      " + x for x in notes]
                gp["holes"].append({
                    "id": h, "title": graph.claims[h].title,
                    "impact": graph.claim_impact[h], "claimed": h in locks,
                    "necessary": h in g["necessary"], "path_to_goal": path,
                    "prior_attempts": max(prior, 0)})
        payload["goals"].append(gp)
        L.append("")
    if elsewhere and not only_goal:
        L.append("ELSEWHERE (on no live path to any goal)")
        L += ["  " + claim_line(graph.claims[h], graph, locks) for h in elsewhere]
        payload["elsewhere"] = [
            {"id": h, "title": graph.claims[h].title,
             "impact": graph.claim_impact[h], "claimed": h in locks}
            for h in elsewhere]
    return emit(args, payload, "\n".join(L).rstrip() or "(no open holes)")


def cmd_context(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    packet = context_packet(graph, args.id, all_locks(), args.budget)
    n = graph.nodes[args.id]
    return emit(args, {"status": "ok", "id": args.id, "kind": n.kind,
                       "node_status": n.status, "packet": packet}, packet)


def cmd_search(args):
    graph, errors = compile_graph()
    if args.cmd == "relevant" or args.similar:
        n = graph.nodes.get(args.query)
        text = (n.title + " " + n.body[:400]) if n else args.query
        hits = similar_nodes(text, graph.nodes, limit=args.limit, threshold=0.2,
                             exclude={args.query}, min_overlap=1)
        payload = {"status": "ok", "results": [
            {"id": m.id, "kind": m.kind, "node_status": m.status,
             "title": m.title, "score": s} for s, m in hits]}
        human = "\n".join(f"{m.id:<44} [{m.kind}/{m.status}] {m.title}"
                          for _, m in hits) or "(nothing similar)"
        return emit(args, payload, human)
    q = _tokens(args.query)
    scored = []
    for n in graph.nodes.values():
        hay = _tokens(n.title + " " + n.id.replace("-", " ") + " " + n.body)
        inter = len(q & hay)
        if inter:
            scored.append((inter / max(1, len(q)), n.id, n.kind, n.status, n.title))
    if args.notes and os.path.isdir(NOTES_DIR):
        for base, _, files in os.walk(NOTES_DIR):
            for fn in files:
                if not fn.endswith((".md", ".txt")):
                    continue
                p = os.path.join(base, fn)
                try:
                    hay = _tokens(open(p, encoding="utf-8", errors="ignore").read())
                except OSError:
                    continue
                inter = len(q & hay)
                if inter:
                    scored.append((inter / max(1, len(q)),
                                   os.path.relpath(p, REPO), "note", "-", fn))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = scored[:args.limit]
    payload = {"status": "ok", "results": [
        {"id": i, "kind": k, "node_status": st, "title": t, "score": round(sc, 2)}
        for sc, i, k, st, t in top]}
    human = "\n".join(f"{i:<44} [{k}/{st}] {t}" for _, i, k, st, t in top) or "(no matches)"
    return emit(args, payload, human)


def cmd_impact(args):
    graph, errors = compile_graph()
    n = graph.nodes.get(args.id)
    if n is None:
        unknown_node(graph, args.id)
    if n.kind != "claim":
        raise SystemExit(f"{args.id!r} is a route; impact takes a claim")
    est1, inv1, _, _ = graph._solve(forced=frozenset([args.id]))
    newly = sorted(est1 - graph.established - {args.id})
    newly_dead = sorted(inv1 - graph.invalidated)
    direct = graph.required_by.get(args.id, [])
    L = [f"IF {args.id} WERE ESTABLISHED:"]
    L += [f"  claim flips OPEN -> ESTABLISHED: {c}" for c in newly] or ["  no claims flip"]
    L += [f"  route becomes INVALIDATED: {r}" for r in newly_dead]
    L.append("  routes directly waiting on it: " + (", ".join(direct) or "(none)"))
    payload = {"status": "ok", "id": args.id, "would_establish": newly,
               "would_invalidate": newly_dead, "directly_needed_by": direct}
    return emit(args, payload, "\n".join(L))


def cmd_lock(args):
    lock, holder = acquire_lock(args.id, parse_ttl(args.ttl))
    locks = all_locks()
    held = [{"id": nid, "expires_at": lk["expires_at"]}
            for nid, lk in locks.items()]
    roster = ("all active locks: "
              + ", ".join(f"{nid} ({fmt_remaining(lk)})" for nid, lk in locks.items()))
    if lock is None:
        return emit(args, {"status": "claimed", "id": args.id,
                           "expires_at": holder["expires_at"], "locks": held},
                    f"CLAIMED {args.id} — {fmt_remaining(holder)}\n"
                    f"(locks are identity-free; if this is your own earlier "
                    f"lock it is still active)\n" + roster,
                    EXIT_LEASE)
    return emit(args, {"status": "locked", "id": args.id,
                       "expires_at": lock["expires_at"], "locks": held},
                f"LOCKED {args.id} "
                f"expires={time.strftime('%H:%M:%S', time.localtime(lock['expires_at']))}"
                f"\n" + roster)


def cmd_unlock(args):
    if read_lock(args.id) is None:
        return emit(args, {"status": "unlocked", "id": args.id}, f"no active lock on {args.id}")
    os.unlink(_lock_path(args.id))
    return emit(args, {"status": "unlocked", "id": args.id}, f"UNLOCKED {args.id}")


def cmd_site(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    out = generate_site(graph, all_locks())
    print(f"site -> {os.path.relpath(out, REPO)}/index.html")
    if args.serve:
        import functools
        from http.server import HTTPServer, SimpleHTTPRequestHandler
        handler = functools.partial(SimpleHTTPRequestHandler, directory=out)
        srv = HTTPServer(("127.0.0.1", args.port), handler)
        print(f"serving http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return EXIT_OK


def cmd_status(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    locks = all_locks()
    est = sum(1 for c in graph.claims.values() if c.status == "ESTABLISHED")
    L = [f"{len(graph.claims)} claims ({est} established) · "
         f"{len(graph.routes)} routes ({len(graph.invalidated)} invalidated) · "
         f"{len(graph.frontier)} frontier holes · {len(locks)} active claims"]
    if graph.goals:
        L.append("goals:")
        L += [f"  {gid} [{graph.claims[gid].status}] {graph.claims[gid].title}"
              for gid in graph.goals]
    # goal-cone holes first: agents should see the work that serves a
    # goal before the merely well-connected work (necessity ranking and
    # per-hole paths live in `cairn frontier`, which is allowed to be
    # slower than status)
    cone = set()
    for gid in graph.goals:
        if graph.claims[gid].status == "OPEN":
            cone |= goal_cone(graph, gid)
    toward = [q for q in graph.frontier if q in cone]
    pool = toward or graph.frontier
    top = sorted(pool, key=lambda q: -graph.claim_impact[q])[:5]
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
               "frontier": len(graph.frontier), "toward_goals": len(toward),
               "goals": [{"id": g, "node_status": graph.claims[g].status}
                         for g in graph.goals],
               "locks": sorted(locks)}
    return emit(args, payload, "\n".join(L))


def stakes_lines(graph, cid, waiting):
    """Both payoffs of an open claim, so a hole reads as a fork with two
    prizes rather than inventory: what establishing it completes and
    cascades, and what a refutation (an established negation) would
    dead-end."""
    L = []
    est2, _, _, _ = graph._solve(forced=frozenset(graph.established | {cid}))
    completes = [rid for rid in waiting if graph.routes[rid].blocked_on == [cid]]
    comp_tgts = {graph.routes[rid].meta.get("target") for rid in completes}
    cascade = sorted(est2 - graph.established - {cid} - comp_tgts)
    gains = []
    if completes:
        gains.append("completes " + ", ".join(
            f"{rid} -> {graph.routes[rid].meta.get('target')}" for rid in completes))
    if cascade:
        gains.append("cascade also establishes: " + ", ".join(cascade))
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
    if gains:
        L.append("if established: " + "; ".join(gains))
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


def cmd_why(args):
    # Line 1 is always self-identifying (`<id> [STATUS] — …`): agents
    # habitually pipe this through `head -1` and must learn something.
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    n = graph.nodes.get(args.id)
    if n is None:
        unknown_node(graph, args.id)
    L = []
    if n.kind == "route":
        reqs = n.get_list("requires")
        L.append(f"{args.id} [{n.status}] route — {n.title}")
        L.append(f"  {' AND '.join(reqs) if reqs else '(direct proof)'} "
                 f"=> {n.meta.get('target')}")
        L += ["  " + r for r in n.status_reasons]
    else:
        if n.status == "ESTABLISHED":
            rid = graph.provenance.get(args.id)
            L.append(f"{args.id} [ESTABLISHED"
                     + (f" via {rid}" if rid else "") + f"] — {n.title}")
            L += ["derivation:"] + ["  " + x for x in derivation_lines(graph, args.id)]
        else:
            L.append(f"{args.id} [OPEN] — {n.title}")
            locks = all_locks()
            live = [rid for rid in graph.routes_into.get(args.id, [])
                    if graph.routes[rid].status != "INVALIDATED"]
            if live:
                L.append("decomposition (routes into it; ✓ = already in hand):")
                L += ["  " + x for x in
                      render_tree(graph, args.id, locks, max_depth=4)[1:]]
            else:
                L.append("frontier hole: no live routes into it — prove it directly "
                         "(a route with requires: []) or decompose it with a new route")
                for rid in graph.routes_into.get(args.id, []):
                    r = graph.routes[rid]
                    if r.status == "INVALIDATED":
                        L.append(f"  dead: {rid} — {'; '.join(r.status_reasons)}")
        chain = why_chain(graph, args.id)
        if chain:
            L.append("why it matters: "
                     + " -> ".join([chain[0][0]] + [c for _, _, c in chain]))
        waiting = [rid for rid in graph.required_by.get(args.id, [])
                   if graph.routes[rid].status != "INVALIDATED"]
        if waiting:
            L.append("live routes waiting on it: " + ", ".join(waiting))
        if n.status == "OPEN":
            L += stakes_lines(graph, args.id, waiting)
    payload = {"status": "ok", "id": args.id, "kind": n.kind,
               "node_status": n.status, "why": L}
    return emit(args, payload, "\n".join(L))




# ---------------------------------------------------------------------------
# Telemetry: every invocation appends one JSONL record. Observability
# state (like locks) — lives in .cairn/, never committed, never able to
# affect research state. Purpose: see how agents actually use the tool
# (and which commands they never touch) to drive design changes.
# ---------------------------------------------------------------------------

TELEMETRY_EXTRA = {}  # commands may deposit counters (e.g. banner sizes)


def record_telemetry(cmd, argv, code, ms):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": cmd,
                 "argv": argv, "exit": code, "ms": ms}
        if TELEMETRY_EXTRA:
            entry["extra"] = dict(TELEMETRY_EXTRA)
        with open(TELEMETRY, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # telemetry must never break a command


def read_telemetry():
    entries = []
    try:
        with open(TELEMETRY, encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return entries


def cmd_telemetry(args):
    entries = read_telemetry()
    if args.tail:
        tail = entries[-args.tail:]
        payload = {"status": "ok", "entries": tail}
        human = "\n".join(
            f"{e['ts']}  {e['cmd']:<10} "
            f"exit={e['exit']} {e['ms']}ms  {' '.join(map(str, e.get('argv', [])))}"
            for e in tail) or "(no telemetry)"
        return emit(args, payload, human)
    if not entries:
        return emit(args, {"status": "ok", "total": 0}, "(no telemetry yet)")
    per_cmd, per_exit = {}, {}
    for e in entries:
        c = per_cmd.setdefault(e["cmd"], {"n": 0, "errors": 0, "ms": []})
        c["n"] += 1
        c["errors"] += e["exit"] != 0
        c["ms"].append(e.get("ms", 0))
        per_exit[str(e["exit"])] = per_exit.get(str(e["exit"]), 0) + 1
    unused = sorted(set(COMMANDS) - {"telemetry", "build", "relevant"} - set(per_cmd))
    L = [f"{len(entries)} invocations, {entries[0]['ts']} .. {entries[-1]['ts']}", "",
         f"{'command':<12} {'n':>5} {'errs':>5} {'med ms':>7}"]
    stats = {}
    for cmd in sorted(per_cmd, key=lambda c: -per_cmd[c]["n"]):
        c = per_cmd[cmd]
        med = sorted(c["ms"])[len(c["ms"]) // 2]
        stats[cmd] = {"n": c["n"], "errors": c["errors"], "median_ms": med}
        L.append(f"{cmd:<12} {c['n']:>5} {c['errors']:>5} {med:>7}")
    L += ["", "exit codes: " + ", ".join(f"{k}: {v}" for k, v in sorted(per_exit.items()))]
    if unused:
        L += ["", "never used (candidates to rethink or cut): " + ", ".join(unused)]
    payload = {"status": "ok", "total": len(entries), "per_command": stats,
               "per_exit": per_exit, "never_used": unused}
    return emit(args, payload, "\n".join(L))


COMMANDS = {}


def main():
    COMMANDS.update({
        "check": cmd_check, "build": cmd_check, "preview": cmd_preview,
        "status": cmd_status, "frontier": cmd_frontier, "why": cmd_why,
        "context": cmd_context, "search": cmd_search, "relevant": cmd_search,
        "impact": cmd_impact, "lock": cmd_lock, "unlock": cmd_unlock,
        "site": cmd_site, "telemetry": cmd_telemetry})
    class Parser(argparse.ArgumentParser):
        def error(self, message):  # usage errors must not collide with exit 2
            self.print_usage(sys.stderr)
            self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")

    p = Parser(prog="cairn", description=__doc__.split("\n")[0],
               epilog="exit codes: 0 ok · 2 policy findings (duplicates, "
                      "unattacked new holes) · 3 already claimed · 4 invalid "
                      "graph · 64 usage · 1 runtime error. Env: CAIRN_ROOT "
                      "overrides project-root discovery.")
    p.add_argument("--version", action="version", version=f"cairn {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_, *, jsonable=True, node_id=False, aliases=()):
        sp = sub.add_parser(name, help=help_, aliases=list(aliases))
        if jsonable:
            sp.add_argument("--json", action="store_true")
        if node_id:
            sp.add_argument("id")
        return sp

    ck = add("check", "compile + lint + duplicate detection; refresh FRONTIER.md",
             jsonable=False, aliases=("build",))
    ck.add_argument("--changed", action="store_true",
                    help="duplicates and unattacked new holes are errors "
                         "for files changed vs HEAD")
    ck.add_argument("--strict", action="store_true", help="fail on warnings")
    add("preview", "research-state delta of the working tree vs HEAD")
    add("status", "one-screen program state: goals, frontier, locks")
    fr = add("frontier", "open holes grouped by the goals they serve "
             "(necessity-ranked, with the path each hole unblocks)")
    fr.add_argument("--goal", metavar="ID",
                    help="restrict to holes on live paths into this claim")
    fr.add_argument("--flat", action="store_true",
                    help="ungrouped impact-ranked list (the pre-2.3 view)")
    add("why", "derivation if established; decomposition, why-it-matters and "
        "stakes-both-ways if open", node_id=True)
    cx = add("context", "bounded context packet (statement, derivation, routes, "
             "reusable claims, dead space)", node_id=True)
    cx.add_argument("--budget", type=int, default=8000, help="approx token budget")
    se = add("search", "lexical search over the graph (and notes/); "
             "--similar ranks by similarity to a node id or free text",
             aliases=("relevant",))
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=10)
    se.add_argument("--notes", action="store_true")
    se.add_argument("--similar", action="store_true",
                    help="similarity mode (what `relevant` implies)")
    add("impact", "what would change if this claim were established", node_id=True)
    lk = add("lock", "claim a hole for --ttl (advisory; everyone is one team)",
             node_id=True)
    lk.add_argument("--ttl", default="45m")
    add("unlock", "release a claim", node_id=True)
    st = add("site", "generate the static HTML site", jsonable=False)
    st.add_argument("--serve", action="store_true",
                    help="serve the generated site locally")
    st.add_argument("--port", type=int, default=8000)
    tl = add("telemetry", "usage summary: what agents actually run")
    tl.add_argument("--tail", type=int, help="show the last N raw entries")

    if len(sys.argv) == 1:
        p.print_help()
        sys.exit(EXIT_USAGE)
    args = p.parse_args()
    fn = COMMANDS[args.cmd]
    t0 = time.time()
    try:
        code = fn(args)
    except BaseException as e:
        code = e.code if isinstance(e, SystemExit) and isinstance(e.code, int) else 1
        if args.cmd != "telemetry":
            record_telemetry(args.cmd, sys.argv[1:], code, int((time.time() - t0) * 1000))
        if (isinstance(e, SystemExit) and isinstance(e.code, str)
                and getattr(args, "json", False)):
            print(json.dumps({"status": "error", "error": e.code}, indent=1))
            print(e.code, file=sys.stderr)
            sys.exit(1)
        raise
    if args.cmd != "telemetry":
        record_telemetry(args.cmd, sys.argv[1:], code, int((time.time() - t0) * 1000))
    sys.exit(code)


if __name__ == "__main__":
    main()
