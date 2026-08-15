---
rg: 2
id: via-quadrant-induction
kind: route
title: Induction on n via the quadrant split
target: deficient-board-tromino-tiling
requires: [tromino-base-case, quadrant-induction-step]
---

Induct on n. The base case is tromino-base-case. For the step,
quadrant-induction-step reduces the 2^n instance to four 2^(n-1)
instances, each covered by the induction hypothesis.
