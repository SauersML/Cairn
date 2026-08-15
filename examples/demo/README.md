# Demo project

A tiny worked example: two classic tiling results expressed as a cairn
graph. It exercises every kernel feature:

- an **established goal** — `mutilated-board-untileable` is proved by the
  route `via-color-counting` once its two prerequisite claims are
  established by direct-proof routes;
- an **invalidated route** — `brute-force-enumeration` claimed a direct
  proof and is killed by the established obstruction claim
  `enumeration-lower-bound`;
- an **open goal with a frontier hole** — `deficient-board-tromino-tiling`
  waits on `quadrant-induction-step`, which has no routes into it yet and
  therefore shows up in `cairn frontier`.

Try it from this directory:

```sh
../../bin/cairn check           # compile + lint; writes research/FRONTIER.md
../../bin/cairn frontier        # the one open hole
../../bin/cairn context mutilated-board-untileable
../../bin/cairn impact quadrant-induction-step
../../bin/cairn site            # static site in .cairn/site/
```

To "finish" the demo, write a proof body for `quadrant-induction-step`
and add a route file with `requires: []` targeting it — then re-run
`check` and watch both the claim and the goal flip to ESTABLISHED.
