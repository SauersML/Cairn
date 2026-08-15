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
state), frontier, why (how a node was established / why it matters),
context --budget (statement, derivation, routes, reusable claims, dead
space in one bounded packet), search [--notes|--similar] (alias:
relevant = search --similar), impact, lock/unlock (advisory TTL claims —
identity-free: everyone is one team), site [--serve], telemetry. Claims
are scheduler state, never committed into mathematical history.

Exit categories (stable, for agents): 0 ok, 2 duplicate candidates,
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

__version__ = "2.1.0"

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

        for cid, c in self.claims.items():
            if c.status == "OPEN" and not c.reachable:
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


def report_errors(errors, fail_on_warning=False):
    n = 0
    for sev, msg in errors:
        print(f"{sev.upper()}: {msg}", file=sys.stderr)
        n += sev == "error" or (fail_on_warning and sev == "warning")
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


def claim_line(c, graph, locks, width=52):
    lock = locks.get(c.id)
    lockmark = f"  🔒 {fmt_remaining(lock)}" if lock else ""
    title = c.title if len(c.title) <= width else c.title[:width - 1] + "…"
    return f"{c.id:<36}  {title:<{width}}  {MARK.get(c.status, c.status)}{lockmark}"


def why_chain(graph, cid, limit=8):
    from collections import deque
    prev, dq = {r: None for r in graph.roots}, deque(graph.roots)
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
    L += ["## Frontier holes (open, reachable, undecomposed)", ""]
    if not graph.frontier:
        L.append("*(none)*")
    for cid in sorted(graph.frontier, key=lambda q: -graph.claim_impact[q]):
        c = graph.claims[cid]
        lock = locks.get(cid)
        who = f" — 🔒 claimed ({fmt_remaining(lock)})" if lock else " — unclaimed"
        L.append(f"- **{cid}** [{graph.claim_impact[cid]} live route(s) need it] "
                 f"[{c.title}]({cid}.md){who}")
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
SANS = ('-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,'
        'Helvetica,Arial,sans-serif')
SITE_CSS = """
body{font:15px/1.55 SANS;max-width:60em;margin:2em auto;padding:0 1em;color:#1c1e1c}
a{color:#4f46e5;text-decoration:none}a:hover{text-decoration:underline}
code,pre{font:13px/1.45 Menlo,monospace;background:#f4f4f2}
pre{padding:.8em;overflow-x:auto;border:1px solid #e5e7e3}
.badge{display:inline-block;padding:.1em .55em;border-radius:.8em;color:#fff;font:700 11px SANS;letter-spacing:.04em}
.node{font:13px Menlo,monospace}
h1{font-size:1.4em}h2{font-size:1.05em;border-bottom:1px solid #e5e7e3;padding-bottom:.2em}
ul.rel{list-style:none;padding-left:0}ul.rel li{margin:.25em 0}
.muted{color:#8b9089}.tree{white-space:pre;font:13px/1.5 Menlo,monospace;background:#fafaf8;border:1px solid #e5e7e3;padding:1em;overflow-x:auto}
table{border-collapse:collapse}td,th{border:1px solid #e5e7e3;padding:.25em .6em;font-size:.9em}
""".replace("SANS", SANS)
MATHJAX = ('<script>MathJax={tex:{inlineMath:[["$","$"],["\\\\(","\\\\)"]]}};</script>'
           '<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>')


def md_to_html(md):
    out, in_code, in_list, para = [], False, False, []

    def inline(s):
        s = html.escape(s, quote=False)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<em>\1</em>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
        return s

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
            out.append("<pre>" if not in_code else "</pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
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
        out.append("</pre>")
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
    return (f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{SITE_CSS}</style>{MATHJAX}<body>{body_html}</body>")


INDEX_TMPL = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cairn</title>
<script>MathJax={tex:{inlineMath:[["$","$"]]}};</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>
:root{--bg:#fafaf9;--ink:#1c1e1c;--mut:#8b9089;--line:#e5e7e3;
--est:#178a5e;--open:#c08a00;--dead:#c43c2e;--goal:#4f46e5;--edge:#9aa096}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{display:flex;align-items:center;gap:1.1em;padding:.55em 1.1em;
border-bottom:1px solid var(--line);background:#fff}
.wordmark{font-size:12px;font-weight:700;letter-spacing:.3em}
.stats{color:var(--mut);font-size:12px;font-variant-numeric:tabular-nums}
header label{margin-left:auto;color:var(--mut);font-size:12px;cursor:pointer;
user-select:none}
header button{border:1px solid var(--line);background:#fff;color:var(--ink);
font:inherit;font-size:12px;padding:.25em .9em;border-radius:2em;cursor:pointer}
header button:hover{border-color:var(--mut)}
header a{color:var(--mut);text-decoration:none;font-size:12px}
header a:hover{color:var(--ink)}
main{position:relative;height:calc(100% - 42px);overflow:hidden}
#view{display:block;width:100%;height:100%;cursor:grab}
aside{position:absolute;top:0;right:0;bottom:0;width:340px;background:#fff;
border-left:1px solid var(--line);padding:1em 1.2em 2em;overflow-y:auto;
box-sizing:border-box;transform:translateX(103%);transition:transform .16s ease;
box-shadow:-8px 0 20px rgba(0,0,0,.05)}
aside.open{transform:none}
aside .x{position:absolute;top:.45em;right:.6em;border:0;background:none;
color:var(--mut);font-size:18px;cursor:pointer;line-height:1}
aside h2{font-size:15px;font-weight:650;margin:.4em 1.2em .5em 0}
.chip{display:inline-block;padding:.15em .7em;border-radius:1em;color:#fff;
font-size:10px;font-weight:700;letter-spacing:.06em}
.chip.ESTABLISHED{background:var(--est)}.chip.OPEN{background:var(--open)}
.chip.INVALIDATED{background:var(--dead)}.chip.route{background:#9aa096}
.chip.goal{background:var(--goal)}
aside code{font:11.5px Menlo,monospace;color:var(--mut)}
.stmt{font-size:13px;line-height:1.55;background:#fafaf8;
border:1px solid var(--line);border-radius:4px;padding:.2em .8em;
max-height:44vh;overflow-y:auto}
.stmt code{font:11px Menlo,monospace;background:#f0f0ec}
.stmt pre{font:11px/1.5 Menlo,monospace;background:#f0f0ec;padding:.5em;overflow-x:auto}
.stmt a{color:var(--goal);text-decoration:none}
h3.sec{font-size:11px;font-weight:700;letter-spacing:.12em;color:var(--mut);
text-transform:uppercase;margin:1.2em 0 .2em}
.fr{list-style:none;padding:0;margin:.3em 0}
.fr li{padding:.35em 0;border-bottom:1px solid #f1f2ef;font-size:13px;cursor:pointer}
.fr li:hover{color:var(--goal)}
.fr .imp{color:var(--mut);font:11px Menlo,monospace}
.hint{color:var(--mut);font-size:12px}
details summary{cursor:pointer;font-size:13px;font-weight:650;margin:1.2em 0 .3em;
color:var(--mut)}
svg text{font:10px Menlo,monospace;fill:#60655e;pointer-events:none}
.lk{stroke:var(--edge);stroke-width:1.6}
.lk.kill,.lk.dead{stroke:var(--dead);stroke-dasharray:5 3;stroke-width:1.4}
g.deadbit,line.dead{visibility:hidden}
.showdead g.deadbit,.showdead line.dead{visibility:visible}
g.orphan{display:none}
.dim{opacity:.12}
a.open-page{color:var(--goal);text-decoration:none;font-size:13px}
#key{position:absolute;left:14px;bottom:12px;display:flex;flex-direction:column;
gap:.28em;font-size:11px;color:var(--mut);pointer-events:none}
#key svg{vertical-align:-3px;margin-right:.45em}
</style>
<body>
<header><span class="wordmark">CAIRN</span><span class="stats">__STATS__</span>
<label><input type="checkbox" id="showdead" checked> failed routes</label>
<button id="frontierbtn">frontier</button>
<a href="nodes.html">all nodes</a></header>
<main><svg id="view"></svg>
<div id="key">
<span><svg width="22" height="16"><circle cx="9" cy="8" r="7.6" fill="none" stroke="#4f46e5" stroke-width="1.8"/><circle cx="9" cy="8" r="4.6" fill="#fff" stroke="#c08a00" stroke-width="2"/></svg>goal</span>
<span><svg width="22" height="16"><circle cx="9" cy="8" r="6" fill="#178a5e"/></svg>established</span>
<span><svg width="22" height="16"><circle cx="9" cy="8" r="6" fill="#fff" stroke="#c08a00" stroke-width="2.2"/></svg>open</span>
<span><svg width="22" height="16"><rect x="4" y="3" width="10" height="10" fill="#9aa096"/></svg>&and; multi-premise route</span>
<span><svg width="27" height="16"><line x1="1" y1="8" x2="20" y2="8" stroke="#9aa096" stroke-width="1.6"/><path d="M19,4.5L25,8L19,11.5z" fill="#9aa096"/></svg>premises &#10230; target</span>
<span><svg width="27" height="16"><line x1="1" y1="8" x2="20" y2="8" stroke="#c43c2e" stroke-width="1.4" stroke-dasharray="5,3"/><path d="M19,4.5L25,8L19,11.5z" fill="#c43c2e"/></svg>failed / invalidated</span>
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
const real=l=>l.kind!=='aff';
const svg=d3.select('#view'),W=svg.node().clientWidth,H=svg.node().clientHeight;
svg.append('defs').html('<marker id="m" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="7.5" markerHeight="7.5" orient="auto"><path d="M0,0L8,4L0,8z" fill="#7c8377"/></marker><marker id="mr" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="7.5" markerHeight="7.5" orient="auto"><path d="M0,0L8,4L0,8z" fill="#c0392b"/></marker>');
const g=svg.append('g');
svg.call(d3.zoom().scaleExtent([.25,3]).on('zoom',e=>g.attr('transform',e.transform)))
   .on('dblclick.zoom',null);
const linkForce=d3.forceLink(links).id(d=>d.id)
 .distance(l=>l.kind==='aff'?150:(l.kind==='in'?60:115))
 .strength(l=>l.kind==='aff'?.03+.1*l.w:.55);
const sim=d3.forceSimulation(nodes)
 .force('link',linkForce)
 .force('charge',d3.forceManyBody().strength(-430))
 .force('x',d3.forceX(W/2).strength(.06))
 .force('y',d3.forceY(H/2).strength(.09))
 .force('collide',d3.forceCollide(d=>d.type==='claim'?(d.goal?48:38):13));
const line=g.selectAll('line').data(links.filter(real)).join('line')
 .attr('class',l=>'lk'+(l.kind==='kill'?' kill':'')+(l.dead?' dead':''))
 .attr('marker-end',l=>l.kind==='in'?null:(l.dead||l.kind==='kill'?'url(#mr)':'url(#m)'));
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
});
node.append('title').text(d=>d.type==='claim'?`${d.id} [${d.status}]`:(d.rtitle||d.route));
function neighbors(d){
 const keep=new Set([d.id]);
 links.filter(real).forEach(l=>{if(l.source.id===d.id)keep.add(l.target.id);
   if(l.target.id===d.id)keep.add(l.source.id)});
 node.classed('dim',n=>!keep.has(n.id));
 line.classed('dim',l=>l.source.id!==d.id&&l.target.id!==d.id);
}
node.on('mouseenter',(e,d)=>neighbors(d))
 .on('mouseleave',()=>{node.classed('dim',false);line.classed('dim',false)});
function show(d){
 if(d.type==='claim'){
  pbody.innerHTML=`${d.goal?'<span class="chip goal">GOAL</span> ':''}<span class="chip ${d.status}">${d.status}</span>
   <h2>${esc(d.title)}</h2><code>${d.id}</code>
   ${d.lock?`<p class="hint">claimed (${esc(d.lock)})</p>`:''}
   <div class="stmt">${d.html||'(no statement)'}</div>
   <p><a class="open-page" href="${d.id}.html">open page &#8594;</a></p>`;
 }else{
  const imp=(d.requires&&d.requires.length?d.requires.join(' \\u2227 '):'\\u22a4')+' \\u27f9 '+d.tgt;
  pbody.innerHTML=`<span class="chip route">route${d.dead?' &middot; failed':''}</span>
   <h2>${esc(d.rtitle||d.route)}</h2><code>${esc(imp)}</code>
   ${d.killers&&d.killers.length?`<p class="hint">invalidated by ${d.killers.join(', ')}</p>`:''}
   <p><a class="open-page" href="${d.route}.html">open page &#8594;</a></p>`;
 }
 openPanel();
 if(window.MathJax&&MathJax.typesetPromise)MathJax.typesetPromise([pbody]);
}
selectById=id=>{const d=byId[id];if(d){show(d);if(!d.orphan){neighbors(d);
 setTimeout(()=>{node.classed('dim',false);line.classed('dim',false)},1600)}}};
node.on('click',(e,d)=>{e.stopPropagation();show(d)});
svg.on('click',closePanel);
function refreshVis(){
 const sd=document.getElementById('showdead').checked;
 const deg={};
 links.forEach(l=>{if(real(l)&&(!l.dead||sd)){
  const a=l.source.id||l.source,b=l.target.id||l.target;
  deg[a]=(deg[a]||0)+1;deg[b]=(deg[b]||0)+1}});
 nodes.forEach(d=>{d.orphan=d.type==='claim'&&!d.root&&!d.goal&&!d.frontier&&!(deg[d.id]>0)});
 node.classed('orphan',d=>d.orphan);
 g.classed('showdead',sd);
 sim.force('charge',d3.forceManyBody().strength(d=>d.orphan?-10:-430));
 linkForce.strength(l=>l.kind==='aff'
  ?((l.source.orphan||l.target.orphan)?0:.03+.1*l.w):.55);
 sim.alpha(.5).restart();
}
document.getElementById('showdead').onchange=refreshVis;
sim.on('tick',()=>{
 line.attr('x1',l=>l.source.x).attr('y1',l=>l.source.y)
     .attr('x2',l=>l.target.x).attr('y2',l=>l.target.y);
 node.attr('transform',d=>`translate(${d.x},${d.y})`);
});
refreshVis();
}
</script>
"""


def autolink(html_str, ids):
    """Hyperlink every mention of a known node id in already-rendered HTML."""
    pat = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
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
        out.append(pat.sub(
            lambda m: (f'<a href="{m.group(0)}.html">{m.group(0)}</a>'
                       if m.group(0) in ids else m.group(0)), part))
    return "".join(out)


def generate_site(graph, locks):
    os.makedirs(SITE_DIR, exist_ok=True)
    # index = the graph, full viewport
    idset = set(graph.nodes)
    data = {"claims": [], "links": [], "junctions": [], "dead": [], "affinity": []}
    for cid, c in graph.claims.items():
        data["claims"].append({
            "id": cid, "status": c.status, "root": bool(c.meta.get("root")),
            "goal": bool(c.meta.get("goal")),
            "title": c.title, "impact": graph.claim_impact.get(cid, 0),
            "frontier": cid in graph.frontier,
            "lock": fmt_remaining(locks[cid]) if cid in locks else None,
            "html": autolink(md_to_html(c.body), idset)})
    for rid, r in graph.routes.items():
        tgt = r.meta.get("target")
        if tgt not in graph.claims:
            continue
        reqs = [q for q in r.get_list("requires") if q in graph.claims]
        dead = r.status == "INVALIDATED"
        killers = graph.invalidated_by.get(rid, [])
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
    import math

    def _feats(text):
        words = [w for w in re.findall(r"[a-z0-9]+", text.lower())
                 if len(w) > 2 and w not in STOPWORDS]
        return set(words) | {a + "_" + b for a, b in zip(words, words[1:])}

    docs = {cid: _feats(c.title + " " + c.body) for cid, c in graph.claims.items()}
    df = {}
    for toks in docs.values():
        for t in toks:
            df[t] = df.get(t, 0) + 1
    N = max(1, len(docs))
    cutoff = 0.35 * N   # program-wide jargon carries no signal
    vecs = {cid: {t: math.log(N / df[t]) for t in toks if df[t] <= cutoff}
            for cid, toks in docs.items()}

    def _cos(u, v):
        if len(v) < len(u):
            u, v = v, u
        num = sum(w * v.get(t, 0.0) for t, w in u.items())
        du = math.sqrt(sum(w * w for w in u.values()))
        dv = math.sqrt(sum(w * w for w in v.values()))
        return num / (du * dv) if du and dv else 0.0

    cids = list(docs)
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
    idx = (INDEX_TMPL.replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
                     .replace("__STATS__", html.escape(stats)))
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(idx)
    # secondary: plain listing
    B = ["<p><a href='index.html'>&larr; graph</a></p>",
         "<h1>All nodes</h1><table><tr><th>id</th><th>kind</th><th>status</th><th>title</th></tr>"]
    for nid, n in sorted(graph.nodes.items()):
        B.append(f"<tr><td><a href='{nid}.html'>{nid}</a></td><td>{n.kind}</td>"
                 f"<td>{badge(n.status)}</td><td>{html.escape(n.title)}</td></tr>")
    B.append("</table>")
    with open(os.path.join(SITE_DIR, "nodes.html"), "w", encoding="utf-8") as f:
        f.write(page("Cairn — all nodes", "\n".join(B)))
    for nid, n in graph.nodes.items():
        goalmark = (f'<span class="badge" style="background:{GOAL_COLOR}">GOAL</span> '
                    if n.meta.get("goal") else "")
        B = ["<p><a href='index.html'>← index</a></p>",
             f"<h1><span class='node'>{nid}</span> {html.escape(n.title)}</h1>",
             f"<p>{goalmark}{badge(n.status)} <span class='muted'>{n.kind} · "
             f"<code>{html.escape(n.relpath)}</code></span></p>"]
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
        arts = n.get_list("artifacts")
        if arts:
            B.append("<h2>Artifacts</h2><ul class='rel'>")
            B.extend(f"<li><code>{html.escape(str(a))}</code></li>" for a in arts)
            B.append("</ul>")
        B.append("<h2>Statement</h2>")
        B.append(autolink(md_to_html(n.body), set(graph.nodes))
                 if n.body else "<p class='muted'>(no body)</p>")
        with open(os.path.join(SITE_DIR, f"{nid}.html"), "w", encoding="utf-8") as f:
            f.write(page(f"{nid}", "\n".join(B)))
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
    only = None
    if args.changed:
        only = changed_research_files()
        if only is None:
            print("WARNING: git unavailable; checking everything", file=sys.stderr)
    dups = duplicate_findings(graph, only_ids=only)
    dup_sev = "error" if args.changed else "warning"
    for cid, cand, score in dups:
        errors.append((dup_sev, f"possible duplicate claim: {cid} vs {cand} "
                       f"(similarity {score}); if genuinely distinct, add to {cid}:\n"
                       f"  distinct_from:\n    {cand}: <why this is not that>"))
    nerr = report_errors(errors, fail_on_warning=args.strict)
    write_outputs(graph)
    print(f"compiled {len(graph.claims)} claims + {len(graph.routes)} routes -> "
          f".cairn/cache/graph.json, research/FRONTIER.md"
          + ("" if errors else " — check clean"))
    if not nerr:
        return EXIT_OK
    non_dup_errors = nerr - (len(dups) if (args.changed or args.strict) else 0)
    return EXIT_INVALID if non_dup_errors > 0 else EXIT_DUP


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
    report_errors(errors)
    locks = all_locks()
    holes = sorted(graph.frontier, key=lambda q: -graph.claim_impact[q])
    payload = {"status": "ok", "frontier": [
        {"id": q, "title": graph.claims[q].title, "impact": graph.claim_impact[q],
         "claimed": q in locks} for q in holes]}
    human = "\n".join(claim_line(graph.claims[q], graph, locks) for q in holes)
    return emit(args, payload, human)


def cmd_context(args):
    graph, errors = compile_graph()
    report_errors(errors)
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
    if lock is None:
        return emit(args, {"status": "claimed", "id": args.id,
                           "expires_at": holder["expires_at"]},
                    f"CLAIMED {args.id} — {fmt_remaining(holder)}",
                    EXIT_LEASE)
    return emit(args, {"status": "locked", "id": args.id,
                       "expires_at": lock["expires_at"]},
                f"LOCKED {args.id} "
                f"expires={time.strftime('%H:%M:%S', time.localtime(lock['expires_at']))}")


def cmd_unlock(args):
    if read_lock(args.id) is None:
        return emit(args, {"status": "unlocked", "id": args.id}, f"no active lock on {args.id}")
    os.unlink(_lock_path(args.id))
    return emit(args, {"status": "unlocked", "id": args.id}, f"UNLOCKED {args.id}")


def cmd_site(args):
    graph, errors = compile_graph()
    report_errors(errors)
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
    report_errors(errors)
    locks = all_locks()
    est = sum(1 for c in graph.claims.values() if c.status == "ESTABLISHED")
    L = [f"{len(graph.claims)} claims ({est} established) · "
         f"{len(graph.routes)} routes ({len(graph.invalidated)} invalidated) · "
         f"{len(graph.frontier)} frontier holes · {len(locks)} active claims"]
    if graph.goals:
        L.append("goals:")
        L += [f"  {gid} [{graph.claims[gid].status}] {graph.claims[gid].title}"
              for gid in graph.goals]
    top = sorted(graph.frontier, key=lambda q: -graph.claim_impact[q])[:5]
    if top:
        L.append("frontier (top impact):")
        L += ["  " + claim_line(graph.claims[q], graph, locks) for q in top]
    payload = {"status": "ok", "claims": len(graph.claims), "established": est,
               "routes": len(graph.routes), "invalidated": len(graph.invalidated),
               "frontier": len(graph.frontier),
               "goals": [{"id": g, "node_status": graph.claims[g].status}
                         for g in graph.goals],
               "claims": sorted(locks)}
    return emit(args, payload, "\n".join(L))


def cmd_why(args):
    graph, errors = compile_graph()
    report_errors(errors)
    n = graph.nodes.get(args.id)
    if n is None:
        unknown_node(graph, args.id)
    L = []
    if n.kind == "route":
        reqs = n.get_list("requires")
        L.append(f"{args.id} [{n.status}]: "
                 f"{' AND '.join(reqs) if reqs else '(direct proof)'} "
                 f"=> {n.meta.get('target')}")
        L += ["  " + r for r in n.status_reasons]
    else:
        if n.status == "ESTABLISHED":
            L += ["derivation:"] + ["  " + x for x in derivation_lines(graph, args.id)]
        else:
            L.append(f"{args.id} is OPEN")
        chain = why_chain(graph, args.id)
        if chain:
            L.append("why it matters: "
                     + " -> ".join([chain[0][0]] + [c for _, _, c in chain]))
        waiting = [rid for rid in graph.required_by.get(args.id, [])
                   if graph.routes[rid].status != "INVALIDATED"]
        if waiting:
            L.append("live routes waiting on it: " + ", ".join(waiting))
    payload = {"status": "ok", "id": args.id, "kind": n.kind,
               "node_status": n.status, "why": L}
    return emit(args, payload, "\n".join(L))




# ---------------------------------------------------------------------------
# Telemetry: every invocation appends one JSONL record. Observability
# state (like locks) — lives in .cairn/, never committed, never able to
# affect research state. Purpose: see how agents actually use the tool
# (and which commands they never touch) to drive design changes.
# ---------------------------------------------------------------------------

def record_telemetry(cmd, argv, code, ms):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": cmd,
                 "argv": argv, "exit": code, "ms": ms}
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
               epilog="exit codes: 0 ok · 2 duplicate candidates · "
                      "3 already claimed · 4 invalid graph · 64 usage · "
                      "1 runtime error. Env: CAIRN_ROOT overrides "
                      "project-root discovery.")
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
                    help="duplicates are errors for files changed vs HEAD")
    ck.add_argument("--strict", action="store_true", help="fail on warnings")
    add("preview", "research-state delta of the working tree vs HEAD")
    add("status", "one-screen program state: goals, frontier, locks")
    add("frontier", "unresolved claims worth attacking, with locks")
    add("why", "how a node was established, or why it matters if open",
        node_id=True)
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
