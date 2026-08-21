#!/usr/bin/env python3
"""Temporary deterministic runner for the audited v2.8.2 transformation."""
from pathlib import Path
import subprocess
import sys


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


helper = Path("tools/apply_nonhyperlinear_fixes.py")
hs = helper.read_text(encoding="utf-8")
hs = replace_once(
    hs,
    "text.count('_negated(') != 2",
    "text.count('_negated(') != 4",
    "negation call-count assertion",
)
hs = replace_once(
    hs,
    "negation callers: expected 2",
    "negation callers: expected 4",
    "negation call-count message",
)
hs = replace_once(
    hs,
    "re.subn(pattern, repl, text, count=1, flags=flags)",
    "re.subn(pattern, lambda _m: repl, text, count=1, flags=flags)",
    "literal replacement function",
)
helper.write_text(hs, encoding="utf-8")
subprocess.run([sys.executable, str(helper)], check=True)

core = Path("cairn.py")
s = core.read_text(encoding="utf-8")
old = "if len(w) > 2 and w not in TEXT_STOPWORDS"
if s.count(old) != 2:
    raise SystemExit(f"short logical token sites: expected 2, got {s.count(old)}")
s = s.replace(old, 'if (len(w) > 2 or w == "no") and w not in TEXT_STOPWORDS')
core.write_text(s, encoding="utf-8")

test = Path("tests/test_nonhyperlinear.py")
ts = test.read_text(encoding="utf-8")
if ts.count(", root=True)") != 5:
    raise SystemExit(f"root fixture calls: expected 5, got {ts.count(', root=True)')}")
ts = ts.replace(", root=True)", ", root_=True)")
if ts.count('"x"') != 2 or ts.count('"y"') != 2:
    raise SystemExit("unexpected short fixture-id count")
ts = ts.replace('"x"', '"xx"').replace('"y"', '"yy"')
old = 'self.assertIn("nearest: side-leaf", r.stderr)'
if ts.count(old) != 1:
    raise SystemExit(f"nearest assertion sites: expected 1, got {ts.count(old)}")
ts = ts.replace(
    old,
    'self.assertIn("nearest:", r.stderr)\n        self.assertIn("side-leaf", r.stderr)',
    1,
)
test.write_text(ts, encoding="utf-8")

readme = Path("README.md")
rs = readme.read_text(encoding="utf-8")
old = (
    "- the **frontier**: open, reachable claims with no live decomposition —\n"
    "  the actual attack surface;\n"
    "- **goal cones**: which holes sit on a live route-tree under each goal,\n"
    "  and which are *necessary* — granting every other open hole still\n"
    "  doesn't reach the goal without this one (the forced-solve run in\n"
    "  reverse);\n"
)
new = (
    "- the **frontier**: undecomposed open claims reachable from roots, plus\n"
    "  undecomposed holes on live route-trees under open human goals — the\n"
    "  actual attack surface even when a goal is not also root-reachable;\n"
    "- **goal cones**: which holes sit on a live route-tree under each goal,\n"
    "  and, when that cone is obstruction-free, which are *necessary* —\n"
    "  granting every other cone hole still doesn't reach the goal without\n"
    "  this one. Cairn deliberately does not infer necessity by forcing every\n"
    "  hole at once in obstruction-sensitive cones, where that counterfactual\n"
    "  is non-monotone;\n"
)
rs = replace_once(rs, old, new, "README goal-cone semantics")
old = "| `impact <id>` | what would flip if this claim were established |"
new = (
    "| `impact <id>` | full counterfactual delta if this claim were established: "
    "claims gained/retracted and routes closed/reactivated |"
)
rs = replace_once(rs, old, new, "README impact row")
readme.write_text(rs, encoding="utf-8")
