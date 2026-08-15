# cairn

**A build system whose build targets are unknown facts.**

Cairn coordinates long-running research programs — human or agent-driven —
by treating open questions the way a build system treats build targets.
The state of knowledge is a graph of Markdown files in your repository;
the CLI compiles it, computes what is established, what is open, what has
been refuted, and what is worth attacking next.

Every finding is a waymark stacked in unmapped terrain for whoever comes
next.

```text
$ cairn frontier
quadrant-induction-step   The quadrant split preserves the induction hypothesis   OPEN
```

## The kernel

The schema (`rg: 2`) is deliberately tiny — **two objects, one extra
relation, one metadata flag**:

| | |
|---|---|
| **Claim** | a proposition. Unresolved → a hole; established → a reusable theorem. *These are not different object types* — today's open question is tomorrow's lemma. |
| **Route** | a justified implication `AND(requires) ⟹ target`. Its existence asserts the implication is valid; the body carries the argument. `requires: []` asserts a complete direct proof. |
| **invalidates** | an *established* claim can invalidate routes (obstructions, no-go results). Dead routes stay in the graph as a record of failed space. |
| **goal: true** | marks a claim as a top-level human goal. Pure metadata — no effect on compilation — but surfaced everywhere agents look. |

Everything else falls out of one equation:

```text
Solved(Q) = OR over routes into Q of ( AND over their requires )
```

A **reduction** is a route with one prerequisite. An **equivalence** is
two routes, one each way. A **direct proof** is a route with no
prerequisites. An **obstruction** is an established claim that
invalidates routes. None of these are object types; the compiler
recognizes the patterns.

Status is always **computed, never declared**. There is no
`status:` field to edit and no way to assert a claim true except by
giving a route that proves it.

## File format

One node per file in `research/`, flat, filename = `<id>.md`. A claim:

```markdown
---
rg: 2
id: domino-color-balance
kind: claim
title: Every domino covers one dark and one light square
---

Under the standard chessboard coloring, any 2×1 domino placed on the
board covers exactly one dark square and exactly one light square.
```

A route (this one is a complete proof, since `requires` is empty):

```markdown
---
rg: 2
id: prove-color-balance
kind: route
title: Direct proof by adjacency
target: domino-color-balance
requires: []
---

A domino covers two edge-adjacent squares, and the coloring assigns
adjacent squares opposite colors.
```

Allowed keys — claims: `rg, id, kind, title, root, goal, invalidates,
distinct_from, artifacts`; routes: `rg, id, kind, title, target,
requires, artifacts`. Anything else is a lint error. `root: true` marks
program-level targets; reachability and the frontier are computed from
roots. `distinct_from: {other-id: why}` answers the duplicate detector
once you've confirmed two similar claims are genuinely different.
`artifacts:` lists repo paths (proof documents, formalizations, data)
that justify the node.

## Canonical vs noncanonical

- `research/*.md` — the authoritative graph. Humans and agents edit these
  **directly with their normal editing tools**; the CLI never writes them.
- `research/artifacts/` — substantial proof artifacts routes may cite.
- `notes/` — scratch, session logs, thinking out loud. Searchable
  (`cairn search --notes`), but noncanonical: notes can never change
  compiled state, and canonical files may not cite them as justification
  (lint enforces this).
- `.cairn/` — machine state (cache, locks, telemetry, generated site).
  Never commit it.

## The compiler

`cairn check` parses every node, lints it, and solves the fixpoint:
routes fire when all their prerequisites are established; an established
obstruction switches its `invalidates:` targets off; the two are iterated
to a mutually consistent fixpoint (oscillation is reported as an error —
break the cycle). It then derives:

- **status** per node: claims `ESTABLISHED`/`OPEN`; routes
  `COMPLETE`/`OPEN`/`INVALIDATED`, with provenance ("via route …");
- **reachability** from root claims through live routes;
- the **frontier**: open, reachable claims with no live decomposition —
  the actual attack surface;
- **impact**: how many live routes are waiting on each claim;
- warnings for cycles, unreachable open claims, and **possible duplicate
  claims** (token-overlap similarity, silenced by `distinct_from`).

Outputs: `.cairn/cache/graph.json` (machine) and `research/FRONTIER.md`
(human dashboard, regenerated every run).

## CLI

Read-only over canonical files and deliberately small:

```text
check      compile + lint + duplicate detection; refresh FRONTIER.md
           (--changed: duplicates are errors for files changed vs HEAD;
            --strict: fail on warnings)   alias: build
preview    derived-state delta of the working tree vs HEAD, before you commit
status     one-screen program state: counts, goals, top frontier, locks
frontier   unresolved claims worth attacking, highest impact first
why ID     how a node was established (derivation), or why it matters if open
context ID bounded packet for one node: statement, derivation, routes in/out,
           reusable established claims, nearby failed space (--budget tokens)
search Q   lexical search over the graph (--notes to include notes/)
relevant Q nodes similar to a node id or to free text
impact ID  what would flip if this claim were established
lock ID    acquire a TTL work lease (--ttl 45m; re-run to extend)
unlock ID  release it (--force to break another owner's)
next       highest-impact unclaimed frontier claim (--lock to claim it)
site       static HTML site into .cairn/site/
telemetry  usage summary: what workers actually run, and what they never touch
```

Unknown node ids fail with nearest-id suggestions; lock TTLs require an
explicit unit (`900s`, `45m`, `2h`) so a bare number can never silently
mean the wrong timescale. Several commands (`status`, `why`, `relevant`,
the `build` alias) exist because usage telemetry showed workers reaching
for them — `telemetry` closes that loop for your own deployment.

Exit codes are stable for scripting: `0` ok, `2` duplicate candidates,
`3` lease conflict, `4` invalid graph. Every query command takes
`--json`.

Root discovery: the project root is `$CAIRN_ROOT` if set, else the
nearest ancestor of the working directory containing a `research/`
directory. (`preview` and `check --changed` additionally assume the
cairn root is the git repository root.)

## Working with agents

Cairn is designed for many concurrent workers of mixed species. The
loop each worker runs:

1. `cairn next --lock` — get the highest-impact unclaimed frontier hole
   plus its context packet, and lease it (`CAIRN_AGENT` names you).
2. Work on it. Add or edit files in `research/` directly — new routes
   decomposing the claim, a direct-proof route, an obstruction claim.
3. `cairn preview` — see the derived consequences of your edit before
   committing. A new `requires: []` route is flagged loudly: you are
   asserting a complete proof.
4. `cairn check --changed` — duplicates involving your changed files are
   hard errors; everything else compiles.
5. Commit the Markdown. Locks expire on their own; mathematical history
   never contains scheduler state.

Locks are cooperative TTL leases in `.cairn/locks/`, not enforcement.
Telemetry (`.cairn/telemetry.jsonl`, one JSONL record per invocation) is
observability only and can never affect research state.

## The site

`cairn site` renders a self-contained static site: an interactive
force-directed graph of the whole program (goals ringed, established
filled, failed routes dashed red and toggleable), a panel with frontier
and library, per-node pages with statements, derivations, and dead
space. Publish it with GitHub Pages:

```yaml
name: Cairn site
on:
  push:
    branches: [main]
    paths: ['research/**']
permissions: {contents: read, pages: write, id-token: write}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: pip install git+https://github.com/SauersML/Cairn && cairn site
      - uses: actions/upload-pages-artifact@v3
        with: {path: .cairn/site}
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: {name: github-pages}
    steps:
      - uses: actions/deploy-pages@v4
```

## Install

Any one of these; the tool is a single stdlib-only file (Python 3.9+,
no dependencies):

```sh
# as a command
pipx install git+https://github.com/SauersML/Cairn
# or
pip install git+https://github.com/SauersML/Cairn

# or vendor the file into your repo and pin it
curl -fsSL https://raw.githubusercontent.com/SauersML/Cairn/main/cairn.py -o tools/cairn.py

# or run from a checkout
git clone https://github.com/SauersML/Cairn && Cairn/bin/cairn --help
```

## Quick start

A complete project is three files:

```sh
mkdir -p research notes && echo '.cairn/' >> .gitignore

cat > research/main-conjecture.md <<'EOF'
---
rg: 2
id: main-conjecture
kind: claim
title: The main conjecture of the program
root: true
goal: true
---

State the thing you are actually trying to prove.
EOF

cat > research/key-lemma.md <<'EOF'
---
rg: 2
id: key-lemma
kind: claim
title: The key lemma
---

The technical statement everything hinges on.
EOF

cat > research/via-key-lemma.md <<'EOF'
---
rg: 2
id: via-key-lemma
kind: route
title: Reduce the conjecture to the key lemma
target: main-conjecture
requires: [key-lemma]
---

Why the lemma implies the conjecture. This body is a mathematical
commitment: the route's existence asserts the implication is valid.
EOF

cairn check      # compiles; writes research/FRONTIER.md
cairn frontier   # -> key-lemma is the one hole worth attacking
```

The moment someone adds a route with `requires: []` targeting
`key-lemma`, both claims flip to ESTABLISHED on the next `check` —
status is derived, never edited. (CI runs exactly this scenario as its
smoke test.)

## Design principles

- **The CLI never writes canonical files.** Editing mathematics is the
  human's/agent's job, with their own tools; the machine only compiles,
  reports, and leases.
- **Status is computed, never declared.**
- **Failed space is preserved.** Invalidated routes stay visible — in the
  graph, the context packets, and the site — so nobody re-walks dead
  ends.
- **Noncanonical text cannot leak into the proof state.**
- **Scheduler state (locks, telemetry) is never committed.**
- **No third-party dependencies**, one file, boring formats (Markdown +
  restricted YAML frontmatter, JSON out).

## License

Apache-2.0.
