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
| `.cairn/` | This copy's machine state — compile cache, generated site. Never commit it. |
| `$XDG_STATE_HOME/cairn/<owner>-<repo>/` | The **program's** state — leases and the usage log — keyed off the git remote, so every clone and worktree of the project shares one. Under `.cairn/` a lease was invisible to every worker it should warn, and the log was thrown away with the throwaway clone that wrote it. |

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
- warnings, each of which has to earn its line:
  **possible duplicate claims** — title overlap *and* a TF-IDF gate over
  title + body, because sharing the program's subject is not being the
  same claim, and a negation never matches its positive (silenced for
  good by `distinct_from`);
  **dead work** — every route that needed this hole is invalidated, so
  it stopped being load-bearing;
  **detached lanes** — one line with a count, not one line per claim,
  since reconnecting a lane's top carries everything under it;
  and **circular reasoning** — but never `A ⟺ B`, which is the kernel's
  own way to write an equivalence.

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
| `search <q>…` · `relevant` | lexical search over the graph, each hit tagged with its **compiled status** (`--notes` to include notes/); pass several queries to sweep them in one pass; `--similar` ranks by similarity to an id or text |
| `impact <id>` | what would flip if this claim were established |
| `lock <id> --ttl 45m` | claim a hole (advisory, identity-free); every reply lists the full active-lock roster |
| `unlock <id>` | release a claim |
| `site` | static HTML site into `.cairn/site/` (`--serve` to preview) |
| `telemetry` | usage summary: what workers actually run, what they never touch, which lint rule the graph keeps tripping on, and which copy of the project each invocation came from |

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
- **The log has to answer the question it provokes.** A real program's
  telemetry said `check --changed` failed 64% of the time and could not
  say why, because rows recorded an exit code and nothing else. Every
  lint finding now carries a rule name, so `telemetry` ends with a
  by-rule table. The same log showed 83 commits of graph edits with zero
  recorded invocations — the work was happening in throwaway clones,
  each writing to its own `.cairn/` and taking the evidence with it when
  it was deleted. Leases and the log are keyed off the git remote now.
- **A warning that is usually wrong is worse than no warning.** On a
  449-claim program the old detectors produced 30 findings of which 7
  were real: token overlap flagged whole `id`-prefix families and even
  scored a claim against its own negation, and every claim in a
  detached lane was reported separately. The rewrite leaves 5 — and CI
  now pins each retired false positive as a case that must stay silent.
- **A goal with no route-tree gets a route-finding prompt**, not an
  empty list: "decompose it, don't hunt lemmas" is the mode switch
  agents are worst at making on their own.
- **Holes that resisted prior locked attempts are annotated** from
  telemetry, so a fresh agent doesn't independently grind the same
  attractive hole. Advisory color only — observability state can never
  affect compiled status.
- **`search` takes several queries at once**, because that is how the
  log says agents orient: probes arrive in runs — one program's log has
  a single agent firing nine searches back to back, each a different
  concept, each paying its own process start and its own compile. One
  call now sweeps them all against a corpus tokenized once, and names
  the probes that came back empty. **One incidental word shared with a
  long body is not a match**: a multi-word query needs a word in the
  title/id or two words anywhere, because a search that always answers
  teaches an agent to stop reading the answer. What keeps the command
  from being redundant with `grep` over the same files is the
  `[kind/status]` column: status is computed from the graph, never
  stored in it, so no text search can report it.

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

A multi-premise route draws as a gate whose nose points at the claim it
would establish: premises arrive at its flat back with small hollow
heads, and the one edge out leaves heavy with a solid one, so which way
a route fires is readable from the shape. `fold proven` (in the header)
collapses each settled interior — a connected block of established
claims that no open route reads from directly — into one block carrying
its count. Goals, roots, obstructions and the established claims that
feed open routes always stay explicit, relations that cross a fold are
re-pointed at the block, and navigating to a folded claim opens its
region. Folding is a view: it changes no status, route or file.
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

Cairn is one dependency-free Python file. **Commit it into the repo it
compiles.** Then a clone, a *Download ZIP*, a tarball, and an offline
sandbox all arrive with a working CLI, and nobody who opens the repo
next has a setup step:

```sh
mkdir -p bin
curl -fsSLo bin/cairn https://raw.githubusercontent.com/SauersML/Cairn/main/cairn.py
chmod +x bin/cairn && git add bin/cairn
```

Not a submodule and not a package install. A submodule is *empty* in
every copy that isn't a git clone, and a package install needs a
network — both turn "read the graph" into "first, fix your checkout".
`bin/cairn` finds the project from its own path, so it works from any
directory. To upgrade, re-run the `curl` and commit the diff.

The graph itself is three files:

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

bin/cairn check      # compiles; writes research/FRONTIER.md
bin/cairn frontier   # -> key-lemma, ★ on every live path to main-conjecture
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
