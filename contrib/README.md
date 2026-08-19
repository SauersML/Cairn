# Cairn contrib analyses

These are experimental compiler analyses built on Cairn's public graph model.
They are deliberately outside the tiny core CLI until usage justifies a
permanent command.

## Proof portfolios

`proof_portfolios.py` compiles Cairn's AND/OR route graph into
**inclusion-minimal sets of current open leaf claims sufficient to establish a
target** through already-recorded routes.

```text
python3 contrib/proof_portfolios.py --root /path/to/project TARGET
python3 contrib/proof_portfolios.py --root /path/to/project TARGET --max-size 5 --json
```

The analysis uses a least fixed point, so unsupported dependency cycles do not
manufacture proofs. AND-route prerequisites combine by set union; alternative
routes are OR choices; strict supersets are removed immediately. Candidate
portfolios are finally re-run through Cairn's own solver with those leaves
forced, because forcing a claim can establish an obstruction and invalidate a
route that looked usable syntactically.

`--max-size` bounds the number of open leaves in a portfolio. `--state-limit`
bounds the antichain retained per claim; if it is reached, displayed portfolios
remain solver-verified but the output is explicitly marked incomplete.

Run the standard-library smoke test with:

```text
python3 contrib/test_proof_portfolios.py
```

The fixture includes two genuine portfolios and a pure dependency cycle; the
cycle must contribute no portfolio.

## Genetic frontier

`genetic_frontier.py` uses Cairn's counterfactual solver as a research-landscape
assay.  A forced set of open leaves is treated as a genotype and the re-solved
closure as its phenotype.  This makes several population-genetic notions exact
compiler diagnostics rather than metaphors:

- **positive epistasis**: target-cone consequences established by a pair that
  neither single establishes;
- **antagonistic epistasis**: consequences of a single that disappear in the
  pair because newly established obstructions invalidate routes;
- **synthetic target pairs**: neither leaf reaches the requested target alone,
  but granting both does;
- **outcross distance**: lexical distance between the two proof niches, used
  only as a ranking signal after solver-derived epistasis;
- **balancing selection**: a greedy panel that preserves consequence coverage
  and proof-niche diversity instead of sending every worker to one attractive
  frontier hole.

```text
python3 contrib/genetic_frontier.py --root /path/to/project TARGET
python3 contrib/genetic_frontier.py --root /path/to/project TARGET --pool 40 --json
```

The analysis is read-only.  It never writes canonical nodes and never claims
that a high score is mathematics; every establishment/loss result comes from
`Graph._solve`, including non-monotone route invalidation.

Run its deterministic smoke test with:

```text
python3 contrib/test_genetic_frontier.py
```

The fixture pins both a genuinely synthetic pair and an obstruction-driven
antagonistic pair.