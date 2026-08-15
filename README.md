<div align="center">

# cairn

**A build system whose build targets are unknown facts.**

[![CI](https://github.com/SauersML/Cairn/actions/workflows/ci.yml/badge.svg)](https://github.com/SauersML/Cairn/actions/workflows/ci.yml)

[**Live demo**](https://sauersml.github.io/Cairn/) · [Quick start](#quick-start) · [The kernel](#the-kernel) · [CLI](#the-cli) · [Agents](#working-with-agents)

</div>

---

Cairn coordinates long-running research programs — human or agent-driven —
by treating open questions the way a build system treats build targets.
The state of knowledge is a graph of Markdown files in your repository;
the CLI compiles it: what is established, what is open, what has been
refuted, and what is worth attacking next — **toward which goal**.

```text
$ cairn frontier
TOWARD main-conjecture [OPEN] — The main conjecture of the program
  key-lemma            The quadrant split preserves the induction    OPEN
      ★ on every live path to main-conjecture
      path: key-lemma -> compression-bound -> main-conjecture
```

## The kernel

The schema (`rg: 2`) is deliberately tiny — **two objects, one extra
relation, one metadata flag**:

| | |
|---|---|
| **Claim** | a proposition. Unresolved → a hole; established → a reusable theorem. *These are not different object types* — today's open question is tomorrow's lemma. |
| **Route** | a justified implication `AND(requires) ⟹ target`. Its existence asserts the implication is valid; the body carries the argument. `requires: []` asserts a complete direct proof. |
| **invalidates** | an *established* claim can invalidate routes (obstructions, no-go results). Dead routes stay in the graph as a record of failed space. |
| **goal: true** | marks a claim as a top-level human goal. Pure metadata — no effect on compilation — but it orients everything workers see: the frontier is grouped by goals, why-chains anchor at them, the site hangs from them. |

Everything else falls out of one equation:

```text
Solved(Q) = OR over routes into Q of ( AND over their requires )
```

A **reduction** is a route with one prerequisite. An **equivalence** is
two routes, one each way. A **direct proof** is a route with no
prerequisites. An **obstruction** is an established claim that
invalidates routes. None of these are object types; the compiler
recognizes the patterns.

Status is always **computed, never declared**. There is no `status:`
field to edit and no way to assert a claim true except by giving a
route that proves it.

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

A route — this one a complete proof, since `requires` is empty:

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
program-level targets; reachability is computed from roots.
`distinct_from: {other-id: why}` answers the duplicate detector once
you've confirmed two similar claims are genuinely different.
`artifacts:` lists repo paths (proof documents, formalizations, data)
that justify the node.

| Path | Role |
|---|---|
| `research/*.md` | **Canonical.** The authoritative graph. Humans and agents edit these directly with their normal tools; the CLI never writes them. |
| `research/artifacts/` | Substantial proof artifacts routes may cite. |
| `notes/` | Scratch, session logs, thinking out loud. Searchable (`cairn search --notes`) but **noncanonical**: notes can never change compiled state, and canonical files may not cite them as justification (lint enforces this). |
| `.cairn/` | Machine state — cache, locks, telemetry, generated site. Never commit it. |

## The compiler

`cairn check` parses every node, lints it, and solves the fixpoint:
routes fire when all their prerequisites are established; an established
obstruction switches its `invalidates:` targets off; the two iterate to
a mutually consistent fixpoint (oscillation is an error — break the
cycle). It then derives:

- **status** per node: claims `ESTABLISHED`/`OPEN`; routes
  `COMPLETE`/`OPEN`/`INVALIDATED`, with provenance ("via route …");
- **reachability** from root claims through live routes;
- the **frontier**: open, reachable claims with no live decomposition —
  the actual attack surface;
- **goal cones**: which holes sit on a live route-tree under each goal,
  and which are *necessary* — granting every other open hole still
  doesn't reach the goal without this one (the forced-solve run in
  reverse);
- **impact**: how many live routes are waiting on each claim;
- warnings for cycles, unreachable open claims (with reconnect hints),
  and **possible duplicate claims** (token-overlap similarity, silenced
  by `distinct_from`).

Outputs: `.cairn/cache/graph.json` (machine) and `research/FRONTIER.md`
(human dashboard, regenerated every run).

## The CLI

Read-only over canonical files and deliberately small — twelve commands:

| Command | What it does |
|---|---|
| `check` · `build` | compile + lint + duplicate detection; refresh `FRONTIER.md`. `--changed`: duplicates are errors for files changed vs HEAD · `--strict`: fail on warnings |
| `preview` | derived-state delta of the working tree vs HEAD, *before* you commit |
| `status` | one screen: counts, goals, goal-cone frontier, active locks |
| `frontier` | open holes **grouped by the goals they serve**, necessity first, with the path each hole unblocks. `--goal <id>`: one cone (works for any claim, not just goals) · `--flat`: ungrouped impact-ranked list |
| `why <id>` | established → the derivation tree; open → the live decomposition tree, why it matters, and what's waiting on it |
| `context <id>` | bounded packet for one node: statement, derivation, routes in/out, reusable established claims, nearby failed space (`--budget` tokens) |
| `search <q>` · `relevant` | lexical search over the graph (`--notes` to include notes/); `--similar` ranks by similarity to an id or text |
| `impact <id>` | what would flip if this claim were established |
| `lock <id> --ttl 45m` | claim a hole (advisory, identity-free); every reply lists the full active-lock roster |
| `unlock <id>` | release a claim |
| `site` | static HTML site into `.cairn/site/` (`--serve` to preview) |
| `telemetry` | usage summary: what workers actually run, and what they never touch |

Exit codes are stable for scripting: `0` ok, `2` duplicate candidates,
`3` already claimed, `4` invalid graph, `64` usage error, `1` runtime
error (unknown node, bad TTL). Every query command takes `--json`, and
with `--json` even errors arrive as a JSON envelope on stdout — a
harness never has to parse prose. Unknown node ids fail with nearest-id
suggestions; lock TTLs require an explicit unit (`900s`, `45m`, `2h`)
so a bare number can never silently mean the wrong timescale.

### Shaped by transcripts

The surface is telemetry-governed in both directions: `status`, `why`,
`relevant`, and the `build` alias exist because recorded usage showed
workers reaching for those exact names, and a `next` command was cut
after hundreds of invocations never touched it. The 2.3 output rules
each trace to a real agent session:

- **Line 1 of `why` is always `<id> [STATUS] — …`.** Agents pipe
  through `head -1`; a bare header line teaches them nothing.
- **Query commands collapse graph warnings to one stderr line**;
  `check` prints them all, with reconnect hints. Re-printing nine
  warnings before every command trains agents into `2>/dev/null` —
  which then swallows real errors too.
- **A goal with no route-tree gets a route-finding prompt**, not an
  empty list: "decompose it, don't hunt lemmas" is the mode switch
  agents are worst at making on their own.
- **Holes that resisted prior locked attempts are annotated** from
  telemetry, so a fresh agent doesn't independently grind the same
  attractive hole. Advisory color only — observability state can never
  affect compiled status.

### Momentum (2.4)

The 2.4 additions target one observed failure mode: a worker maps a
hole, gets a green check, commits — and stops, because every signal
rewarded *naming* the problem and nothing distinguished "parked after a
real attack" from "parked unexamined." The tool cannot manufacture
drive; what it can do is make the next concrete action obvious and
cheap at the exact moment the author's context is fully loaded.

- **`check` ends with what the change unlocked.** New establishments,
  routes now one prerequisite from complete, fresh invalidations, and
  plan-cost movement at goals and roots:

  ```text
  unlocked by this change:
    established: beta
    route r1 -> delta: missing only zeta (was 2 open)
    root delta: cheapest mapped plan 2 -> 1 open hole(s)
  ```

  The build-system moment — "three targets just became buildable" —
  shown to the one person best positioned to place the next stone.
- **Naming a hole is not finishing it.** A NEW open claim (vs HEAD)
  with no nonempty `## Attempts` section — at least one attempted
  approach and where it dies, or one line on why the attack is
  deferred — is a warning, and an error under `--changed`. Writing
  down where the obvious attack fails is where the next one usually
  comes from; the lint exists to force that one act of articulation
  before parking. Goal claims are exempt (a goal is a wish, not a
  target you attack directly).
- **New open claims print their nearest established neighbours** ("near
  established `alpha` (0.31) — check whether they already decide it"),
  using the same TF-IDF geometry as the site layout. A fresh hole
  adjacent to proved claims is often already decided by composing
  them, and only the author, right then, is positioned to notice.
- **`why` on an open claim prints the stakes both ways**: what
  establishing it completes and cascades (including goal plan-cost
  movement), and what refuting it — establishing the negation — would
  dead-end. A hole should read as a fork with two prizes, not
  inventory.
- **⚑ marks last-missing holes** in `status`, `frontier`, and
  `FRONTIER.md`: claims that are the single open prerequisite of some
  live route. The cheapest wins float to the surface.

"Cheapest mapped plan" is the least fixpoint of: established costs 0,
an undecomposed open claim costs 1 (itself), a decomposed claim costs
its best route's sum; `None` means no finite mapped plan exists and the
missing work is route-finding, not lemma-proving. It measures the
mapped decomposition only — any claim can still be attacked directly,
but that is not a plan the graph knows about.

## Working with agents

Cairn is designed for many concurrent workers of mixed species. The
loop each worker runs:

1. `cairn status` (or read `research/FRONTIER.md`) — goals, open holes,
   what's already claimed.
2. `cairn frontier` — pick a hole *toward a goal* (★ marks the
   necessary ones); `cairn lock <id> --ttl 45m`, then
   `cairn context <id>` for the bounded working packet.
3. Work on it. Add or edit files in `research/` directly — new routes
   decomposing the claim, a direct-proof route, an obstruction claim.
4. `cairn preview` — the derived consequences of your edit, before
   committing. A new `requires: []` route is flagged loudly: you are
   asserting a complete proof.
5. `cairn check --changed` — duplicates involving your changed files
   are hard errors; everything else compiles.
6. Commit the Markdown. Locks expire on their own; mathematical history
   never contains scheduler state.

Claims are advisory TTL flags in `.cairn/locks/`, not enforcement, and
they carry no identity: everyone is one team, a claim means "someone is
on this," and the TTL cleans up after crashes. Telemetry
(`.cairn/telemetry.jsonl`, one record per invocation — command, argv,
exit, duration, no attribution) is observability only and can never
affect research state.

## The site

`cairn site` renders a self-contained static site: an interactive graph
of the whole program, laid out by the goal hierarchy — goals anchor the
top band, every claim sits at its derivation distance from them, route
junctions hang between their target and its prerequisites, failed
routes (dashed red, toggleable) keep their obstructions beside them,
and anything unreachable parks at the bottom. Goals ringed, established
filled, plus a frontier/library panel and per-node pages with
statements, derivations, and dead space.
**Live demo: <https://sauersml.github.io/Cairn/>** — a small worked
example, rebuilt by CI from scratch on every push.

`cairn site --serve` previews it locally. Publish with GitHub Pages:

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

Root discovery: the project root is `$CAIRN_ROOT` if set, else the
nearest ancestor of the working directory containing a `research/`
directory. (`preview` and `check --changed` additionally assume the
cairn root is the git repository root.)

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
cairn frontier   # -> key-lemma, ★ on every live path to main-conjecture
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
- **Failed space is preserved.** Invalidated routes stay visible — in
  the graph, the context packets, and the site — so nobody re-walks
  dead ends.
- **Noncanonical text cannot leak into the proof state.** Notes and
  telemetry inform display; they can never establish anything.
- **Workers are one team.** No agent identities, no ownership, no
  adversarial machinery — claims are shared signal flags with TTLs.
- **Scheduler state (claims, telemetry) is never committed.**
- **No third-party dependencies.** One file, boring formats (Markdown +
  restricted YAML frontmatter, JSON out).

## License

Apache-2.0.
