#!/usr/bin/env python3
"""cairn — a build system whose build targets are unknown facts.

THE KERNEL (schema rg: 2) is two persistent objects and two relations:

  Claim   a mathematical proposition. Unresolved -> a hole/`sorry`;
          established -> a reusable theorem. NOT different object types:
          today's open question is tomorrow's lemma.
  Route   a justified implication  AND(requires) => target. Its existence
          asserts the implication is valid; the body carries the argument.
          requires: [] asserts a COMPLETE DIRECT PROOF of the target.
  invalidates   an ESTABLISHED claim can invalidate routes (obstructions).
  refuted_by    an ESTABLISHED claim proves another claim false. The refuted
                claim and every route targeting or requiring it are disabled.
  goal: true    marks a claim as a TOP-LEVEL HUMAN GOAL. Pure metadata —
                no effect on compilation — but surfaced everywhere agents
                look (graph.json, FRONTIER.md, context packets, the site).

Everything else falls out:
  Solved(Q) = OR over routes into Q of AND over their requires.
  A reduction is a route with one prerequisite. An equivalence is two
  routes. A proof is a route with no prerequisites. No further ontology.

THE TWO TIERS:
  research/*.md          claims + routes (flat; `kind:` says which) — the
                         authoritative graph. Agents edit these DIRECTLY
                         with their normal tools; this CLI never writes them.
  research/artifacts/    substantial proof artifacts routes may cite.
  notes/                 the prose corpus: derivations, audits, dead ends,
                         session logs. Searchable and citable via
                         `artifacts:`, but never compiled — prose cannot
                         change research state, only a route and its proof.

THE CLI is read-only over canonical files and deliberately small —
twelve commands: check (compile+lint+dups, refreshes FRONTIER.md; alias:
build), preview (state delta vs HEAD), status (one-screen program
state), frontier (holes grouped by the goals they serve, necessity
first; --goal for one cone, --flat for the ungrouped list), why
(derivation if established; decomposition + why-it-matters if open),
context --budget (statement, derivation, routes, reusable claims, dead
space in one bounded packet), search [--notes|--similar] — several
queries sweep in one pass (alias: relevant = search --similar), impact,
lock/unlock (advisory TTL claims — identity-free: everyone is one
team), site [--serve], telemetry. Claims are scheduler state, never committed into
mathematical history.

INSTALLING INTO A PROJECT: copy this file into the repo and commit it.
Do not use a submodule or a package install — a submodule is empty in
every copy that is not a git clone (a "Download ZIP", a tarball, an
uploaded sandbox), and a package install needs a network. The file is
stdlib-only and self-locating, so a committed copy works everywhere the
repo goes, with no setup step for whoever opens it next.

AGENT ERGONOMICS (each of these exists because transcripts showed the
lack of it costing real work): line 1 of `why` is always
`<id> [STATUS] — …` so `| head -1` learns something; query commands
collapse graph warnings to one line (spam trains agents into
`2>/dev/null`, which then eats real errors — `check` prints them all);
every warning has to earn its line, so duplicate detection needs BOTH
wording overlap and a TF-IDF gate and never matches a claim against its
own negation, a detached lane is one counted line rather than one per
claim in it, and `A <=> B` is the kernel's equivalence, never a cycle;
`frontier` marks holes on EVERY live path to a goal with ★, prints the
claim-path each hole unblocks, warns when a goal has no route-tree at
all (that means route-finding, not lemma-proving), and annotates holes
that resisted prior locked attempts; `search` takes SEVERAL queries at
once because agents orient by probing concept after concept, and it
answers with each hit's compiled status — the one thing a text search
of the same files can never report.

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
the nearest such ancestor of THIS FILE (so a copy committed into a repo
finds that repo from anywhere), else the working directory itself.

STATE: .cairn/ under the root holds what belongs to ONE COPY of the
project — the compile cache and the generated site. Leases and the usage
log belong to the PROGRAM, so they live once per project under
$XDG_STATE_HOME/cairn/<owner>-<repo> (from the git remote, the one string
every clone agrees on; the root commit when there is no remote; $CAIRN_STATE
to say so directly). Workers run in throwaway clones and worktrees: under
.cairn/ a lease is invisible to everyone it should warn, and the usage log
is discarded with the directory. Neither key exists in a zip, which has
nothing to share with, so there it stays local. Every telemetry row carries
which copy ran it, and $CAIRN_AGENT when a harness sets one.

SITE TITLE: the generated site is titled $CAIRN_SITE_TITLE if set, else
after the tool.  Set it to the name of the project the graph is about.

No third-party dependencies. Python 3.9+.
"""

import argparse
import fcntl
import html
import json
import os
import re
import resource
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

def _project_above(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, "research")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _find_root():
    # $CAIRN_ROOT wins; then the project the working directory sits in;
    # then the project THIS FILE sits in, so a copy committed into a repo
    # works from any directory with no launcher and no environment set.
    env = os.environ.get("CAIRN_ROOT")
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))
    return _project_above(os.getcwd()) or _project_above(here) or os.getcwd()


REPO = _find_root()
RESEARCH_DIR = os.path.join(REPO, "research")
NOTES_DIR = os.path.join(REPO, "notes")
STATE_DIR = os.path.join(REPO, ".cairn")
CACHE_DIR = os.path.join(STATE_DIR, "cache")   # per checkout: derived, disposable
SITE_DIR = os.path.join(STATE_DIR, "site")     # per checkout: a build artifact
_SHARED = None


def shared_dir():
    """Where state that is about the PROGRAM lives, rather than about one
    copy of it: leases and the usage log.

    Workers run in throwaway clones and worktrees. Kept under .cairn/, a
    lease is invisible to every worker it is supposed to warn, and the usage
    log is thrown away with the directory it was written in — which is how a
    project ends up with 83 commits of graph edits and no record that the
    tool was ever run. The git remote is the same string in every copy, so
    it names the program; the root commit does when there is no remote.
    Neither exists in a zip, and a zip has nothing to share with anyway.
    """
    global _SHARED
    if _SHARED:
        return _SHARED
    env = os.environ.get("CAIRN_STATE")
    if env:
        _SHARED = os.path.abspath(env)
        return _SHARED
    stamp = os.path.join(CACHE_DIR, "project-key")   # resolved once per checkout
    key = ""
    try:
        with open(stamp, encoding="utf-8") as f:
            key = f.read().strip()
    except OSError:
        r = _git("config", "--get", "remote.origin.url")
        m = re.search(r"([^/:]+)/([^/]+?)(?:\.git)?/?$",
                      r.stdout.strip()) if r.returncode == 0 else None
        if m:  # owner and name both, or two owners of a `docs` repo collide
            key = re.sub(r"[^a-z0-9]+", "-", f"{m[1]}-{m[2]}".lower()).strip("-")
        else:
            r = _git("rev-list", "--max-parents=0", "HEAD")
            if r.returncode == 0 and r.stdout.strip():
                key = r.stdout.split()[-1][:12]
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(stamp, "w", encoding="utf-8") as f:
                f.write(key)
        except OSError:
            pass
    if not key:
        _SHARED = STATE_DIR      # no git: an isolated copy, nothing to share
        return _SHARED
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    _SHARED = os.path.join(base, "cairn", key)
    return _SHARED


def lock_dir():
    return os.path.join(shared_dir(), "locks")


def telemetry_path():
    path = os.path.join(shared_dir(), "telemetry.jsonl")
    legacy = os.path.join(STATE_DIR, "telemetry.jsonl")
    if path != legacy and not os.path.exists(path) and os.path.exists(legacy):
        try:  # carry this checkout's history into the shared log, once
            os.makedirs(shared_dir(), exist_ok=True)
            shutil.copyfile(legacy, path)
        except OSError:
            pass
    return path

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
NON_NODE_FILES = {"README.md", "FRONTIER.md"}
KINDS = ("claim", "route")
CACHE_FORMAT = 2

__version__ = "2.12.0"

EXIT_OK, EXIT_DUP, EXIT_LEASE, EXIT_INVALID, EXIT_USAGE = 0, 2, 3, 4, 64

ALLOWED_KEYS = {
    "claim": {"rg", "id", "kind", "title", "root", "goal",
              "invalidates", "refuted_by", "distinct_from", "artifacts"},
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
            errors.append(("error", "frontmatter", str(e)))
            continue
        kind = meta.get("kind")
        if kind not in KINDS:
            errors.append(("error", "kind", f"{rel}: kind must be claim|route, got {kind!r}"))
            continue
        node = Node(meta, body, path, kind, relroot)
        nid = meta.get("id")
        if not isinstance(nid, str) or not ID_RE.match(nid):
            errors.append(("error", "id", f"{rel}: missing or malformed id (want a kebab-case slug), got {nid!r}"))
            continue
        if os.path.splitext(fn)[0] != nid:
            errors.append(("error", "filename", f"{rel}: filename must equal id ({nid}.md)"))
            continue
        if nid in nodes:
            errors.append(("error", "duplicate-id", f"{rel}: duplicate id {nid} (also {nodes[nid].relpath})"))
            continue
        if not meta.get("title"):
            errors.append(("error", "title", f"{rel}: missing title"))
        if meta.get("rg") != 2:
            errors.append(("error", "schema", f"{rel}: missing or unsupported schema version (want 'rg: 2')"))
        nodes[nid] = node
    return nodes


def existing_git_objects(repo, objects):
    """Return object expressions resolved by one `git cat-file` process."""
    wanted = list(dict.fromkeys(objects))
    if not wanted:
        return set()
    proc = subprocess.run(
        ["git", "-C", repo, "cat-file", "--batch-check"],
        input="\n".join(wanted) + "\n", capture_output=True, text=True)
    if proc.returncode != 0:
        return set()
    return {obj for obj, line in zip(wanted, proc.stdout.splitlines())
            if not line.endswith(" missing") and len(line.split()) >= 3}


def lint_nodes(nodes, errors, repo=REPO):
    pinned = [p for node in nodes.values() for p in node.get_list("artifacts")
              if isinstance(p, str) and ":" in p
              and not os.path.exists(os.path.join(repo, p))]
    existing_pins = existing_git_objects(repo, pinned)

    def ref(node, key, val, want_kind):
        if not isinstance(val, str) or not ID_RE.match(val):
            errors.append(("error", "ref", f"{node.relpath}: {key}: malformed id {val!r}"))
        elif val not in nodes:
            errors.append(("error", "ref", f"{node.relpath}: {key}: unknown node {val}"))
        elif val == node.id:
            errors.append(("error", "ref", f"{node.relpath}: {key}: self-reference"))
        elif nodes[val].kind != want_kind:
            errors.append(("error", "ref", f"{node.relpath}: {key}: {val} is a {nodes[val].kind}, want a {want_kind}"))

    for node in nodes.values():
        extra = set(node.meta) - ALLOWED_KEYS[node.kind]
        if extra:
            errors.append(("error", "unknown-key", f"{node.relpath}: unknown keys for {node.kind}: {sorted(extra)}"))
        for p in node.get_list("artifacts"):
            if not isinstance(p, str):
                errors.append(("error", "artifact", f"{node.relpath}: malformed artifact entry: {p!r}"))
                continue
            if not os.path.exists(os.path.join(repo, p)):
                # a <rev>:<path> entry pins a file that left the working tree
                if p not in existing_pins:
                    errors.append(("error", "artifact", f"{node.relpath}: artifact not found: {p} "
                                   "(want a working-tree path or a <rev>:<path> pin)"))
                    continue
        if node.kind == "claim":
            if node.meta.get("root") not in (None, True, False):
                errors.append(("error", "flag", f"{node.relpath}: root must be true/false"))
            if node.meta.get("goal") not in (None, True, False):
                errors.append(("error", "flag", f"{node.relpath}: goal must be true/false"))
            for r in node.get_list("invalidates"):
                ref(node, "invalidates", r, "route")
            for c in node.get_list("refuted_by"):
                ref(node, "refuted_by", c, "claim")
            df = node.meta.get("distinct_from")
            if df is not None:
                if not isinstance(df, dict):
                    errors.append(("error", "distinct-from", f"{node.relpath}: distinct_from must be a map {{claim-id: why}}"))
                else:
                    for k, why in df.items():
                        if k not in nodes or nodes[k].kind != "claim":
                            errors.append(("error", "distinct-from", f"{node.relpath}: distinct_from: unknown claim {k!r}"))
                        if not why:
                            errors.append(("error", "distinct-from", f"{node.relpath}: distinct_from: {k} needs a reason"))
        else:
            tgt = node.meta.get("target")
            if not isinstance(tgt, str) or tgt not in nodes or nodes[tgt].kind != "claim":
                errors.append(("error", "target", f"{node.relpath}: target must name an existing claim, got {tgt!r}"))
            if "requires" not in node.meta:
                errors.append(("error", "requires", f"{node.relpath}: requires is mandatory "
                               "(requires: [] asserts a complete direct proof)"))
            reqs = node.get_list("requires")
            for q in reqs:
                ref(node, "requires", q, "claim")
            if len(reqs) != len(set(reqs)):
                errors.append(("error", "requires", f"{node.relpath}: duplicate entries in requires"))
            if isinstance(tgt, str) and tgt in reqs:
                errors.append(("error", "requires", f"{node.relpath}: target appears in its own requires"))
            # restatement dressed as reduction: a single-prerequisite route
            # whose prerequisite reads like its target renames the problem
            if (len(reqs) == 1 and reqs[0] in nodes and isinstance(tgt, str)
                    and tgt in nodes):
                a, b = nodes[reqs[0]], nodes[tgt]
                t, u = _tokens(a.title), _tokens(b.title)
                # same wording gates as the duplicate detector, and the same
                # way to answer once: distinct_from either way settles it
                answered = (tgt in (a.meta.get("distinct_from") or {})
                            or reqs[0] in (b.meta.get("distinct_from") or {}))
                opposite = (_negation_signature(a.title + " " + a.id)
                            != _negation_signature(b.title + " " + b.id))
                if (t and u and not answered and not opposite and len(t & u) >= 3
                        and len(t & u) / len(t | u) >= DUP_LEXICAL):
                    errors.append(("warning", "restatement", f"{node.relpath}: prerequisite {reqs[0]} "
                                   f"reads like a restatement of target {tgt}; "
                                   "if the route only renames the problem, replace the "
                                   "prerequisite with one that can independently fail"))


# ---------------------------------------------------------------------------
# The compiler: Solved(Q) = OR_routes AND_requires, minus invalidated
# routes. Invalidation and refutation activate only from ESTABLISHED claims;
# a refuted target or premise invalidates its routes automatically. Both are
# iterated to one mutually consistent fixpoint.
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
        self.refuted_by = {}      # claim -> [established refuter claim ids]
        self.compile()

    def _solve(self, forced=frozenset()):
        """Return (established, refuted, invalidated, provenance, stable)."""
        prev_inv, prev_ref, seen = set(), set(), []
        for _ in range(64):
            est, prov = set(forced) - prev_ref, {}
            changed = True
            while changed:
                changed = False
                for rid in self.routes:
                    target = self.route_target[rid]
                    if (rid in prev_inv or target not in self.claims
                            or target in est or target in prev_ref):
                        continue
                    if all(q in est for q in self.route_requires[rid]):
                        est.add(target)
                        prov[target] = rid
                        changed = True
            refuted = {target for refuter in est
                       for target in self.refutes_from.get(refuter, [])}
            inv = {rid for claim in est
                   for rid in self.invalidates_from.get(claim, [])}
            inv.update(rid for claim in refuted
                       for rid in (self.routes_into.get(claim, [])
                                   + self.required_by.get(claim, [])))
            state = (frozenset(inv), frozenset(refuted))
            if inv == prev_inv and refuted == prev_ref:
                return est, refuted, inv, prov, True
            if state in seen:
                return est, refuted, inv, prov, False
            seen.append((frozenset(prev_inv), frozenset(prev_ref)))
            prev_inv, prev_ref = inv, refuted
        return est, prev_ref, prev_inv, prov, False

    def compile(self):
        self.route_target = {}
        self.route_requires = {}
        for rid, r in self.routes.items():
            tgt = r.meta.get("target")
            self.route_target[rid] = tgt
            self.route_requires[rid] = [q for q in r.get_list("requires")
                                        if q in self.claims]
            if isinstance(tgt, str) and tgt in self.claims:
                self.routes_into.setdefault(tgt, []).append(rid)
            for q in self.route_requires[rid]:
                self.required_by.setdefault(q, []).append(rid)
        self.invalidates_from = {
            cid: [rid for rid in claim.get_list("invalidates")
                  if rid in self.routes]
            for cid, claim in self.claims.items()}
        self.refutes_from = {}
        for target, claim in self.claims.items():
            for refuter in claim.get_list("refuted_by"):
                if refuter in self.claims:
                    self.refutes_from.setdefault(refuter, []).append(target)

        est, refuted, inv, prov, stable = self._solve()
        if not stable:
            self.errors.append(("error", "stratification", "invalidation/refutation is not stratified: "
                                "derived states oscillate; break the cycle between an obstruction "
                                "or refuter claim and a route it depends on"))
        self.established, self.refuted = est, refuted
        self.invalidated, self.provenance = inv, prov

        for cid, c in self.claims.items():
            refs = [q for q in c.get_list("refuted_by") if q in est]
            if refs:
                self.refuted_by[cid] = refs

        for cid, c in self.claims.items():
            for r in c.get_list("invalidates"):
                if r in self.routes and cid in est:
                    self.invalidated_by.setdefault(r, []).append(cid)

        for cid, c in self.claims.items():
            if cid in refuted:
                c.status = "REFUTED"
                c.status_reasons = [f"proved false by established claim {q}"
                                    for q in self.refuted_by.get(cid, [])]
            elif cid in est:
                c.status = "ESTABLISHED"
                c.status_reasons = [f"via route {prov[cid]}"] if cid in prov else []
            else:
                c.status = "OPEN"
        for rid, r in self.routes.items():
            if rid in inv:
                r.status = "INVALIDATED"
                reasons = [f"invalidated by established claim {c}"
                           for c in self.invalidated_by.get(rid, [])]
                tgt = r.meta.get("target")
                if tgt in refuted:
                    reasons.append(f"target claim {tgt} is refuted")
                reasons.extend(f"required claim {q} is refuted"
                               for q in r.get_list("requires") if q in refuted)
                r.status_reasons = reasons
            else:
                reqs = [q for q in r.get_list("requires") if q in self.claims]
                r.blocked_on = [q for q in reqs if q not in est]
                r.status = "COMPLETE" if not r.blocked_on else "OPEN"

        # Refutation must not silently win over a live proof. A route disabled
        # only because its target is refuted is contradictory evidence, while
        # a route already killed by an obstruction or a refuted premise is not.
        for cid in sorted(refuted):
            for rid in self.routes_into.get(cid, []):
                r = self.routes[rid]
                reqs = [q for q in r.get_list("requires") if q in self.claims]
                if (not self.invalidated_by.get(rid)
                        and not any(q in refuted for q in reqs)
                        and all(q in est for q in reqs)):
                    self.errors.append((
                        "error", "contradiction",
                        f"{self.claims[cid].relpath}: {cid} is proved false by "
                        f"{', '.join(self.refuted_by.get(cid, []))}, but live route "
                        f"{rid} also proves it"))

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

        # Unreachable open claims: one warning per node buried the signal in
        # a 449-claim program — 27 lines, ~19 of them one lane top and its
        # dependents restated. Two findings hide in there and only one is a
        # defect, so say each once:
        #   dead work  every route that needed this claim is invalidated —
        #              the hole stopped being load-bearing, worth a line each
        #   detached   a lane nothing consumes yet, plus everything under it.
        #              True, but it is what an unfinished program looks like:
        #              one aggregate line, detail in check's hint block.
        self.unreachable_open, self.dead_work, self.detached_tops = [], [], []
        for cid in sorted(self.claims):
            c = self.claims[cid]
            if c.status != "OPEN" or c.reachable:
                continue
            self.unreachable_open.append(cid)
            consumers = self.required_by.get(cid, [])
            live = [r for r in consumers
                    if self.routes[r].status != "INVALIDATED"]
            if consumers and not live:
                self.dead_work.append(cid)
            elif not live:
                self.detached_tops.append(cid)
        for cid in self.dead_work:
            self.errors.append(("warning", "dead-work", f"{self.claims[cid].relpath}: every "
                                f"route that needs {cid} is invalidated — the "
                                "hole is no longer load-bearing; retarget it or "
                                "let it stand as recorded dead space"))
        detached = [c for c in self.unreachable_open if c not in self.dead_work]
        if detached:
            n_below = len(detached) - len(self.detached_tops)
            self.errors.append((
                "warning", "detached",
                f"{len(detached)} open claim(s) sit on no live path to a root "
                f"claim: {len(self.detached_tops)} lane top(s)"
                + (f" and {n_below} claim(s) below them" if n_below else "")
                + " — reconnect a top and its lane comes with it "
                  "(`cairn check` lists them)"))
        self._cycle_check()

    def _cycle_check(self):
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

    def to_json(self, source_manifest=None):
        out = {"generated_by": "cairn build", "rg": 2, "nodes": {}, "derived": {}}
        for i, n in sorted(self.nodes.items()):
            out["nodes"][i] = {"kind": n.kind, "title": n.title, "path": n.relpath,
                               "status": n.status, "status_reasons": n.status_reasons,
                               "blocked_on": n.blocked_on, "reachable": n.reachable,
                               "meta": n.meta}
        out["derived"] = {"roots": self.roots, "goals": self.goals,
                          "refuted": sorted(self.refuted),
                          "frontier": self.frontier,
                          "internal_open": self.internal_open,
                          "claim_impact": self.claim_impact,
                          "provenance": self.provenance}
        out["cache"] = {"format": CACHE_FORMAT, "cairn": __version__,
                        "sources": source_manifest}
        return out


def research_manifest(research_dir=RESEARCH_DIR):
    """Cheap exact-enough source identity for cache invalidation."""
    try:
        entries = []
        with os.scandir(research_dir) as scan:
            for entry in scan:
                if (not entry.name.endswith(".md")
                        or entry.name in NON_NODE_FILES or not entry.is_file()):
                    continue
                st = entry.stat()
                entries.append([entry.name, st.st_mtime_ns, st.st_size, st.st_ino])
        return sorted(entries)
    except OSError:
        return None


def read_graph_cache(research_dir=RESEARCH_DIR, repo=REPO):
    if (os.path.abspath(research_dir) != os.path.abspath(RESEARCH_DIR)
            or os.path.abspath(repo) != os.path.abspath(REPO)):
        return None
    path = os.path.join(CACHE_DIR, "nodes.sqlite3")
    db = None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        metadata = dict(db.execute("SELECT key, value FROM cache_meta"))
        cached_manifest = json.loads(metadata.get("sources", "null"))
        if (int(metadata.get("format", 0)) != CACHE_FORMAT
                or metadata.get("cairn") != __version__
                or cached_manifest != research_manifest(research_dir)):
            return None
        nodes = {}
        for nid, kind, relpath, meta_json, body in db.execute(
                "SELECT id, kind, relpath, meta, body FROM nodes"):
            meta = json.loads(meta_json)
            if kind not in KINDS or not isinstance(meta, dict):
                return None
            nodes[nid] = Node(meta, body, os.path.join(repo, relpath), kind, repo)
        cached_errors = [tuple(row) for row in db.execute(
            "SELECT severity, rule, message FROM errors")]
    except (OSError, sqlite3.Error, ValueError):
        return None
    finally:
        if db is not None:
            db.close()
    graph = Graph(nodes, [], repo)
    graph.errors[:] = cached_errors
    graph.compile_errors = list(cached_errors)
    graph.source_manifest = cached_manifest
    graph.cache_hit = True
    return graph, graph.errors


def compile_graph(research_dir=RESEARCH_DIR, repo=REPO, use_cache=True):
    started = time.perf_counter_ns()
    if use_cache:
        cached = read_graph_cache(research_dir, repo)
        if cached is not None:
            graph, _ = cached
            TELEMETRY_EXTRA.update({
                "cache_hit": True, "nodes": len(graph.nodes),
                "claims": len(graph.claims), "routes": len(graph.routes),
                "phases_ms": {"cache": round(
                    (time.perf_counter_ns() - started) / 1_000_000, 2)}})
            return cached
    load_started = time.perf_counter_ns()
    before = research_manifest(research_dir)
    errors = []
    nodes = load_nodes(errors, research_dir, repo)
    load_ms = (time.perf_counter_ns() - load_started) / 1_000_000
    lint_started = time.perf_counter_ns()
    lint_nodes(nodes, errors, repo)
    lint_ms = (time.perf_counter_ns() - lint_started) / 1_000_000
    solve_started = time.perf_counter_ns()
    graph = Graph(nodes, errors, repo)
    solve_ms = (time.perf_counter_ns() - solve_started) / 1_000_000
    graph.compile_errors = list(errors)
    after = research_manifest(research_dir)
    graph.source_manifest = before if before == after else None
    graph.cache_hit = False
    TELEMETRY_EXTRA.update({
        "cache_hit": False, "nodes": len(graph.nodes),
        "claims": len(graph.claims), "routes": len(graph.routes),
        "phases_ms": {"load": round(load_ms, 2), "lint": round(lint_ms, 2),
                      "solve": round(solve_ms, 2), "total": round(
                          (time.perf_counter_ns() - started) / 1_000_000, 2)}})
    return graph, errors


LINT_COUNTS = {}  # rule -> times fired this run, for telemetry


def report_errors(errors, fail_on_warning=False, brief=False):
    # brief (query commands): errors in full, warnings collapsed to one
    # line. Re-printing every warning on every invocation trains agents
    # to append 2>/dev/null, which then swallows real errors too.
    n = 0
    warnings = [m for s, _, m in errors if s == "warning"]
    collapse = brief and not fail_on_warning
    for sev, rule, msg in errors:
        LINT_COUNTS[rule] = LINT_COUNTS.get(rule, 0) + 1
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

TEXT_STOPWORDS = {"the", "and", "for", "with", "from", "into", "are",
                  "its", "this", "that", "one", "two", "via", "under", "over"}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower())
            if (len(w) > 2 or w == "no") and w not in TEXT_STOPWORDS}


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


def semantic_vectors(claims, only_ids=None, titles_only=False):
    """TF-IDF vectors over titles or complete claim text.

    Validation and theorem-neighbor hints use full text. The site's invisible
    layout links use titles: bodies make a large graph's transient vector
    index much larger while mostly clustering shared proof vocabulary.
    """
    import math

    def feats(text):
        words = [w for w in re.findall(r"[a-z0-9]+", text.lower())
                 if (len(w) > 2 or w == "no") and w not in TEXT_STOPWORDS]
        return set(words) | {a + "_" + b for a, b in zip(words, words[1:])}

    selected = set(claims) if only_ids is None else set(only_ids)
    def claim_text(claim):
        return claim.title if titles_only else claim.title + " " + claim.body

    docs = {cid: feats(claim_text(claims[cid]))
            for cid in selected}
    relevant = set().union(*docs.values()) if docs else set()
    df = {}
    for cid, claim in claims.items():
        toks = docs[cid] if cid in docs else feats(claim_text(claim))
        for t in toks & relevant:
            df[t] = df.get(t, 0) + 1
    N = max(1, len(claims))
    # program-wide jargon carries no signal — but on a young graph a
    # proportional cutoff discards everything (in five claims, any word used
    # twice is "program-wide"), and two identical claims share every word by
    # definition, so the floor is what keeps the geometry alive early on.
    cutoff = max(3.0, 0.35 * N)
    # smoothed idf: log(N/df) is exactly 0 for a token every document has,
    # so an unsmoothed weighting gives two identical claims a zero vector
    # and a cosine of 0 — the one pair it most needs to score 1
    vectors = {}
    for cid in list(docs):
        toks = docs.pop(cid)
        vectors[cid] = {t: math.log(1 + N / df[t])
                        for t in toks if df[t] <= cutoff}
    return vectors


def cosine(u, v):
    import math
    if len(v) < len(u):
        u, v = v, u
    num = sum(w * v.get(t, 0.0) for t, w in u.items())
    du = math.sqrt(sum(w * w for w in u.values()))
    dv = math.sqrt(sum(w * w for w in v.values()))
    return num / (du * dv) if du and dv else 0.0


def semantic_affinity(vecs, threshold=0.16, per_node=3,
                      candidate_features=8, candidate_limit=32,
                      candidate_posting=64):
    """Bounded approximate neighbors for the site's invisible layout links.

    Rare features nominate a small candidate pool, then the final weights use
    exact cosine over the complete vectors. Common vocabulary is deliberately
    ineligible to nominate a pair: it creates quadratic work and poor layout
    signal, while still contributing to the score of a pair nominated by a
    more discriminating feature.
    """
    import math
    postings = {}
    norms = {}
    for cid, vector in vecs.items():
        norms[cid] = math.sqrt(sum(weight * weight for weight in vector.values()))
        for token in vector:
            postings.setdefault(token, []).append(cid)
    pairs = []
    for cid, vector in vecs.items():
        votes = {}
        rare = sorted((token for token in vector
                       if 1 < len(postings[token]) <= candidate_posting),
                      key=lambda token: len(postings[token]))
        for token in rare[:candidate_features]:
            peers = postings[token]
            weight = vector[token]
            for other in peers:
                if other > cid:
                    votes[other] = votes.get(other, 0.0) + weight * vecs[other][token]
        candidates = sorted(votes, key=lambda other: (votes[other], other),
                            reverse=True)[:candidate_limit]
        for other in candidates:
            left, right = vector, vecs[other]
            if len(right) < len(left):
                left, right = right, left
            dot = sum(weight * right.get(token, 0.0)
                      for token, weight in left.items())
            denom = norms[cid] * norms[other]
            score = dot / denom if denom else 0.0
            if score >= threshold:
                pairs.append((score, cid, other))
    pairs.sort(reverse=True)
    counts, affinity = {}, []
    for score, left, right in pairs:
        if counts.get(left, 0) < per_node and counts.get(right, 0) < per_node:
            affinity.append({"a": left, "b": right,
                             "w": round(min(1.0, score), 2)})
            counts[left] = counts.get(left, 0) + 1
            counts[right] = counts.get(right, 0) + 1
    return affinity


NEGATIONS = {"no", "not", "non", "never", "cannot", "neither", "nor",
             "fails", "failure", "without", "false", "counterexample",
             "refuted", "impossible", "obstruction"}


def _negation_signature(text):
    """Logical-negation markers, with multiplicity.

    A boolean is not enough: ``non-hyperlinear`` and ``no non-hyperlinear``
    both contain a negative-looking token, but the second adds a genuine
    proposition-level negation. Duplicate/restatement checks must preserve
    that difference rather than normalising it away.
    """
    norm = re.sub(r"n['’]t\b", " not", text.lower())
    counts = {}
    for tok in re.findall(r"[a-z0-9]+", norm):
        if tok in NEGATIONS:
            counts[tok] = counts.get(tok, 0) + 1
    return tuple(sorted(counts.items()))


# Both gates must fire. Calibrated against a 449-claim program: every
# false positive there scored below 0.10 on the meaning gate, while the
# two pairs authors had actually annotated as confusable scored 0.37 and
# 0.45 — a clean separation with room on both sides.
DUP_LEXICAL, DUP_MEANING = 0.6, 0.25

HINT_LIMIT = 10  # a wall of hints is read as one hint and then skipped


def duplicate_findings(graph, only_ids=None, vecs=None):
    """(claim, candidate, score) triples not answered by distinct_from."""
    toks = {cid: _tokens(c.title) for cid, c in graph.claims.items()}
    postings = {}
    for cid, words in toks.items():
        for word in words:
            postings.setdefault(word, []).append(cid)
    candidates = []
    for cid, c in graph.claims.items():
        if only_ids is not None and cid not in only_ids:
            continue
        df = c.meta.get("distinct_from") or {}
        overlap = {}
        for word in toks[cid]:
            for oid in postings[word]:
                overlap[oid] = overlap.get(oid, 0) + 1
        for oid in sorted(overlap):
            if oid == cid or overlap[oid] < 2:
                continue
            cand = graph.claims[oid]
            if oid in df or cid in (cand.meta.get("distinct_from") or {}):
                continue
            if oid < cid and (only_ids is None or oid in only_ids):
                continue  # report each unordered pair once
            if len(toks[cid] & toks[oid]) / len(toks[cid] | toks[oid]) < DUP_LEXICAL:
                continue
            if (_negation_signature(c.title + " " + c.id)
                    != _negation_signature(cand.title + " " + cand.id)):
                continue
            candidates.append((cid, oid))
    if not candidates:
        return []
    if vecs is None:
        involved = {cid for pair in candidates for cid in pair}
        vecs = semantic_vectors(graph.claims, only_ids=involved)
    out = []
    for cid, oid in candidates:
        score = cosine(vecs.get(cid, {}), vecs.get(oid, {}))
        score = round(score, 2) if score >= DUP_MEANING else None
        if score is not None:
            out.append((cid, oid, score))
    return out


# ---------------------------------------------------------------------------
# TTL work locks. Scheduler state — never committed, never in the DSL.
# Filesystem backend here; the interface is the contract, the backend can
# become SQLite/service for distributed agents without touching semantics.
# ---------------------------------------------------------------------------

def _lock_path(nid):
    if not isinstance(nid, str) or ID_RE.fullmatch(nid) is None:
        raise SystemExit(f"malformed node id {nid!r}")
    return os.path.join(lock_dir(), f"{nid}.json")


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
    if not os.path.isdir(lock_dir()):
        return {}
    out = {}
    for fn in sorted(os.listdir(lock_dir())):
        nid = fn[:-5] if fn.endswith(".json") else ""
        if ID_RE.fullmatch(nid) is not None:
            lock = read_lock(nid)
            if lock:
                out[nid] = lock
    return out


def acquire_lock(nid, ttl_seconds):
    # Claims are identity-free and advisory: one team; TTL handles crashes.
    os.makedirs(lock_dir(), exist_ok=True)
    now = time.time()
    payload = {"node": nid, "acquired_at": now,
               "ttl_seconds": ttl_seconds, "expires_at": now + ttl_seconds}
    with open(os.path.join(lock_dir(), ".mutex"), "w") as mtx:
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
        "REFUTED": "REFUTED", "INVALIDATED": "✗"}


def render_tree(graph, cid, locks, depth=0, seen=None, lines=None, max_depth=6):
    if lines is None:
        lines, seen = [], set()
    c = graph.claims[cid]
    ind = "  " * depth
    lockmark = " 🔒" if cid in locks else ""
    lines.append(f"{ind}{cid} [{MARK.get(c.status, c.status)}]{lockmark} {c.title}")
    if c.status == "REFUTED":
        lines.extend(f"{ind}  {reason}" for reason in c.status_reasons)
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
        out.append(f"{ind}{cid} {c.status}")
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


def actionable_frontier(graph):
    """Root frontier plus undecomposed holes in every open goal cone."""
    holes = set(graph.frontier)
    undecomposed = set(undecomposed_open(graph))
    for gid in graph.goals:
        if graph.claims[gid].status == "OPEN":
            holes.update(goal_cone(graph, gid) & undecomposed)
    return sorted(holes, key=lambda h: (-graph.claim_impact[h], h))


def monotone_frontier_necessity(graph, gid, holes):
    """Return (connected, necessary, stable) for one obstruction-free cone.

    The old implementation solved the entire graph once per omitted hole.
    Necessity in a monotone AND/OR graph is a dataflow property: requirements
    combine by union and alternative routes combine by intersection. Integer
    bitsets compute every hole together after one connectivity solve.
    """
    if not holes:
        return None, set(), True
    positions = {h: i for i, h in enumerate(holes)}
    universe = (1 << len(holes)) - 1
    established, _, _, _, stable = graph._solve(forced=frozenset(holes))
    if not stable:
        return None, set(), False
    cone = goal_cone(graph, gid)
    connected = established & cone
    if gid not in connected:
        return False, set(), True

    must = {cid: 0 for cid in graph.established if cid in connected}
    must.update({h: 1 << positions[h] for h in holes})
    for cid in connected:
        must.setdefault(cid, universe)

    for _ in range(len(connected) + 1):
        changed = False
        for cid in connected:
            if cid in graph.established or cid in positions:
                continue
            candidates = []
            for rid in graph.routes_into.get(cid, ()):
                route = graph.routes[rid]
                if route.status == "INVALIDATED":
                    continue
                reqs = [q for q in route.get_list("requires")
                        if q in graph.claims]
                if all(q in connected for q in reqs):
                    bits = 0
                    for q in reqs:
                        bits |= must[q]
                    candidates.append(bits)
            if candidates:
                bits = candidates[0]
                for alternative in candidates[1:]:
                    bits &= alternative
                if bits != must[cid]:
                    must[cid] = bits
                    changed = True
        if not changed:
            break

    goal_bits = must[gid]
    necessary = {h for h, i in positions.items() if goal_bits & (1 << i)}
    return True, necessary, True


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
                graph.claims[q].get_list("invalidates")
                or graph.claims[q].get_list("refuted_by") for q in cone)
            if cone_holes and with_necessity and not g["obstruction_sensitive"]:
                connected, necessary, stable = monotone_frontier_necessity(
                    graph, gid, cone_holes)
                if not stable:
                    g["counterfactual_unstable"] = True
                else:
                    g["connected"] = connected
                    g["necessary"] = necessary
                g["holes"] = sorted(
                    cone_holes,
                    key=lambda h: (h not in g["necessary"],
                                   -graph.claim_impact[h], h))
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
        elif n.status == "REFUTED":
            sec("PROVED FALSE BY", n.status_reasons)
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
    ref = sum(1 for c in graph.claims.values() if c.status == "REFUTED")
    display_frontier = actionable_frontier(graph)
    L.append(f"{len(graph.claims)} claims ({est} established, {ref} refuted) · "
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
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Static site (human display is downstream of the kernel)
# ---------------------------------------------------------------------------

# The name the generated site carries: its <title>, and the prefix of the
# secondary pages.  Consumers set $CAIRN_SITE_TITLE to their own project
# name; unset, the site is titled after the tool.
SITE_TITLE = os.environ.get("CAIRN_SITE_TITLE") or "Cairn"

STATUS_COLOR = {"OPEN": "#c08a00", "ESTABLISHED": "#178a5e",
                "COMPLETE": "#178a5e", "REFUTED": "#8f2738",
                "INVALIDATED": "#c43c2e"}
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
a.srclink{display:inline-flex;align-items:center;gap:.45em;border:0;
color:var(--mut);text-decoration:none;font-size:.82rem}
a.srclink:hover{color:var(--accent)}
a.srclink svg{flex:none}
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
var CORPUS=null,loading=null,rows=[],cur=0;
function corpus(){
 if(CORPUS)return CORPUS;
 var base=[];
 for(var i=0;i<DATA.claims.length;i++){var c=DATA.claims[i];
  base.push({id:c.id,kind:'claim',status:c.status,goal:c.goal,
   title:c.title,text:''})}
 var R=DATA.routes||{};
 for(var k in R)base.push({id:k,kind:'route',status:R[k].status||'',
   title:R[k].title||k,text:''});
 return base;
}
function loadCorpus(){
 if(CORPUS||loading)return loading;
 loading=fetch('data/search.json').then(function(r){
  if(!r.ok)throw new Error('search index unavailable');return r.json()})
  .then(function(x){CORPUS=x;render();return x})
  .catch(function(){return corpus()});
 return loading;
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
 q.value='';render();loadCorpus();setTimeout(function(){q.focus()},20)}
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
MATH_STOPWORDS = set(
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
                elif w.lower() in MATH_STOPWORDS:
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
        if re.search(r"[│├└┌┬┴┼─┐╭╰•]|\[(?:OPEN|REFUTED|✓|✗)\]|^\s*[-*+]\s|\]\(|^#|"
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
    to the source host: a reader following a reference should land where the
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


def md_to_html(md, ids=(), tex_cache=None):
    out, in_code, in_list, para = [], False, False, []
    fence = [False]  # is the open fence a math fence?
    fence_buf = []

    def inline(s):
        # Verbatim spans come out before escaping: a formula must keep its
        # own `<`, `&` and braces to be translatable at all.
        held = []

        def hold(m):
            raw = m.group(1)
            if tex_cache is not None and raw in tex_cache:
                el = tex_cache[raw]
            else:
                el = tex_span(raw, ids)
                if tex_cache is not None:
                    tex_cache[raw] = el
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


INDEX_TMPL = r"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
__KATEX__
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>
:root{__PALETTE__;color-scheme:light}
html,body{height:100%;margin:0}
body{background:var(--paper);color:var(--ink);font:14px/1.55 __SANS__;
display:flex;flex-direction:column;
font-synthesis:none;text-rendering:geometricPrecision;
-webkit-font-smoothing:antialiased}
header{display:flex;align-items:center;gap:1.4em;padding:.7em 1.3em;
border-bottom:1px solid var(--line);background:var(--paper);flex:none;flex-wrap:wrap}
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
header .viewctl{display:flex;align-items:center;gap:.65em;color:var(--mut2);
font-size:11px;white-space:nowrap}
header select,header input[type=range]{accent-color:var(--accent)}
header select{border:1px solid var(--line);background:var(--paper);color:var(--ink);
font:11px __SANS__;padding:.3em .45em}
header input[type=range]{width:5.5em;vertical-align:middle}
#scopeCount{font:10px __MONO__;color:var(--mut2);min-width:7em}
main{position:relative;flex:1;min-height:0;overflow:hidden}
#view{display:block;width:100%;height:100%;cursor:default;background:var(--paper)}
#view:active{cursor:grabbing}
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
.chip.REFUTED{background:#8f2738}
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
.fr.ctx{margin:.45em 0 0}
.fr.ctx li{cursor:default;display:block;padding:.55em 0 .6em .85em;
border-left:2px solid var(--line);border-bottom:0;margin-bottom:.4em;
line-height:1.45}
.fr.ctx li:hover{color:inherit;border-left-color:var(--rule)}
.fr.ctx .mk{margin-right:.5em;vertical-align:.05em}
.fr.ctx a{color:var(--ink);border-bottom:1px solid var(--rule);text-decoration:none;
cursor:pointer}
.fr.ctx a:hover{color:var(--accent);border-bottom-color:var(--accent)}
.fr.ctx .sub{display:block;color:var(--mut2);font-size:11.5px;margin-top:.28em;
line-height:1.4;padding-left:.1em}
.fr.ctx .sub:before{content:"\21B3\A0";color:var(--rule)}
h3.sec .ct{margin-left:.6em;color:var(--mut2);font-weight:400;
letter-spacing:0;font-family:__MONO__;font-size:10px}
.mk{flex:none;font-size:9.5px;font-weight:700;letter-spacing:.08em;
text-transform:uppercase;padding:.15em .5em;border:1px solid var(--line)}
.mk.ok{color:var(--est);border-color:#178a5e55}
.mk.open{color:var(--open);border-color:#c08a0055}
.mk.dead{color:var(--dead);border-color:#c43c2e55}
.hint{color:var(--mut);font-size:12px}
.flow{font:11px __MONO__;color:var(--mut);letter-spacing:.02em;margin:.5em 0 0}
.flow b{color:var(--ink);font-weight:700}
details summary{cursor:pointer;font-size:10px;font-weight:700;letter-spacing:.16em;
text-transform:uppercase;margin:1.9em 0 .4em;color:var(--mut2)}
svg text{font:10px __MONO__;fill:var(--mut);pointer-events:none;
paint-order:stroke;stroke:var(--paper);stroke-width:3.5px;stroke-linejoin:round}
svg text.goalcap{fill:var(--goal);stroke-width:4px}
.lk{stroke:var(--edge);stroke-width:1.5}
/* Direction is drawn, not inferred: a premise arrives thin with a hollow
   head, the one edge OUT of a route leaves heavy with a solid one. */
.lk.in:not(.dead){stroke-width:1.1}
.lk.out:not(.dead){stroke-width:2.1;stroke:#171714a8}
.lk.kill,.lk.dead{stroke:var(--dead);stroke-dasharray:5 3;stroke-width:1.2;
opacity:.75}
g.deadbit,line.dead{visibility:hidden}
.showdead g.deadbit,.showdead line.dead{visibility:visible}
g.orphan{display:none}
line.gone{display:none}
.dim{opacity:.13}
/* `.lk.dead` is two classes and `.dim` is one, so without these the red
   obstruction edges kept their own opacity while everything else dimmed --
   which read as every red edge lighting up whenever anything was focused.
   Out of focus they fade harder than live edges: an obstruction elsewhere in
   the graph is noise, not context. */
.lk.dim,.lk.kill.dim,.lk.dead.dim{opacity:.06}
g.n,line.lk{transition:opacity .1s ease}
text.orphan{display:none}
g.labels text{font:10px __MONO__;fill:var(--mut);paint-order:stroke;
stroke:var(--paper);stroke-width:3.5px;stroke-linejoin:round}
g.labels text.hot{fill:var(--ink);font-weight:700}
g.n.hot circle{stroke-width:3}
line.lk.hot{stroke:var(--ink);stroke-width:1.9;opacity:1}
line.lk.kill.hot,line.lk.dead.hot{stroke:var(--dead);stroke-width:1.7;opacity:1}
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
<header><span class="stats">__STATS__</span>
<button id="openSearch">search the graph<kbd>/</kbd></button>
<span class="viewctl">show
<select id="viewmode" aria-label="Graph view">
<option value="focus">one claim</option>
<option value="goal">main goal</option>
<option value="all">everything</option>
</select>
<label id="depthbox">levels <input id="focusdepth" type="range" min="1" max="5" value="3"><output id="depthout">3</output></label>
<label id="routebox">ways per claim <input id="routecap" type="range" min="1" max="10" value="4"><output id="routeout">4</output></label>
<label>order <select id="routesort" aria-label="Order ways to prove each claim">
<option value="recent">recent</option>
<option value="missing">fewest missing</option>
<option value="complete">completed first</option>
</select></label>
<span id="scopeCount"></span></span>
<label><input type="checkbox" id="showdead" checked> failed routes</label>
<label id="foldbox"><input type="checkbox" id="fold"> fold proven</label>
<button class="lnk" id="frontierbtn">frontier</button>
<a href="nodes.html">all nodes</a></header>
<main><svg id="view"></svg>
<div id="key">
<span><svg width="22" height="16"><circle cx="9" cy="8" r="7.6" fill="none" stroke="#4f46e5" stroke-width="1.8"/><circle cx="9" cy="8" r="4.6" fill="#fff" stroke="#c08a00" stroke-width="2"/></svg>goal</span>
<span><svg width="22" height="16"><circle cx="9" cy="8" r="6" fill="#178a5e"/></svg>established</span>
<span><svg width="22" height="16"><circle cx="9" cy="8" r="6" fill="#fff" stroke="#c08a00" stroke-width="2.2"/></svg>open</span>
<span><svg width="22" height="16"><circle cx="9" cy="8" r="6" fill="#8f2738" stroke="#6f1728" stroke-width="2.2"/></svg>refuted / proved false</span>
<span><svg width="24" height="16"><path d="M2.5,2.5 L8,2.5 L13.5,8 L8,13.5 L2.5,13.5 Z" fill="#fff" stroke="#171714" stroke-width="1.3" stroke-linejoin="round"/><path d="M4.4,10.4 L6.6,6.2 L8.8,10.4" fill="none" stroke="#171714" stroke-width="1.5" stroke-linejoin="miter"/></svg>&and; gate &mdash; nose points at what it proves</span>
<span><svg width="30" height="16"><line x1="1" y1="8" x2="21" y2="8" stroke="#17171459" stroke-width="1.1"/><path d="M20.6,4.9L26,8L20.6,11.1z" fill="#fff" stroke="#171714a8" stroke-width="1.1"/></svg>premise &#8594; gate (input)</span>
<span><svg width="30" height="16"><line x1="1" y1="8" x2="20" y2="8" stroke="#171714a8" stroke-width="2.1"/><path d="M19,3.9L27,8L19,12.1z" fill="#171714a8"/></svg>gate &#8594; claim (output)</span>
<span><svg width="30" height="16"><rect x="2" y="3.5" width="25" height="9" rx="3" fill="#178a5e1f" stroke="#178a5e" stroke-width="1.5"/></svg>folded proven region</span>
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
let panelLoad=0;
const openPanel=()=>panel.classList.add('open');
const closePanel=()=>{panelLoad++;panel.classList.remove('open')};
document.getElementById('closepanel').onclick=closePanel;
const DETAIL={},DETAIL_LOAD={};
function detailFor(id){
 if(DETAIL[id])return Promise.resolve(DETAIL[id]);
 const key=/^[a-z0-9]/.test(id)?id[0]:'_';
 if(!DETAIL_LOAD[key])DETAIL_LOAD[key]=fetch('data/details-'+key+'.json')
  .then(r=>{if(!r.ok)throw new Error('detail unavailable');return r.json()})
  .then(rows=>{Object.assign(DETAIL,rows);return rows});
 return DETAIL_LOAD[key].then(()=>DETAIL[id]||{}).catch(()=>({}));
}
function frontierHome(){
 panelLoad++;
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
 for(const q of j.requires)links.push({source:q,target:jn.id,kind:'in',
  route:j.route,title:j.title,dead:j.dead});
 links.push({source:jn.id,target:j.target,kind:'arrow',out:true,
  route:j.route,title:j.title,dead:j.dead});
}
for(const d of DATA.dead){
 const st={id:'x:'+d.route,type:'stub',route:d.route,rtitle:d.title,
  tgt:d.target,killers:d.killers,dead:true};
 nodes.push(st);byId[st.id]=st;
 links.push({source:st.id,target:d.target,kind:'arrow',
  route:d.route,title:d.title,dead:true});
 for(const k of d.killers)if(byId[k])links.push({source:k,target:st.id,
  kind:'kill',route:d.route,title:d.title,dead:true});
}
for(const a of DATA.affinity)links.push({source:a.a,target:a.b,kind:'aff',w:a.w});
// ---- proven regions -------------------------------------------------------
// Most of a working graph is settled interior, and none of it is a decision:
// it is the part you have already finished, drawn at the same weight as the
// part you have not.  A region FOLDS when it is a connected block of
// established claims that nothing live still reads from.  Four kinds of
// claim stay explicit however proved they are, because they are what a
// reader navigates by: goals, roots, the obstructions that killed a route,
// and the established claims a live route into an OPEN target requires --
// the supply layer of the frontier.  Folding is a view.  It touches no
// status, no route and no file; one click puts a region back.
const MINFOLD=5;
const killerIds=new Set();
for(const d of DATA.dead)for(const k of d.killers||[])killerIds.add(k);
const supplies=new Set();
const allRoutes=DATA.junctions.concat(
 DATA.links.map(l=>({route:l.route,target:l.target,dead:l.dead,requires:[l.source]})));
for(const r of allRoutes){
 if(r.dead)continue;
 const t=byId[r.target];
 if(t&&t.status==='ESTABLISHED')continue;
 for(const q of r.requires)supplies.add(q);
}
const explicit=c=>c.goal||c.root||killerIds.has(c.id)||supplies.has(c.id);
// union-find over every established claim, along the routes that are proved
// end to end: this is the shape of the settled part
const uf={};
for(const c of DATA.claims)if(c.status==='ESTABLISHED')uf[c.id]=c.id;
const find=x=>{while(uf[x]!==x)x=uf[x]=uf[uf[x]];return x};
for(const r of allRoutes){
 if(r.dead||uf[r.target]===undefined)continue;
 if(!r.requires.every(q=>uf[q]!==undefined))continue;
 for(const q of r.requires){const a=find(q),b=find(r.target);if(a!==b)uf[a]=b}
}
const bucket={},groups=[];
for(const id in uf){const k=find(id);(bucket[k]=bucket[k]||[]).push(id)}
for(const k in bucket){
 const mem=bucket[k].filter(id=>!explicit(byId[id]));
 if(mem.length<MINFOLD)continue;
 const gid='region:'+(groups.length+1);
 let ds=0,dn=0;
 for(const m of mem){const dp=byId[m].depth;if(dp!=null){ds+=dp;dn++}}
 const gn={id:gid,type:'group',n:mem.length,open:false,
  gw:44+Math.min(18,Math.round(mem.length/7)),gh:15,
  members:mem.slice().sort((a,b)=>byId[a].title.localeCompare(byId[b].title)),
  depth:dn?ds/dn:null};
 for(const m of mem)byId[m].region=gid;
 groups.push(gn);nodes.push(gn);byId[gid]=gn;
}
// a gate is inside a region only when the whole route it carries is
for(const n of nodes)if(n.type==='junction'&&!n.dead){
 const t=byId[n.tgt];
 if(t&&t.region&&n.requires.every(q=>byId[q]&&byId[q].region===t.region))
  n.region=t.region;
}
// Every relation that CROSSES a fold is kept and re-pointed at the block, so
// a folded region still shows what it feeds and what feeds it.  A proxy that
// merges several relations carries no route id: it would have to pick one.
const rgOf=id=>{const n=byId[id];return n&&n.region?n.region:null};
const px={};
for(const l of links){
 if(l.kind==='aff')continue;
 const ra=rgOf(l.source),rb=rgOf(l.target);
 if((!ra&&!rb)||(ra&&ra===rb))continue;
 const s=ra||l.source,t=rb||l.target;
 const key=s+'|'+t+'|'+l.kind+(l.dead?'|x':'');
 if(px[key]){px[key].n++;continue}
 px[key]={source:s,target:t,kind:l.kind,dead:l.dead,route:l.route,
  title:l.title,out:l.out,proxy:true,n:1};
}
for(const key in px){const e=px[key];
 if(e.n>1){e.route=null;e.title=e.n+' relations'}
 links.push(e)}
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
const GAP=20;   // breathing room between footprints
const real=l=>l.kind!=='aff';
const REAL=links.filter(real);
const svg=d3.select('#view'),W=svg.node().clientWidth,H=svg.node().clientHeight;
// The graph is a derivation, so the layout should read as one: an integer
// layer per node (half-steps for the junctions and stubs that sit between a
// claim and its premises), with real vertical separation between layers.
for(const n of nodes)n.layer=Math.round((n.depth==null?maxD+1:n.depth)*2);

const LGAP=105;
// Vertical band per layer: this is the hierarchy, and it is a force pull, not
// a pin -- the simulation is free to bend it where the structure demands.
const bandY=d=>80+d.layer*LGAP;
nodes.forEach(n=>{n.y=bandY(n);n.x=W/2+(Math.random()-.5)*W*.5});
// Three heads, and the difference between them IS the direction: an input
// is hollow and small, an output is solid and full size, a failure is red.
// Before this, inputs had no head at all and the reader was asked to read
// direction off an absence.
svg.append('defs').html('<marker id="m" viewBox="0 0 8 8" refX="7.6" refY="4" markerWidth="8.5" markerHeight="8.5" markerUnits="userSpaceOnUse" orient="auto"><path d="M0,0L8,4L0,8z" fill="#171714a8"/></marker><marker id="mi" viewBox="0 0 8 8" refX="7.4" refY="4" markerWidth="7" markerHeight="7" markerUnits="userSpaceOnUse" orient="auto"><path d="M0.7,0.9L7.4,4L0.7,7.1z" fill="#fff" stroke="#171714a8" stroke-width="1.2" stroke-linejoin="round"/></marker><marker id="mr" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="8.5" markerHeight="8.5" markerUnits="userSpaceOnUse" orient="auto"><path d="M0,0L8,4L0,8z" fill="#c43c2e"/></marker>');
const g=svg.append('g');
const zoom=d3.zoom().scaleExtent([.2,3.5])
 .on('zoom',e=>{g.attr('transform',e.transform)});
svg.call(zoom).on('dblclick.zoom',null);
// These constants are the layout: strong repulsion and a weak pull to the
// centre line are what open the derivation bands out into a readable shape.
// Do not tighten them to make the graph narrower -- that collapses it into a
// ball with no structure.  Width is handled after settling, by fitting the
// view to the graph rather than by squeezing the graph into the view.
// The layout resolves text collisions by MOVING NODES.  Every node carries
// its real rectangles -- the disc, and the title at wherever the placement
// pass currently puts it -- and the force separates disc/disc, disc/title and
// title/title alike, pushing the nodes themselves apart along the axis of
// least penetration.  A circular collide cannot see a title at all, which is
// why text kept colliding however cleverly it was placed.
function setRects(d){
 if(d.type==='group'){
  d.rects=[[-d.gw-5,-d.gh-5,d.gw+5,d.gh+5]];
  d.mx=d.gw+5+GAP;d.my=d.gh+5+GAP;return;
 }
 const rad=(d.type==='claim'?(d.goal?23:12):(d.type==='junction'?11:8))+3;
 // a goal also carries its GOAL caption above the ring, which a title
 // flipped overhead would otherwise land on top of
 const r=[[-rad,-rad,rad,rad]];
 const L=d.lbl;
 if(L){
  const hw=L.w/2+3,t=L.top+L.dy-3;
  r.push([L.dx-hw,t,L.dx+hw,t+L.h+6]);
 }
 d.rects=r;
 let mx=0,my=0;
 for(const b of r){mx=Math.max(mx,-b[0],b[2]);my=Math.max(my,-b[1],b[3])}
 d.mx=mx+GAP;d.my=my+GAP;
}
function rectCollide(){
 let ns=[],MX=0,MY=0;
 function force(alpha){
  const q=d3.quadtree(ns,d=>d.x,d=>d.y),k=alpha*0.9;
  for(const d of ns){
   q.visit((quad,x0,y0,x1,y1)=>{
    if(!quad.length){
     let n=quad;
     do{
      const o=n.data;
      // a hidden node has no footprint: folded interiors must not go on
      // shoving the graph around from behind display:none
      if(o&&o!==d&&o.index>d.index&&!d.gone&&!o.gone){
       for(const A of d.rects)for(const B of o.rects){
        const ax1=d.x+A[0]-GAP/2,ay1=d.y+A[1]-GAP/2;
        const ax2=d.x+A[2]+GAP/2,ay2=d.y+A[3]+GAP/2;
        const bx1=o.x+B[0]-GAP/2,by1=o.y+B[1]-GAP/2;
        const bx2=o.x+B[2]+GAP/2,by2=o.y+B[3]+GAP/2;
        const ox=Math.min(ax2,bx2)-Math.max(ax1,bx1);
        if(ox<=0)continue;
        const oy=Math.min(ay2,by2)-Math.max(ay1,by1);
        if(oy<=0)continue;
        if(ox<oy){
         const s=ox*k*.5*(((bx1+bx2)-(ax1+ax2))<0?-1:1);
         d.x-=s;o.x+=s;
        }else{
         const s=oy*k*.5*(((by1+by2)-(ay1+ay2))<0?-1:1);
         d.y-=s;o.y+=s;
        }
       }
      }
      n=n.next;
     }while(n);
    }
    return x0>d.x+d.mx+MX||x1<d.x-d.mx-MX||y0>d.y+d.my+MY||y1<d.y-d.my-MY;
   });
  }
 }
 force.initialize=n=>{ns=n;MX=MY=0;
  for(const d of ns){if(!d.rects)setRects(d);
   MX=Math.max(MX,d.mx);MY=Math.max(MY,d.my)}};
 return force;
}
// links stretch to make room for the titles hanging off their endpoints
const linkDist=l=>{
 if(l.kind==='aff')return 190;
 const room=((l.source.lbl?l.source.lbl.h:0)+(l.target.lbl?l.target.lbl.h:0))*0.55;
 return (l.kind==='in'?86:150)+room;
};
const linkForce=d3.forceLink(links).id(d=>d.id)
 .distance(linkDist)
 .strength(l=>l.kind==='aff'?.03+.1*l.w:.55);
const sim=d3.forceSimulation(nodes)
 .force('link',linkForce)
 .force('charge',d3.forceManyBody().strength(-430))
 .force('x',d3.forceX(W/2).strength(.05))
 .force('y',d3.forceY(bandY).strength(.5))
 .force('collide',rectCollide())
 .alphaDecay(.03);
const line=g.selectAll('line').data(REAL).join('line')
 .attr('class',l=>'lk'+(l.kind==='kill'?' kill':'')+(l.dead?' dead':'')
  +(l.kind==='in'?' in':l.out?' out':''))
 .attr('marker-end',l=>(l.dead||l.kind==='kill')?'url(#mr)'
  :l.kind==='in'?'url(#mi)':'url(#m)')
 .style('cursor',l=>l.route?'pointer':null)
 .on('click',(e,l)=>{if(l.route){e.stopPropagation();showRoute(l.route)}});
line.filter(l=>l.route).append('title').text(l=>l.title||l.route);
const node=g.selectAll('g.n').data(nodes).join('g')
 .attr('class',d=>'n'+(d.dead?' deadbit':''))
 .style('cursor','pointer')
 .call(d3.drag()
   .on('start',(e,d)=>{dragging=true;
     if(!e.active)sim.alphaTarget(.12).restart();d.fx=d.x;d.fy=d.y})
   .on('drag',(e,d)=>{d.fx=e.x;d.fy=e.y})
   .on('end',(e,d)=>{dragging=false;
     if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;scheduleRelabel()}));
node.filter(d=>d.type==='claim'&&d.goal).append('circle')
 .attr('r',23).attr('fill','none').attr('stroke','var(--goal)').attr('stroke-width',2.2);
node.filter(d=>d.type==='claim').append('circle')
 .attr('r',d=>d.goal?15:10+Math.min(d.impact*1.5,4))
 .attr('fill',d=>d.status==='ESTABLISHED'?'var(--est)':d.status==='REFUTED'?'#8f2738':'#fff')
 .attr('stroke',d=>d.status==='ESTABLISHED'?'#0f6b47':d.status==='REFUTED'?'#6f1728':'var(--open)')
 .attr('stroke-width',2.2);
// no GOAL caption: it is a second label on the same node and always fought
// the title for the space above the ring.  The double ring and the legend
// carry the meaning.
// A square carrying the conjunction sign: edges meet a box cleanly, the
// filled face is a real hit target, and the sign is geometry rather than a
// 9px text glyph -- as text it inherited the label halo, a paper-coloured
// stroke around a tiny character, i.e. a blob at any zoom.
const jn=node.filter(d=>d.type==='junction');
// The gate is a DIRECTIONAL glyph.  A square says nothing about which way a
// route fires, so the shell is a D: premises land on the flat back, and the
// nose is aimed, every tick, at the claim the route would establish.  You
// can read the direction off the shape before you look for an arrowhead.
const gate=jn.append('g').attr('class','gate');
gate.append('path')
 .attr('d','M-8.5,-8.5 L0.5,-8.5 L10,0 L0.5,8.5 L-8.5,8.5 Z')
 .attr('fill','var(--paper)')
 .attr('stroke',d=>d.dead?'var(--dead)':'var(--ink)')
 .attr('stroke-width',1.6).attr('stroke-linejoin','round');
// the conjunction sign stays UPRIGHT in the node group -- rotated with the
// shell it stops reading as a sign and becomes a stray tick
jn.append('path')
 .attr('d','M-4.4,3.2 L0,-4 L4.4,3.2')
 .attr('fill','none')
 .attr('stroke',d=>d.dead?'var(--dead)':'var(--ink)')
 .attr('stroke-width',2)
 .attr('stroke-linejoin','miter').attr('stroke-linecap','butt')
 .attr('pointer-events','none')
 .attr('shape-rendering','geometricPrecision');
node.filter(d=>d.type==='stub').append('circle')
 .attr('r',6.5).attr('fill','#fff').attr('stroke','var(--dead)')
 .attr('stroke-width',1.7).attr('stroke-dasharray','3 2');
// A folded region is one block carrying one number, because the whole point
// of the block is that there is nothing left to decide inside it.
const grp=node.filter(d=>d.type==='group');
grp.append('rect')
 .attr('x',d=>-d.gw).attr('y',d=>-d.gh)
 .attr('width',d=>d.gw*2).attr('height',d=>d.gh*2).attr('rx',7)
 .attr('fill','#178a5e1f').attr('stroke','var(--est)').attr('stroke-width',1.8);
grp.append('text')
 .attr('text-anchor','middle').attr('y',4.5)
 .attr('font-size',12).attr('fill','#0f6b47').attr('font-weight',700)
 .attr('pointer-events','none')
 .text(d=>'\u2713 '+d.n+' proven');
// Where an edge STOPS is half of what makes it directional: every line is
// cut back to the outline of the shape it points at, so the head lands on
// paper instead of under a disc that is drawn over it.
const shapeR=d=>d.type==='claim'?(d.goal?24:11+Math.min(d.impact*1.5,4))
 :d.type==='junction'?10.5:7.5;
function edgeOff(d,dx,dy){
 if(d.type!=='group')return shapeR(d);
 const ax=Math.abs(dx),ay=Math.abs(dy);
 return Math.min(ax>1e-6?(d.gw+2)/ax:1e9,ay>1e-6?(d.gh+2)/ay:1e9);
}
function trimEdge(l){
 const s=l.source,t=l.target;
 let dx=t.x-s.x,dy=t.y-s.y;
 const len=Math.hypot(dx,dy)||1;dx/=len;dy/=len;
 // the head is what carries the direction, so a crowded short edge gives up
 // its tail and keeps its head on the outline it points at
 let b=edgeOff(t,dx,dy)+(l.kind==='in'?2:1);
 if(b>len-1)b=Math.max(0,len-1);
 let a=edgeOff(s,dx,dy)+1;
 if(a>len-1-b)a=Math.max(0,len-1-b);
 l.ex1=s.x+dx*a;l.ey1=s.y+dy*a;
 l.ex2=t.x-dx*b;l.ey2=t.y-dy*b;
}
const gateAim=d=>{
 const t=byId[d.tgt];
 if(!t||!isFinite(t.x))return null;
 return 'rotate('+(Math.atan2(t.y-d.y,t.x-d.x)*180/Math.PI).toFixed(1)+')';
};
// Labels are the real estate that runs out first, so they are placed by
// priority and any that would collide with one already placed is dropped:
// the graph stays readable at every zoom instead of turning into a hedge.
const LBL=[];
const prio=d=>(d.goal?1e6:0)+(d.frontier?1e4:0)+(d.impact||0)*10
 +(d.status==='ESTABLISHED'?1:0);
// Wrap to as many lines as the title needs, up to a budget; only titles that
// genuinely overflow it are elided.  The old two-line cap cut most titles.
// Measured against the corpus: 34 columns over 4 lines shows every claim
// title in full, so the ellipsis is a genuine last resort rather than the
// normal case.  Lines are then balanced -- the narrowest width that still
// fits the same number of lines -- so a label is a tidy block instead of one
// long line followed by a stray word.
const MAXW=34,MAXL=4;
function wrapAt(words,W){
 const ls=[];let cur='';
 for(const w of words){
  const t=cur?cur+' '+w:w;
  if(t.length<=W||!cur){cur=t;continue}
  ls.push(cur);cur=w;
 }
 if(cur)ls.push(cur);
 return ls;
}
function wrapTitle(title){
 const words=title.split(' ');
 let ls=wrapAt(words,MAXW);
 if(ls.length>1){                       // balance: keep the line count, trim width
  const n=ls.length;
  for(let W=Math.ceil(title.length/n);W<MAXW;W++){
   const t=wrapAt(words,W);
   if(t.length<=n){ls=t;break}
  }
 }
 if(ls.length<=MAXL)return ls;
 ls=ls.slice(0,MAXL);
 let last=ls[MAXL-1];
 if(last.length>MAXW-1)last=last.slice(0,MAXW-1).replace(/\s+\S*$/,'');
 ls[MAXL-1]=last+'\u2026';
 return ls;
}
// Labels live in their own layer, created after the node groups so they draw
// ON TOP: a title half-covered by a neighbouring disc is unreadable, and DOM
// order is the only z-order SVG has.
const labelLayer=g.append('g').attr('class','labels');
const claimNodes=nodes.filter(d=>d.type==='claim');
const lab=labelLayer.selectAll('text').data(claimNodes).join('text')
 .attr('text-anchor','middle')
 .style('cursor','pointer')
 .on('click',(e,d)=>{e.stopPropagation();selected=d;highlight(d);show(d)})
 .on('mouseenter',(e,d)=>{if(!dragging&&!selected)highlight(d)})
 .on('mouseleave',()=>{if(!dragging&&!selected)highlight(null)});
lab.each(function(d){
 const title=(d.title||'').replace(/\s+/g,' ').trim();
 const lines=title?wrapTitle(title):[''];
 const txt=d3.select(this);
 lines.forEach((ln,i)=>txt.append('tspan').attr('x',0)
  .attr('dy',i?11:(d.goal?40:31)).text(ln));
 const rad=d.goal?23:(10+Math.min(d.impact*1.5,4));
 const w=Math.max.apply(null,lines.map(l=>l.length))*5.9+8;
 const h=lines.length*11+4,top=d.goal?32:23;
 // the node's real footprint, disc plus title, is what the layout must keep
 // apart -- separating circles alone is what let the text collide
 const rec={d:d,el:this,dx:0,dy:0,rad:rad,w:w,h:h,top:top};
 d.lbl=rec;LBL.push(rec);
});
for(const n of nodes)setRects(n);
linkForce.distance(linkDist);
sim.force('collide',rectCollide());

// ---- hierarchy ------------------------------------------------------------
// Layers alone are not structure: what makes a derivation readable is the
// ORDER within each layer, so premises sit under the thing they prove and
// edges stop crossing.  Three passes, all standard layered-DAG practice:
//   components  -- disjoint regions are laid side by side, so a tangle stays
//                  a local tangle instead of threading through everything;
//   barycentre  -- sweep down and up, putting each node at the average
//                  position of its neighbours in the adjacent layers, which
//                  is what actually removes crossings;
//   slots       -- x by accumulated label width, so a row cannot self-overlap.
// The result anchors the simulation rather than replacing it: the force still
// resolves label collisions, but it starts from, and is held near, a shape
// that reflects the dependency structure.
(function layout(){
 const par={},find=x=>{while(par[x]!==x)x=par[x]=par[par[x]];return x};
 for(const n of nodes)par[n.id]=n.id;
 const adj={};for(const n of nodes)adj[n.id]=[];
 for(const l of REAL){
  const a=l.source.id||l.source,b=l.target.id||l.target;
  if(adj[a]&&adj[b]){adj[a].push(b);adj[b].push(a);
   const ra=find(a),rb=find(b);if(ra!==rb)par[ra]=rb;}
 }
 const bucket={};
 for(const n of nodes)(bucket[find(n.id)]=bucket[find(n.id)]||[]).push(n);
 const comps=Object.keys(bucket).sort((a,b)=>bucket[b].length-bucket[a].length);
 comps.forEach((r,i)=>bucket[r].forEach(n=>{n.comp=i}));

 const LAY={};
 for(const n of nodes)(LAY[n.layer]=LAY[n.layer]||[]).push(n);
 const layers=Object.keys(LAY).map(Number).sort((a,b)=>a-b);
 const norm=n=>{const row=LAY[n.layer];return row.length>1?n.ord/(row.length-1):.5};
 for(const L of layers){
  LAY[L].sort((a,b)=>a.comp-b.comp||(a.title||a.id).localeCompare(b.title||b.id));
  LAY[L].forEach((n,i)=>{n.ord=i});
 }
 for(let it=0;it<8;it++){
  const seq=(it%2)?layers.slice().reverse():layers;
  for(const L of seq){
   for(const n of LAY[L]){
    let sum=0,c=0;
    for(const m of adj[n.id]){
     const o=byId[m];
     if(o&&o.layer!==L&&o.ord!=null){sum+=norm(o);c++}
    }
    n.bary=c?sum/c:norm(n);
   }
   LAY[L].sort((a,b)=>a.comp-b.comp||a.bary-b.bary);
   LAY[L].forEach((n,i)=>{n.ord=i});
  }
 }
 // Hierarchy INFORMS the layout, it does not replace it.  Pinning nodes to
 // computed slots produced rigid 200-wide rows and was unreadable; the force
 // layout packs 2D far better.  So the ordering above is used only to seed
 // positions -- premises start near the things they prove, in an order that
 // already has few crossings -- and then the ordinary forces take over and
 // find the shape.
 const SEED=160;
 for(const L of layers){
  const row=LAY[L];
  row.forEach((n,i)=>{
   n.x=W/2+(i-(row.length-1)/2)*SEED;
   n.y=bandY(n)+(i%2?12:-12);
  });
 }
})();
LBL.sort((a,b)=>prio(b.d)-prio(a.d));
let activeLayoutNodes=nodes,activeLabels=LBL;
let visibleNode=node,visibleLab=lab,visibleLine=line,visibleGate=gate;
// Soft, not silent: a label is dropped only when it is mostly buried under
// one already placed, and a goal, root or frontier claim is never dropped --
// a graph that hides the names of the things it is about is worse than one
// with some overlap.
const LPAD=3;
// LABELS ARE NEVER HIDDEN.  A node without its name is useless, so placement
// only ever CHOOSES A POSITION: each label is tried under the node, above it,
// pushed further out, and offset left/right, and the candidate with the least
// overlap against the labels already placed and the node discs wins.  Overlap
// is minimised, never traded for a disappearing title.
let relabelPending=0;
function scheduleRelabel(){
 if(relabelPending)return;
 relabelPending=setTimeout(()=>{relabelPending=0;relabel()},260);
}
function setPos(o,dx,dy){o.dx=dx;o.dy=dy}
function ovl(a,b){
 const x=Math.min(a[2],b[2])-Math.max(a[0],b[0]);
 if(x<=0)return 0;
 const y=Math.min(a[3],b[3])-Math.max(a[1],b[1]);
 return y>0?x*y:0;
}
function relabel(){
 const sd=document.getElementById('showdead').checked;
 const placed=[],discs=[];
 for(const n of activeLayoutNodes){
  if(n.gone||(n.dead&&!sd)||!isFinite(n.x))continue;
  const r=(n.type==='claim'?(n.goal?23:12):9)+2;
  discs.push([n.x-r,n.y-r,n.x+r,n.y+r,n]);
 }
 for(const o of activeLabels){
  const d=o.d;
  o.el.classList.remove('hidelabel');
  if(d.gone||!isFinite(d.x))continue;
  const w=o.w+LPAD*2,h=o.h+LPAD*2;
  const x0=d.x-w/2,below=d.y+o.top-LPAD;   // absolute box of the default spot
  const up=(d.y-o.rad-6-o.h-LPAD)-below;   // flip above the disc
  // ONLY directly under or directly over the node.  Sliding a title sideways
  // to dodge a collision silently reassigns it to whichever node it lands
  // near, and leaves its own node looking untitled.  Horizontal separation is
  // the layout's job -- the collide force moves NODES apart -- not the
  // label placer's.
  const cands=[[0,0],[0,up]];
  let best=null,bestScore=Infinity;
  for(const c of cands){
   const b=[x0+c[0],below+c[1],x0+c[0]+w,below+c[1]+h];
   let sc=0;
   for(let i=0;i<placed.length;i++)sc+=ovl(b,placed[i]);
   for(let i=0;i<discs.length;i++)
    if(discs[i][4]!==d)sc+=ovl(b,discs[i])*1.6;
   if(sc<bestScore){bestScore=sc;best=[c,b];if(sc===0)break}
  }
  setPos(o,best[0][0],best[0][1]);
  setRects(d);
  placed.push(best[1]);
 }
 placeLabels();
}
node.append('title').text(d=>d.type==='claim'?`${d.id} [${d.status}]`
 :d.type==='group'?`${d.n} established claims — folded`
 :d.type==='junction'?`${d.rtitle||d.route} — ${d.requires.length} inputs, 1 output`
 :(d.rtitle||d.route));
// Focus: hover previews, a click sticks, clicking the background clears.
// A route is highlighted whole -- reaching a junction or a stub pulls in its
// other endpoints, so a multi-premise route never lights up half-drawn.
let selected=null;
const _ends=l=>[l.source.id||l.source,l.target.id||l.target];
// Focus is defined by ROUTE MEMBERSHIP, not by walking the graph.  Expanding
// through neighbours pulled in whole unrelated routes: a hub is adjacent to
// its premises, its premises are adjacent to their own other routes, and the
// obstruction edges of every dead route sharing a killer came along too --
// which is why unrelated red edges lit up.  An edge belongs to exactly one
// route, so the set is exactly the edges of the routes that touch this node.
function focusSet(d){
 const routes=new Set(),keep=new Set([d.id]),edges=new Set();
 if(d.type==='claim'){
  for(const l of REAL){
   const[a,b]=_ends(l);
   if((a===d.id||b===d.id)&&l.route)routes.add(l.route);
  }
 }else if(d.route)routes.add(d.route);
 for(const l of REAL){
  const[a,b]=_ends(l);
  const mine=(l.route&&routes.has(l.route))||a===d.id||b===d.id;
  if(mine){edges.add(l);keep.add(a);keep.add(b)}
 }
 return {keep,edges};
}
function highlight(d){
 if(!d){g.classed('focus',false);
  visibleNode.classed('dim',false).classed('hot',false);
  visibleLab.classed('dim',false).classed('hot',false);
  visibleLine.classed('dim',false).classed('hot',false);return}
 const {keep,edges}=focusSet(d);
 g.classed('focus',true);
 visibleNode.classed('dim',n=>!keep.has(n.id)).classed('hot',n=>n.id===d.id);
 visibleLab.classed('dim',n=>!keep.has(n.id)).classed('hot',n=>n.id===d.id);
 visibleLine.classed('dim',l=>!edges.has(l))
     .classed('hot',l=>{const[a,b]=_ends(l);return a===d.id||b===d.id});
}
// Guards, because the layout moves under a still cursor: without them the
// graph fires enter/leave at itself while you drag or while it settles, and
// each one rewrites classes on every node and edge.
let dragging=false,hoverId=null;
node.on('mouseenter',(e,d)=>{
  if(dragging||selected||hoverId===d.id)return;
  hoverId=d.id;highlight(d)})
 .on('mouseleave',(e,d)=>{
  if(dragging||selected||hoverId!==d.id)return;
  hoverId=null;highlight(null)});
// Every id in a panel is a link into the graph, and every artifact is a link
// out to the file it names -- nothing in the panel is a dead end.
const artlist=arts=>!arts||!arts.length?''
 :'<h3 class="sec">Artifacts</h3><ul class="arts">'+arts.map(a=>
   a[1]?`<li><a href="${esc(a[1])}" target="_blank" rel="noopener">${esc(a[0])}</a></li>`
       :`<li>${esc(a[0])}</li>`).join('')+'</ul>';
function showRoute(rid){
 const base=(DATA.routes||{})[rid];
 if(!base){location.href=rid+'.html';return}
 const request=++panelLoad;
 pbody.innerHTML='<p class="hint">Loading route\u2026</p>';openPanel();
 detailFor(rid).then(extra=>{if(request===panelLoad)
  renderRoute(rid,Object.assign({},base,extra))});
}
function renderRoute(rid,r){
 const blocked=new Set(r.blocked||[]);
 const nq=(r.requires||[]).length;
 let h=`<span class="chip route">route${r.dead?' &middot; failed':''}</span>
  <h2>${esc(r.title||rid)}</h2><code>${esc(rid)}</code>`;
 if(r.touched)h+=`<p class="hint">${r.touched==='9999'?'changed in this checkout'
  :'last changed '+esc(r.touched.slice(0,10))}</p>`;
 // the panel says the same thing the glyph does: what goes in, what comes out
 h+=`<p class="flow">${nq>1?`${nq} inputs &#8594; <b>AND</b> &#8594; 1 output`
  :nq===1?'1 input &#8594; 1 output':'no input (direct proof) &#8594; 1 output'}</p>`;
 h+='<h3 class="sec">Inputs &mdash; all of them required</h3><ul class="fr ctx">';
 if(!nq)
  h+='<li><span class="mk ok">nothing</span> a complete direct proof</li>';
 else for(const q of r.requires)
  h+=`<li>${blocked.has(q)?'<span class="mk open">open</span>':'<span class="mk ok">have it</span>'} ${clink(q)}</li>`;
 h+='</ul>';
 h+=`<h3 class="sec">Output &mdash; ${r.dead?'invalid target':'would establish'}</h3><ul class="fr ctx"><li>${clink(r.target)}</li></ul>`;
 if(r.dead&&((r.killers||[]).length||(r.reasons||[]).length)){
  h+='<h3 class="sec">Why it failed</h3><ul class="fr ctx">';
  for(const k of r.killers)h+=`<li><span class="mk dead">ruled out by</span> ${clink(k)}</li>`;
  if(!(r.killers||[]).length)
   for(const reason of r.reasons||[])h+=`<li><span class="mk dead">invalid</span> ${esc(reason)}</li>`;
  h+='</ul>';
 }
 if(r.html)h+='<h3 class="sec">Argument</h3><div class="stmt">'+r.html+'</div>';
 h+=artlist(r.arts)+`<p><a class="open-page" href="${esc(rid)}.html">open page &#8594;</a></p>`;
 pbody.innerHTML=h;
 afterPanel();
}
function afterPanel(){
 pbody.querySelectorAll('a[data-goto]').forEach(a=>a.onclick=e=>{
  e.preventDefault();selectById(a.dataset.goto)});
 pbody.querySelectorAll('a[data-route]').forEach(a=>a.onclick=e=>{
  e.preventDefault();const rid=a.dataset.route;
  const hub=byId['j:'+rid]||byId['x:'+rid];
  if(hub){selected=hub;highlight(hub)}
  showRoute(rid);pbody.scrollTop=0});
 pbody.querySelectorAll('a[data-focus]').forEach(a=>a.onclick=e=>{
  e.preventDefault();if(window.focusProof)window.focusProof(a.dataset.focus)});
 openPanel();
 if(window.cairnTypeset)cairnTypeset(pbody);
}
// A node means little alone: what matters is how it was reached, what has
// been tried against it, what waits on it, and what it would buy.  The panel
// answers those before it shows the statement.
const RT=id=>(DATA.routes||{})[id]||{};
const claimById={};for(const c of DATA.claims)claimById[c.id]=c;
const routeEntries=Object.entries(DATA.routes||{});
const baseEstablished=new Set(DATA.claims.filter(c=>c.status==='ESTABLISHED').map(c=>c.id));
const baseInvalidated=new Set(routeEntries.filter(x=>x[1].dead).map(x=>x[0]));
function sameSet(a,b){
 if(a.size!==b.size)return false;for(const x of a)if(!b.has(x))return false;return true;
}
function counterfactual(id){
 let prevInv=new Set(),prevRef=new Set(),seen=new Set(['|']);
 for(let step=0;step<64;step++){
  const est=new Set([id]);for(const x of prevRef)est.delete(x);
  let changed=true;
  while(changed){changed=false;
   for(const [rid,r] of routeEntries){
    if(prevInv.has(rid)||prevRef.has(r.target)||est.has(r.target))continue;
    if(r.requires.every(q=>est.has(q))){est.add(r.target);changed=true}
   }
  }
  const ref=new Set();
  for(const c of DATA.claims)if((c.refuters||[]).some(q=>est.has(q)))ref.add(c.id);
  const inv=new Set();
  for(const c of DATA.claims)if(est.has(c.id))for(const rid of c.kills||[])inv.add(rid);
  for(const [rid,r] of routeEntries)
   if(ref.has(r.target)||r.requires.some(q=>ref.has(q)))inv.add(rid);
  if(sameSet(inv,prevInv)&&sameSet(ref,prevRef))return {est,inv,stable:true};
  const key=[...inv].sort().join(',')+'|'+[...ref].sort().join(',');
  if(seen.has(key))return {est,inv,stable:false};
  seen.add(key);prevInv=inv;prevRef=ref;
 }
 return {est:new Set(),inv:new Set(),stable:false};
}
function givesFor(id){
 const x=counterfactual(id);
 return {unstable:!x.stable,
  claims:[...x.est].filter(c=>c!==id&&!baseEstablished.has(c)).sort(),
  lost:[...baseEstablished].filter(c=>!x.est.has(c)).sort(),
  routes:[...x.inv].filter(r=>!baseInvalidated.has(r)).sort(),
  reopened:[...baseInvalidated].filter(r=>!x.inv.has(r)).sort()};
}
const rlink=rid=>`<a href="#" data-route="${esc(rid)}">${esc(RT(rid).title||rid)}</a>`;
const clink=cid=>{const c=claimById[cid];
 return `<a href="#" data-goto="${esc(cid)}">${esc(c?c.title:cid)}</a>`};
function routeMark(rid){
 const r=RT(rid);
 if(r.dead)return '<span class="mk dead">failed</span>';
 if(r.status==='COMPLETE')return '<span class="mk ok">proves it</span>';
 return `<span class="mk open">needs ${(r.blocked||[]).length}</span>`;
}
function sec(label,n,rows){
 if(!rows.length)return '';
 return `<h3 class="sec">${label}<span class="ct">${n}</span></h3>`
  +'<ul class="fr ctx">'+rows.join('')+'</ul>';
}
function routeRow(rid,note){
 const r=RT(rid),k=r.killers||[],bl=r.blocked||[],why=r.reasons||[];
 let sub='';
 if(note)sub+=`<span class="sub">${note}</span>`;
 if(k.length)sub+=`<span class="sub">ruled out by ${k.map(clink).join(', ')}</span>`;
 else if(r.dead&&why.length)sub+=`<span class="sub">${esc(why.join('; '))}</span>`;
 else if(bl.length)sub+=`<span class="sub">still needs ${bl.map(clink).join(', ')}</span>`;
 return `<li>${routeMark(rid)}${rlink(rid)}${sub}</li>`;
}
function ctx(d){
 let h='';
 const into=d.into||[],needs=d.needs||[],kills=d.kills||[];
 if(d.status==='ESTABLISHED'&&d.via)
  h+=sec('Established by',1,[routeRow(d.via)]);
 if(d.status==='REFUTED')
  h+=sec('Proved false by',(d.refuted_by||[]).length,
         (d.refuted_by||[]).map(c=>`<li><span class="mk dead">refuter</span>${clink(c)}</li>`));
 const live=into.filter(r=>r!==d.via&&!RT(r).dead);
 const dead=into.filter(r=>r!==d.via&&RT(r).dead);
 h+=sec(d.status==='ESTABLISHED'?'Other routes':d.status==='REFUTED'?'Invalid routes':'Routes open',live.length,
        live.map(r=>routeRow(r)));
 if(!into.length&&d.status==='OPEN')
  h+='<h3 class="sec">Routes</h3><p class="hint">None — nothing in the graph yet proposes how to get this.</p>';
 h+=sec('Needed by',needs.length,needs.map(r=>
   routeRow(r,'establishes '+clink(RT(r).target))));
 const g=d.status==='OPEN'?givesFor(d.id):null;
 if(g){
  const rows=[];
  if(g.unstable)
   rows.push('<li class="hint">no stable invalidation fixpoint for this counterfactual</li>');
  for(const c of g.claims.slice(0,12))
   rows.push(`<li><span class="mk ok">unlocks</span>${clink(c)}</li>`);
  if(g.claims.length>12)
   rows.push(`<li class="hint">\u2026and ${g.claims.length-12} more unlocked</li>`);
  for(const c of g.lost.slice(0,12))
   rows.push(`<li><span class="mk dead">retracts</span>${clink(c)}</li>`);
  for(const r of g.routes.slice(0,8))
   rows.push(`<li><span class="mk dead">closes</span>${rlink(r)}</li>`);
  for(const r of g.reopened.slice(0,8))
   rows.push(`<li><span class="mk ok">reopens</span>${rlink(r)}</li>`);
  h+=sec('If established',rows.length,rows);
 }
 h+=sec('Rules out',kills.length,kills.map(r=>
   routeRow(r,'a route to '+clink(RT(r).target))));
 // failed attempts last: history, not the way forward
 h+=sec('Failed attempts',dead.length,dead.map(r=>routeRow(r)));
 return h;
}
function showRegion(gn){
 panelLoad++;
 pbody.innerHTML=`<span class="chip ESTABLISHED">${gn.open?'EXPANDED':'FOLDED'}</span>
  <h2>${gn.n} established claims</h2>
  <p class="hint">${gn.open
   ?'Back on the canvas as ' +gn.n+' separate claims.'
   :'A settled interior &mdash; every claim in here is proved, and no open route reads from one of them directly.'}
  Nothing about the graph changed: folding is a way of looking at it.</p>
  <p>${gn.open?`<a href="#" data-fold="${gn.id}">fold it back &#8594;</a>`
              :`<a href="#" data-expand="${gn.id}">expand this region &#8594;</a>`}</p>
  <h3 class="sec">Contains</h3><ul class="fr">`
  +gn.members.map(m=>`<li data-id="${m}">${esc(byId[m].title)}<br><span class="imp">${m}</span></li>`).join('')
  +'</ul>';
 afterPanel();
 pbody.querySelectorAll('li[data-id]').forEach(li=>
  li.onclick=()=>selectById(li.dataset.id));
 pbody.querySelectorAll('a[data-expand]').forEach(a=>a.onclick=e=>{
  e.preventDefault();expandRegion(a.dataset.expand);showRegion(byId[a.dataset.expand])});
 pbody.querySelectorAll('a[data-fold]').forEach(a=>a.onclick=e=>{
  e.preventDefault();foldRegion(a.dataset.fold);showRegion(byId[a.dataset.fold])});
 openPanel();
}
// Expanding removes the very node the focus was built around, so the focus
// goes with it -- otherwise the graph stays dimmed around nothing.
function setOpen(gid,v){
 const gn=byId[gid];
 if(!gn||gn.open===v)return;
 gn.open=v;
 if(selected&&(selected===gn||selected.region===gid)){selected=null;highlight(null)}
 refreshVis();sim.alpha(.45).restart();
}
function expandRegion(gid){setOpen(gid,true)}
function foldRegion(gid){setOpen(gid,false)}
function show(d){
 if(d.type==='group'){showRegion(d);return}
 if(d.type==='claim'){
  const request=++panelLoad;
  pbody.innerHTML='<p class="hint">Loading claim\u2026</p>';openPanel();
  detailFor(d.id).then(extra=>{if(request===panelLoad)renderClaim(d,extra)});
 }else{
  showRoute(d.route);
 }
}
function renderClaim(d,extra){
  pbody.innerHTML=`${d.goal?'<span class="chip goal">GOAL</span> ':''}<span class="chip ${d.status}">${d.status}</span>
   <h2>${esc(d.title)}</h2><code>${d.id}</code>
   ${d.lock?`<p class="hint">claimed (${esc(d.lock)})</p>`:''}
   ${ctx(d)}
   <h3 class="sec">Statement</h3>
   <div class="stmt">${extra.html||'(no statement)'}</div>
   ${artlist(extra.arts)}
   <p><a href="#" data-focus="${d.id}">show only ways to prove this claim &#8594;</a></p>
   <p><a class="open-page" href="${d.id}.html">open page &#8594;</a></p>`;
  afterPanel();
}
// Navigating to a folded claim opens its region first: a search result that
// selects a node you cannot see is worse than no result.
selectById=id=>{const d=byId[id];if(!d)return;
 if(d.type==='claim'&&viewMode.value==='focus'&&window.focusProof){
  window.focusProof(id);return}
 if(d.region&&byId[d.region]&&!byId[d.region].open&&foldBox.checked)
  expandRegion(d.region);
 selected=d;highlight(d);show(d);pbody.scrollTop=0};
node.on('click',(e,d)=>{e.stopPropagation();selected=d;highlight(d);show(d)});
svg.on('click',()=>{selected=null;highlight(null);closePanel()});
const foldBox=document.getElementById('fold');
const viewMode=document.getElementById('viewmode');
const focusDepth=document.getElementById('focusdepth');
const routeCap=document.getElementById('routecap');
const routeSort=document.getElementById('routesort');
const depthOut=document.getElementById('depthout');
const routeOut=document.getElementById('routeout');
const scopeCount=document.getElementById('scopeCount');
let focusAnchor=(DATA.claims.find(c=>c.goal)||DATA.claims.find(c=>c.root)
 ||DATA.claims[0]||{}).id;
function routeOrder(a,b){
 const x=a[1],y=b[1];
 const mode=routeSort.value;
 const score=mode==='recent'
  ?(y.touched||'').localeCompare(x.touched||'')
    ||(x.blocked||[]).length-(y.blocked||[]).length
    ||(+x.dead)-(+y.dead)
  :mode==='missing'
  ?(x.blocked||[]).length-(y.blocked||[]).length
    ||(+x.dead)-(+y.dead)
    ||(y.touched||'').localeCompare(x.touched||'')
  :(x.status==='COMPLETE'?0:1)-(y.status==='COMPLETE'?0:1)
    ||(+x.dead)-(+y.dead)
    ||(y.touched||'').localeCompare(x.touched||'');
 return score||a[0].localeCompare(b[0]);
}
const focusInto={};
for(const rid in DATA.routes){const r=DATA.routes[rid];
 (focusInto[r.target]=focusInto[r.target]||[]).push([rid,r])}
function sortWays(){for(const id in focusInto)focusInto[id].sort(routeOrder)}
sortWays();
function currentScope(includeDead=true){
 const claims=new Set(),routes=new Set(),mode=viewMode.value;
 if(mode==='all'){
  for(const c of DATA.claims)claims.add(c.id);
  for(const rid in DATA.routes)routes.add(rid);
 }else if(mode==='goal'){
  for(const c of DATA.claims)if(c.depth!=null)claims.add(c.id);
  for(const rid in DATA.routes){const r=DATA.routes[rid];
   if(claims.has(r.target)&&r.requires.every(q=>claims.has(q)))routes.add(rid)}
 }else{
  let front=new Set([focusAnchor]);claims.add(focusAnchor);
  for(let level=0;level<+focusDepth.value;level++){
   const next=new Set();
   for(const cid of front){
    const choices=includeDead?(focusInto[cid]||[]):(focusInto[cid]||[]).filter(x=>!x[1].dead);
    const chosen=choices.slice(0,+routeCap.value);
    for(const [rid,r] of chosen){routes.add(rid);
     for(const q of r.requires)if(!claims.has(q)){claims.add(q);next.add(q)}}
   }
   front=next;
  }
 }
 return {claims,routes};
}
window.focusProof=id=>{
 if(!byId[id]||byId[id].type!=='claim')return;
 focusAnchor=id;viewMode.value='focus';
 selected=byId[id];refreshVis();highlight(selected);show(selected);
 setTimeout(()=>window.focusNode&&focusNode(selected),80);
};
viewMode.onchange=refreshVis;
focusDepth.oninput=()=>{depthOut.value=focusDepth.value;refreshVis()};
routeCap.oninput=()=>{routeOut.value=routeCap.value;refreshVis()};
routeSort.onchange=()=>{sortWays();refreshVis()};
if(groups.length){
 const hid=groups.reduce((a,gn)=>a+gn.n,0);
 // default to folded only when the settled part is genuinely in the way
 foldBox.checked=hid>=25;
 document.getElementById('foldbox').title=
  hid+' established claims fold into '+groups.length+' block'+(groups.length>1?'s':'');
}else document.getElementById('foldbox').style.display='none';
foldBox.onchange=()=>{
 if(foldBox.checked)for(const gn of groups)gn.open=false;
 refreshVis();sim.alpha(.45).restart();
};
function refreshVis(){
 const sd=document.getElementById('showdead').checked;
 const fold=foldBox.checked&&viewMode.value!=='focus';
 const scope=currentScope(sd);
 // Scope first, then optionally replace settled interiors with region nodes.
 nodes.forEach(d=>{const inside=d.type==='claim'?scope.claims.has(d.id)
  :(d.type==='junction'||d.type==='stub')?scope.routes.has(d.route)
  :d.type==='group'&&d.members.some(id=>scope.claims.has(id));
  d.hidden=!inside||(d.type==='group'?(!fold||d.open)
   :d.region?(fold&&!byId[d.region].open):false)});
 nodes.forEach(d=>{d.gone=d.hidden});
 node.classed('orphan',d=>d.gone);
 lab.classed('orphan',d=>d.gone);
 line.classed('gone',l=>{
  const a=byId[l.source.id||l.source],b=byId[l.target.id||l.target];
  return (a&&a.gone)||(b&&b.gone);
 });
 g.classed('showdead',sd);
 const activeNodes=nodes.filter(d=>!d.gone&&(!d.dead||sd));
 const activeIds=new Set(activeNodes.map(d=>d.id));
 const activeLinks=links.filter(l=>activeIds.has(l.source.id||l.source)
  &&activeIds.has(l.target.id||l.target));
 activeLayoutNodes=activeNodes;
 activeLabels=LBL.filter(o=>activeIds.has(o.d.id));
 visibleNode=node.filter(d=>activeIds.has(d.id));
 visibleLab=lab.filter(d=>activeIds.has(d.id));
 visibleLine=line.filter(l=>activeIds.has(l.source.id||l.source)
  &&activeIds.has(l.target.id||l.target));
 visibleGate=gate.filter(d=>activeIds.has(d.id));
 node.classed('dim',false).classed('hot',false);
 lab.classed('dim',false).classed('hot',false);
 line.classed('dim',false).classed('hot',false);
 sim.nodes(activeNodes);
 linkForce.links(activeLinks).strength(l=>l.kind==='aff'?.03+.1*l.w:.5);
 sim.force('charge',d3.forceManyBody().strength(-430));
 scopeCount.textContent=activeNodes.length+' shown';
 sim.alpha(.5).restart();
 relabel();
}
document.getElementById('showdead').onchange=refreshVis;
function placeLabels(){
 for(const o of activeLabels)
  o.el.setAttribute('transform',
   'translate('+(o.d.x+o.dx)+','+(o.d.y+o.dy)+')');
}
sim.on('tick',()=>{
 visibleLine.each(trimEdge)
     .attr('x1',l=>l.ex1).attr('y1',l=>l.ey1)
     .attr('x2',l=>l.ex2).attr('y2',l=>l.ey2);
 visibleNode.attr('transform',d=>`translate(${d.x},${d.y})`);
 visibleGate.attr('transform',gateAim);
 placeLabels();
 scheduleRelabel();
});
let fitted=false;
// Even a narrowed proof can be wider than the viewport. Open at its anchor at
// a readable scale; the reader can then pan or narrow the depth/route limits.
window.goHome=goHome;
function goHome(){
 const target=nodes.find(n=>n.goal)||nodes.find(n=>n.root)||nodes[0];
 if(!target||!isFinite(target.x))return;
 const k=0.85;
 svg.transition().duration(500).call(zoom.transform,
  d3.zoomIdentity.translate(W/2-k*target.x,H*0.28-k*target.y).scale(k));
}
// Move to the anchor once the scoped layout has settled. Later zooming belongs
// to the reader, so this never fires again.
sim.on('end',()=>{relabelPending=0;relabel();
 if(!fitted){fitted=true;goHome()}});
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
    the mathematics is typeset; fall back to the source host only for files that are
    not in the working tree (revision pins) or too large to publish."""
    out = []
    for p in paths:
        p = str(p)
        if p.startswith(("http://", "https://")):
            out.append((p, p))
        elif ":" in p and not os.path.exists(os.path.join(REPO, p)):
            rev, _, path = p.partition(":")
            out.append((p, f"{root}/blob/{rev}/{path}" if root else None))
        elif (p.startswith("research/") and p.endswith(".md")
              and ID_RE.match(os.path.basename(p)[:-3])):
            # Canonical research files already have a richer node page.  The
            # old site rendered each one a second time as a generic source
            # page, doubling a large graph's page count and markdown work.
            out.append((p, os.path.basename(p)[:-3] + ".html"))
        elif _repo_has(p):
            REFERENCED_FILES.add(p)
            out.append((p, file_page_name(p)))
        else:
            out.append((p, f"{root}/blob/{ref}/{p}" if root else None))
    return out


FILE_PAGE_CAP = 5000
GITHUB_MARK = (
    '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" '
    'fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 '
    '5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49'
    '-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 '
    '1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2'
    '-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 '
    '.67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 '
    '2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07'
    '-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 '
    '.21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg>')


def source_link(web, ref, path):
    """`View source` pointing at wherever the repository is hosted."""
    if not web:
        return ""
    host = re.sub(r"^https?://", "", web).split("/")[0]
    mark = GITHUB_MARK if host == "github.com" else ""
    name = "GitHub" if host == "github.com" else host
    return (f'<a class="srclink" href="{web}/blob/{ref}/{path}" target="_blank" '
            f'rel="noopener">{mark}<span>View source on {name}</span></a>')


def write_file_pages(ids, web, ref, tex_cache):
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
            src = source_link(web, ref, path)
            head = (f"<h1><span class='node'>file</span>{html.escape(path)}</h1>"
                    f"<p class='muted'>{src}</p>")
            if path.lower().endswith(".md"):
                body = text.split("---", 2)[-1] if text.startswith("---") else text
                rendered = autolink(md_to_html(body, ids, tex_cache), ids)
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
              f"(cap {FILE_PAGE_CAP} or unreadable); those links fall back "
              f"to the source host")
    return written


def generate_site(graph, locks):
    os.makedirs(SITE_DIR, exist_ok=True)
    REFERENCED_FILES.clear()
    web, ref = _web_root(), _web_ref()
    # index = the graph, full viewport
    idset = set(graph.nodes)
    rendered_bodies = {}
    tex_cache = {}

    def render_body(body):
        if body not in rendered_bodies:
            rendered_bodies[body] = autolink(
                md_to_html(body, idset, tex_cache), idset)
        return rendered_bodies[body]

    depths = goal_depths(graph)
    data = {"claims": [], "links": [], "junctions": [], "dead": [], "affinity": [],
            "routes": {}, "maxDepth": max(depths.values(), default=0)}
    details = {}
    search_rows = []
    display_frontier = set(actionable_frontier(graph))
    for cid, c in graph.claims.items():
        data["claims"].append({
            "id": cid, "status": c.status, "root": bool(c.meta.get("root")),
            "goal": bool(c.meta.get("goal")),
            "title": c.title, "impact": graph.claim_impact.get(cid, 0),
            "frontier": cid in display_frontier,
            "depth": depths.get(cid),
            "lock": fmt_remaining(locks[cid]) if cid in locks else None,
            "via": graph.provenance.get(cid),
            "refuted_by": graph.refuted_by.get(cid, []),
            "refuters": c.get_list("refuted_by"),
            "into": graph.routes_into.get(cid, []),
            "needs": graph.required_by.get(cid, []),
            "kills": [r for r in c.get_list("invalidates") if r in graph.routes]})
        details[cid] = {
            "html": render_body(c.body),
            "arts": artifact_links(c.get_list("artifacts"), web, ref),
        }
        search_rows.append({
            "id": cid, "kind": "claim", "status": c.status,
            "goal": bool(c.meta.get("goal")), "title": c.title,
            "text": re.sub(r"\s+", " ", c.body).strip(),
        })
    touched = recent_touch_stamps(graph)
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
            "killers": killers, "status": r.status,
            "touched": touched.get(rid),
            "blocked": list(getattr(r, "blocked_on", []) or [])}
        details[rid] = {
            "reasons": list(r.status_reasons),
            "arts": artifact_links(r.get_list("artifacts"), web, ref),
            "html": render_body(r.body),
        }
        search_rows.append({
            "id": rid, "kind": "route", "status": r.status,
            "goal": False, "title": r.title,
            "text": re.sub(r"\s+", " ", r.body).strip(),
        })
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
    data["affinity"] = semantic_affinity(
        semantic_vectors(graph.claims, titles_only=True), threshold=0.45)
    est = sum(1 for c in graph.claims.values() if c.status == "ESTABLISHED")
    ref = sum(1 for c in graph.claims.values() if c.status == "REFUTED")
    stats = (f"{len(graph.claims)} claims · {est} established · {ref} refuted · "
             f"{len(graph.routes)} routes · {len(display_frontier)} frontier holes")
    idx = (INDEX_TMPL.replace("__KATEX__", KATEX)
                     .replace("__PALETTE__", PALETTE)
                     .replace("__SANS__", SANS).replace("__MONO__", MONO)
                     .replace("__SEARCH_JS__", SEARCH_JS)
                     .replace("__DATA__", json.dumps(
                         data, separators=(",", ":")).replace("</", "<\\/"))
                     .replace("__STATS__", html.escape(stats))
                     .replace("__TITLE__", html.escape(SITE_TITLE)))
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(idx)
    data_dir = os.path.join(SITE_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)
    shards = {}
    for nid, detail in details.items():
        key = nid[0] if nid and nid[0].isalnum() else "_"
        shards.setdefault(key, {})[nid] = detail
    for key, shard in shards.items():
        with open(os.path.join(data_dir, f"details-{key}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(shard, f, separators=(",", ":"))
    with open(os.path.join(data_dir, "search.json"), "w", encoding="utf-8") as f:
        json.dump(search_rows, f, separators=(",", ":"))
    # secondary: plain listing
    B = ["<h1>All nodes</h1>",
         "<table><tr><th>id</th><th>kind</th><th>status</th><th>title</th></tr>"]
    for nid, n in sorted(graph.nodes.items()):
        B.append(f"<tr><td class='art'><a href='{nid}.html'>{nid}</a></td>"
                 f"<td>{n.kind}</td><td>{badge(n.status)}</td>"
                 f"<td><a href='{nid}.html'>{html.escape(n.title)}</a></td></tr>")
    B.append("</table>")
    with open(os.path.join(SITE_DIR, "nodes.html"), "w", encoding="utf-8") as f:
        f.write(page(f"{SITE_TITLE} — all nodes", "\n".join(B)))
    for nid, n in graph.nodes.items():
        goalmark = (f'<span class="badge" style="background:{GOAL_COLOR}">GOAL</span> '
                    if n.meta.get("goal") else "")
        src = html.escape(n.relpath)
        if web:
            srclink = (f"<a href='{web}/blob/{ref}/{src}' target='_blank' "
                       f"rel='noopener'>{src}</a>")
        else:
            srclink = src
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
            rel("Proved false by", n.get_list("refuted_by"))
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
        B.append(render_body(n.body) if n.body
                 else "<p class='muted'>(no body)</p>")
        with open(os.path.join(SITE_DIR, f"{nid}.html"), "w", encoding="utf-8") as f:
            f.write(page(f"{nid}", "\n".join(B)))
    write_file_pages(idset, web, ref, tex_cache)
    return SITE_DIR


# ---------------------------------------------------------------------------
# git helpers (for check/preview --changed)
# ---------------------------------------------------------------------------

def _git(*argv):
    return subprocess.run(["git", "-C", REPO] + list(argv),
                          capture_output=True, text=True)


def recent_touch_stamps(graph, window=300):
    """Node id -> comparable history timestamp for recently touched nodes.

    The bounded history window makes this cheap on a large repository. Nodes
    older than the window intentionally have no stamp and sort behind recent
    work. Uncommitted nodes sort first. Filesystem mtimes are used only outside
    a git checkout, where there is no portable history to consult.
    """
    hist, stamp = {}, None
    r = _git("log", "-n", str(window), "--format=%x01%cI",
             "--name-only", "--", "research")
    for line in (r.stdout.splitlines() if r.returncode == 0 else []):
        if line.startswith("\x01"):
            stamp = line[1:]
        elif line and stamp:
            hist.setdefault(line, stamp)

    pending = changed_research_files() or set()
    out = {}
    for n in graph.nodes.values():
        if n.id in pending:
            out[n.id] = "9999"
        elif n.relpath in hist:
            out[n.id] = hist[n.relpath]
        elif r.returncode != 0:
            try:
                out[n.id] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                          time.localtime(os.path.getmtime(n.path)))
            except OSError:
                pass
    return out


def recently_touched(graph, limit=8):
    """(day, node) newest first — by when each node last changed in HISTORY,
    not on disk. FRONTIER.md is a committed file and mtime is not committed:
    a fresh clone stamps every path with the checkout time, so ordering this
    section by mtime made the generated file disagree with itself on every
    machine that regenerated it — a diff nobody wrote, in every checkout.
    An uncommitted edit is the most recent thing there is, so it sorts first.
    A node the log window does not reach is the opposite of that — older than
    every stamp we hold — and the two must not share a sentinel. Once a graph
    grows past the window most of it misses, and one sentinel for both cases
    ties nearly every node at "newest", where the tiebreak is the id: the
    section then prints the alphabetically-last nodes as the latest work, in
    every checkout, and nothing about it looks wrong.
    """
    stamps = recent_touch_stamps(graph)
    today = time.strftime("%Y-%m-%d")
    dated = [n for n in graph.nodes.values() if n.id in stamps]
    ordered = sorted(dated, key=lambda n: (stamps[n.id], n.id),
                     reverse=True)[:limit]
    return [(today if stamps[n.id] == "9999" else stamps[n.id][:10], n)
            for n in ordered]


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
        want = [p for p in r.stdout.splitlines()
                if p.endswith(".md") and os.path.basename(p) not in NON_NODE_FILES
                and "/" not in p[len("research/"):]]
        # One `git cat-file --batch` for the whole tree. A `git show` per node
        # meant ~900 process spawns, which is the entire reason preview ran a
        # hundred times slower than every other command — and why the loop
        # step it implements got skipped.
        for path, blob in _cat_file_batch([f"HEAD:{p}" for p in want], want):
            with open(os.path.join(tmp, os.path.basename(path)), "wb") as f:
                f.write(blob)
    graph, errors = compile_graph(research_dir=tmp, repo=REPO)
    return graph, errors, tmp


def _cat_file_batch(revs, paths):
    """(path, bytes) for each rev, over a single git process. The batch
    protocol is `<sha> <type> <size>\\n<size bytes>\\n` per request, or a
    single line ending in `missing` when the object is not there."""
    if not revs:
        return
    proc = subprocess.run(["git", "-C", REPO, "cat-file", "--batch"],
                          input="\n".join(revs).encode() + b"\n",
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        return
    out, at = proc.stdout, 0
    for path in paths:
        nl = out.find(b"\n", at)
        if nl < 0:
            return
        header = out[at:nl].split()
        at = nl + 1
        if len(header) < 3:      # "<rev> missing"
            continue
        size = int(header[2])
        yield path, out[at:at + size]
        at += size + 1           # trailing newline git adds after each blob


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
        cost[c] = (0 if c in done else INF if c in graph.refuted
                   else (INF if c in plans else 1))
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
         "refuted": sorted(new.refuted - old.refuted),
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
    obstruction_sensitive = any(c.get_list("invalidates") or c.get_list("refuted_by")
                                for c in new.claims.values())
    if anchors and not obstruction_sensitive:
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

def write_node_cache(graph):
    """Atomically persist parsed source so query commands avoid reparsing."""
    path = os.path.join(CACHE_DIR, "nodes.sqlite3")
    fd, tmp = tempfile.mkstemp(prefix="nodes-", suffix=".sqlite3.tmp",
                               dir=CACHE_DIR)
    os.close(fd)
    db = None
    try:
        db = sqlite3.connect(tmp)
        db.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, relpath TEXT NOT NULL,
                meta TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE errors (
                severity TEXT NOT NULL, rule TEXT NOT NULL, message TEXT NOT NULL);
        """)
        db.executemany("INSERT INTO cache_meta VALUES (?, ?)", [
            ("format", str(CACHE_FORMAT)), ("cairn", __version__),
            ("sources", json.dumps(graph.source_manifest,
                                   separators=(",", ":")))])
        db.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?)", (
            (nid, node.kind, node.relpath,
             json.dumps(node.meta, separators=(",", ":")), node.body)
            for nid, node in graph.nodes.items()))
        db.executemany("INSERT INTO errors VALUES (?, ?, ?)",
                       graph.compile_errors)
        db.commit()
        db.close()
        db = None
        os.replace(tmp, path)
    finally:
        if db is not None:
            db.close()
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def write_outputs(graph):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, "graph.json")
    fd, tmp_path = tempfile.mkstemp(prefix="graph-", suffix=".json.tmp",
                                    dir=CACHE_DIR, text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(graph.to_json(graph.source_manifest), f,
                  separators=(",", ":"))
    os.replace(tmp_path, cache_path)
    write_node_cache(graph)
    with open(os.path.join(RESEARCH_DIR, "FRONTIER.md"), "w", encoding="utf-8") as f:
        f.write(generate_frontier_md(graph, all_locks()))


def cmd_check(args):
    graph, errors = compile_graph(use_cache=False)
    graph_valid = not any(sev == "error" for sev, _, _ in errors)
    changed = changed_research_files()
    only = None
    if args.changed:
        only = changed
        if only is None:
            print("WARNING: git unavailable; checking everything", file=sys.stderr)
    dups = duplicate_findings(graph, only_ids=only) if graph_valid else []
    policy_sev = "error" if args.changed else "warning"
    for cid, cand, score in dups:
        errors.append((policy_sev, "duplicate", f"possible duplicate claim: {cid} vs {cand} "
                       f"(similarity {score}); if genuinely distinct, add to {cid}:\n"
                       f"  distinct_from:\n    {cand}: <why this is not that>"))
    # naming a hole is not finishing it: a NEW open claim must record at
    # least one attack (or say why the attack is deferred) before parking
    prev = previous_graph(changed) if graph_valid else None
    new_open = []
    if prev is not None:
        new_open = sorted(cid for cid in graph.claims
                          if cid not in prev.nodes
                          and graph.claims[cid].status == "OPEN")
    parked = [cid for cid in new_open
              if not graph.claims[cid].meta.get("goal")
              and missing_attempts(graph.claims[cid].body)]
    for cid in parked:
        errors.append((policy_sev, "parked",
                       f"{graph.claims[cid].relpath}: new open claim {cid} parks a "
                       "hole with no recorded attack — add an '## Attempts' section: "
                       "at least one approach and where it dies, or one line on why "
                       "the attack is deferred. Writing down where the obvious "
                       "attack fails is where the next one usually comes from"))
    nerr = report_errors(errors, fail_on_warning=args.strict)
    # Only the lane tops: reconnecting one carries its dependents with it, so
    # listing the dependents too is the same fix printed several times.
    if graph_valid and graph.detached_tops:
        print("to reconnect a lane: add a route from a reachable claim to its "
              "top, or mark the top root: true if it is a genuine program "
              "target. nearest reachable claims by similarity:", file=sys.stderr)
        for cid in graph.detached_tops[:HINT_LIMIT]:
            near = [m.id for _, m in similar_nodes(
                graph.claims[cid].title, graph.claims, limit=4, threshold=0.2,
                exclude={cid}, min_overlap=1) if m.reachable][:2]
            print(f"  {cid}" + (f" ~ {', '.join(near)}" if near else " ~ (none)"),
                  file=sys.stderr)
        if len(graph.detached_tops) > HINT_LIMIT:
            print(f"  ... and {len(graph.detached_tops) - HINT_LIMIT} more "
                  "(`cairn check --json` for all)", file=sys.stderr)
    # momentum, printed while the author's context is still loaded
    delta = kinetic_delta(prev, graph) if graph_valid and prev is not None else None
    n_unlocked = 0
    if delta and any(delta.values()):
        n_unlocked = sum(len(v) for v in delta.values())
        print("unlocked by this change:")
        if delta["established"]:
            print("  established: " + ", ".join(delta["established"]))
        if delta["refuted"]:
            print("  refuted: " + ", ".join(delta["refuted"]))
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
    if graph_valid:
        write_outputs(graph)
        print(f"compiled {len(graph.claims)} claims + {len(graph.routes)} routes -> "
              f".cairn/cache/graph.json, research/FRONTIER.md"
              + ("" if errors else " — check clean"))
    else:
        print("compile failed; derived outputs left untouched")
    if not nerr:
        return EXIT_OK
    n_policy = (len(dups) + len(parked)) if (args.changed or args.strict) else 0
    return EXIT_INVALID if nerr - n_policy > 0 else EXIT_DUP


def cmd_preview(args):
    old, _, tmp = head_graph()
    shutil.rmtree(tmp, ignore_errors=True)
    new, errors = compile_graph(use_cache=False)
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
    errs = [m for s, _, m in errors if s == "error"]
    if errs:
        delta["errors"] = errs
        L += ["", "Errors in working tree:"] + [f"  {e}" for e in errs]
    if not old.nodes:
        # unconditional before, so a project with 449 committed claims was
        # told on every preview that it had committed nothing
        L += ["", "No canonical state committed yet — everything above is new."]
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
              "obstruction_sensitive": g.get("obstruction_sensitive", False),
              "counterfactual_unstable": g.get("counterfactual_unstable", False),
              "holes": []}
        if c.status == "ESTABLISHED":
            L.append("  ✓ established — nothing further needed")
        elif c.status == "REFUTED":
            L.append("  REFUTED — proved false; " + "; ".join(c.status_reasons))
        elif not g["holes"]:
            L += [f"  no live route-tree under it — no known path exists yet.",
                  f"  The needed work is route-finding, not lemma-proving: "
                  f"decompose it (`cairn why {gid}`)."]
        else:
            if g.get("obstruction_sensitive"):
                L.append("  (obstruction-sensitive cone: ★ necessity is not inferred "
                         "by forcing every hole at once)")
            elif g.get("counterfactual_unstable"):
                L.append("  (necessity counterfactual has no stable invalidation fixpoint)")
            elif g["connected"] is False:
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


def _haystack(graph, with_notes):
    """Everything searchable, tokenized once: (key, kind, status, title,
    heading tokens, full tokens).

    A sweep asks several questions of the same corpus, so the corpus is built
    one time per invocation rather than one time per question.
    """
    hay = []
    for n in graph.nodes.values():
        head = _tokens(n.title + " " + n.id.replace("-", " "))
        hay.append((n.id, n.kind, n.status, n.title, head,
                    head | _tokens(n.body)))
    if with_notes and os.path.isdir(NOTES_DIR):
        for base, _, files in os.walk(NOTES_DIR):
            for fn in sorted(files):
                if not fn.endswith((".md", ".txt")):
                    continue
                path = os.path.join(base, fn)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        body = f.read()
                except OSError:
                    continue
                head = _tokens(fn.replace("-", " ").replace("_", " "))
                hay.append((os.path.relpath(path, REPO), "note", "-", fn,
                            head, head | _tokens(body)))
    return hay


def _hit_rows(hits):
    return [f"{key:<44} [{kind}/{status}] {title}"
            for _, key, kind, status, title in hits]


def _lexical_hits(query, hay, limit):
    """Rank by how much of the query landed, and where.

    One incidental word shared with a long body is not a match — on a real
    graph that alone returns a page of unrelated claims for a query about
    nothing, and a search that always answers teaches an agent to stop
    reading the answer. So a multi-word query needs either a word in the
    title/id or two words anywhere, and a title match outweighs a body one.
    """
    q = _tokens(query)
    if not q:
        return []
    scored = []
    for key, kind, status, title, head, full in hay:
        inhead, infull = len(q & head), len(q & full)
        if not infull:
            continue
        if len(q) > 1 and not inhead and infull < 2:
            continue
        scored.append(((inhead + infull) / (2 * len(q)), key, kind, status, title))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:limit]


def cmd_search(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    similar = args.cmd == "relevant" or args.similar
    # Recorded usage: agents probe several concepts back to back — runs of up
    # to nine consecutive searches from one agent — because one question per
    # invocation is all the CLI offered. A sweep is one call now: one compile,
    # one pass over notes/, and one answer that says which probes found
    # nothing. What `grep` cannot do, and the reason this command exists, is
    # the [kind/status] column: status is compiled, never stored.
    queries = list(dict.fromkeys(args.query))
    hay = None if similar else _haystack(graph, args.notes)
    groups = []
    for query in queries:
        if similar:
            n = graph.nodes.get(query)
            text = (n.title + " " + n.body[:400]) if n else query
            hits = [(score, m.id, m.kind, m.status, m.title) for score, m in
                    similar_nodes(text, graph.nodes, limit=args.limit,
                                  threshold=0.2, exclude={query}, min_overlap=1)]
        else:
            hits = _lexical_hits(query, hay, args.limit)
        groups.append((query, hits))

    empty = "(nothing similar)" if similar else "(no matches)"
    if len(groups) == 1:
        human = "\n".join(_hit_rows(groups[0][1])) or empty
    else:
        blank = [query for query, hits in groups if not hits]
        total = sum(len(h) for _, h in groups)
        head = f"{len(groups)} probes · {total} hit{'' if total == 1 else 's'}"
        if blank:
            head += " · nothing for: " + ", ".join(blank)
        L = [head]
        for query, hits in groups:
            L.append(f"── {query}")
            L += ["  " + r for r in _hit_rows(hits)] or ["  " + empty]
        human = "\n".join(L)

    payload = {"status": "ok", "queries": queries, "results": [
        {"query": query, "id": key, "kind": kind, "node_status": status,
         "title": title, "score": round(score, 2)}
        for query, hits in groups
        for score, key, kind, status, title in hits]}
    return emit(args, payload, human)


def cmd_impact(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    n = graph.nodes.get(args.id)
    if n is None:
        unknown_node(graph, args.id)
    if n.kind != "claim":
        raise SystemExit(f"{args.id!r} is a route; impact takes a claim")
    if n.status == "REFUTED":
        human = (f"{args.id} [REFUTED] — " + "; ".join(n.status_reasons)
                 + "\nA refuted claim cannot be assumed established for impact analysis.")
        return emit(args, {"status": "ok", "id": args.id,
                           "node_status": "REFUTED", "would_establish": [],
                           "would_unestablish": [], "would_invalidate": [],
                           "would_reactivate": [], "directly_needed_by": []}, human)
    est1, _, inv1, _, stable = graph._solve(forced=frozenset([args.id]))
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
    return emit(args, payload, "\n".join(L))


def cmd_lock(args):
    graph, errors = compile_graph()
    report_errors(errors, brief=True)
    n = graph.nodes.get(args.id)
    if n is None:
        unknown_node(graph, args.id)
    if n.kind != "claim":
        raise SystemExit(f"{args.id!r} is a route; lock claims an open claim")
    if n.status != "OPEN":
        state = "already established" if n.status == "ESTABLISHED" else "proved false"
        raise SystemExit(f"{args.id!r} is {state}; lock claims an open claim")
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
    ref = sum(1 for c in graph.claims.values() if c.status == "REFUTED")
    actionable = actionable_frontier(graph)
    L = [f"{len(graph.claims)} claims ({est} established, {ref} refuted) · "
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
               "refuted": ref,
               "cache_hit": graph.cache_hit,
               "routes": len(graph.routes), "invalidated": len(graph.invalidated),
               "frontier": len(actionable), "root_frontier": len(graph.frontier),
               "toward_goals": len(toward),
               "goals": [{"id": g, "node_status": graph.claims[g].status}
                         for g in graph.goals],
               "locks": sorted(locks)}
    return emit(args, payload, "\n".join(L))


def stakes_lines(graph, cid, waiting):
    """Consequences of granting an open claim under the real solver."""
    est2, _, inv2, _, stable = graph._solve(forced=frozenset({cid}))
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
    if not any(c.get_list("invalidates") or c.get_list("refuted_by")
               for c in graph.claims.values()):
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
        elif n.status == "REFUTED":
            L.append(f"{args.id} [REFUTED — PROVED FALSE] — {n.title}")
            L += ["  " + reason for reason in n.status_reasons]
            for rid in graph.routes_into.get(args.id, []):
                r = graph.routes[rid]
                L.append(f"  invalid route: {rid} — {'; '.join(r.status_reasons)}")
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


def record_telemetry(cmd, argv, code, ms, cpu_ms):
    try:
        path = telemetry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": cmd,
                 "argv": argv, "exit": code, "ms": ms, "cpu_ms": cpu_ms,
                 "max_rss_mb": round(usage.ru_maxrss / rss_divisor, 2),
                 "version": __version__,
                 # which copy of the program ran this. The log is shared
                 # across clones now, so without it "who is stuck" has no
                 # answer; unlike an agent name it costs nobody anything.
                 "from": os.path.basename(REPO)}
        if os.environ.get("CAIRN_AGENT"):
            entry["agent"] = os.environ["CAIRN_AGENT"]
        extra = dict(TELEMETRY_EXTRA)
        if LINT_COUNTS:
            # WHICH rule fired, not just that something did: a 38% failure
            # rate over `check` is unactionable until it names a rule
            extra["lint"] = dict(sorted(LINT_COUNTS.items()))
        if extra:
            entry["extra"] = extra
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # telemetry must never break a command


def read_telemetry():
    entries = []
    try:
        with open(telemetry_path(), encoding="utf-8") as f:
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
    per_cmd, per_exit, per_rule, per_repo = {}, {}, {}, {}
    for e in entries:
        c = per_cmd.setdefault(e["cmd"], {
            "n": 0, "errors": 0, "ms": [], "cpu_ms": [], "rss_mb": [],
            "cache_hits": 0, "cache_misses": 0, "phases": {}})
        c["n"] += 1
        c["errors"] += e["exit"] != 0
        c["ms"].append(e.get("ms", 0))
        if "cpu_ms" in e:
            c["cpu_ms"].append(e["cpu_ms"])
        if "max_rss_mb" in e:
            c["rss_mb"].append(e["max_rss_mb"])
        extra = e.get("extra", {})
        if "cache_hit" in extra:
            c["cache_hits" if extra["cache_hit"] else "cache_misses"] += 1
        for phase, ms in (extra.get("phases_ms") or {}).items():
            c["phases"].setdefault(phase, []).append(ms)
        per_exit[str(e["exit"])] = per_exit.get(str(e["exit"]), 0) + 1
        who = e.get("agent") or e.get("from")
        if who:
            per_repo[who] = per_repo.get(who, 0) + 1
        for rule, n in (extra.get("lint") or {}).items():
            per_rule[rule] = per_rule.get(rule, 0) + n
    unused = sorted(set(COMMANDS) - {"telemetry", "build", "relevant"} - set(per_cmd))

    def percentile(values, fraction):
        if not values:
            return None
        ordered = sorted(values)
        return round(ordered[int((len(ordered) - 1) * fraction)], 2)

    L = [f"{len(entries)} invocations, {entries[0]['ts']} .. {entries[-1]['ts']}", "",
         f"{'command':<12} {'n':>5} {'errs':>5} {'p50':>8} {'p90':>8} {'p99':>8} {'cache':>11}"]
    stats = {}
    for cmd in sorted(per_cmd, key=lambda c: -per_cmd[c]["n"]):
        c = per_cmd[cmd]
        p50, p90, p99 = (percentile(c["ms"], q) for q in (0.5, 0.9, 0.99))
        cache_n = c["cache_hits"] + c["cache_misses"]
        cache = f"{c['cache_hits']}/{cache_n}" if cache_n else "-"
        row = {"n": c["n"], "errors": c["errors"], "p50_ms": p50,
               "p90_ms": p90, "p99_ms": p99,
               "p50_cpu_ms": percentile(c["cpu_ms"], 0.5),
               "p90_rss_mb": percentile(c["rss_mb"], 0.9),
               "cache_hits": c["cache_hits"], "cache_misses": c["cache_misses"],
               "phase_p50_ms": {phase: percentile(values, 0.5)
                                for phase, values in sorted(c["phases"].items())}}
        stats[cmd] = row
        L.append(f"{cmd:<12} {c['n']:>5} {c['errors']:>5} "
                 f"{p50:>8} {p90:>8} {p99:>8} {cache:>11}")
    L += ["", "exit codes: " + ", ".join(f"{k}: {v}" for k, v in sorted(per_exit.items()))]
    if per_rule:
        L += ["", "lint findings by rule — what the graph actually trips on:"]
        L += [f"  {r:<16} {n:>6}" for r, n in
              sorted(per_rule.items(), key=lambda kv: -kv[1])]
    if len(per_repo) > 1:
        L += ["", "by worker: " + ", ".join(
            f"{w} {n}" for w, n in sorted(per_repo.items(), key=lambda kv: -kv[1]))]
    if unused:
        L += ["", "never used (candidates to rethink or cut): " + ", ".join(unused)]
    payload = {"status": "ok", "total": len(entries), "per_command": stats,
               "per_exit": per_exit, "per_rule": per_rule, "per_worker": per_repo,
               "never_used": unused, "log": telemetry_path()}
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
                      "overrides project-root discovery; CAIRN_SITE_TITLE "
                      "names the generated site; CAIRN_STATE overrides where "
                      "leases and the usage log live; CAIRN_AGENT labels this "
                      "worker in the log.")
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
    se = add("search", "lexical search over the graph (and notes/), reporting "
             "each hit's compiled status; several queries sweep in one pass; "
             "--similar ranks by similarity to a node id or free text",
             aliases=("relevant",))
    se.add_argument("query", nargs="+",
                    help="one or more quoted queries; each is probed separately")
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
    t0 = time.perf_counter_ns()
    cpu0 = time.process_time_ns()
    try:
        code = fn(args)
    except BaseException as e:
        code = e.code if isinstance(e, SystemExit) and isinstance(e.code, int) else 1
        if args.cmd != "telemetry":
            record_telemetry(
                args.cmd, sys.argv[1:], code,
                round((time.perf_counter_ns() - t0) / 1_000_000, 2),
                round((time.process_time_ns() - cpu0) / 1_000_000, 2))
        if (isinstance(e, SystemExit) and isinstance(e.code, str)
                and getattr(args, "json", False)):
            print(json.dumps({"status": "error", "error": e.code}, indent=1))
            print(e.code, file=sys.stderr)
            sys.exit(1)
        raise
    if args.cmd != "telemetry":
        record_telemetry(
            args.cmd, sys.argv[1:], code,
            round((time.perf_counter_ns() - t0) / 1_000_000, 2),
            round((time.process_time_ns() - cpu0) / 1_000_000, 2))
    sys.exit(code)


if __name__ == "__main__":
    main()
