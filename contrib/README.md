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
