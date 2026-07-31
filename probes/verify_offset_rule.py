"""Replay the 48-scene calibration through the SHIPPED resolver.

The calibration was derived in a scratch script. This runs the same 48 scenes
through `processing.harmonize.resolve_offset` itself, so the numbers quoted in
PLAN.md 5.3 and docs/limitations.md describe the code that actually runs rather
than the analysis that motivated it.

Input is `probes/calibration/calib_measurements.json`, written by the measurement sweep
of 2026-07-31: 8 regions x 6 years, baselines 02.11-05.12, deliberately including
arid tiles (Thar, Kutch) where a dark-target statistic is expected to fail. It is
committed rather than gitignored because it is the evidence behind PLAN.md
5.3.1c, and a probe nobody else can run does not verify anything.
`calib_candidates.json` beside it records the scene ids and band hrefs the sweep
used, so the measurement itself can be repeated.

Ground truth is established from physics, not from the rule under test:
reflectance cannot sit below about -0.02, so an offset-bearing product cannot
hold a distribution floor under ~800 DN. The single offset-PRESENT scene was
confirmed separately against its own tile -- assuming the offset reproduces the
2021 peer's median NDVI (-0.1462 against -0.1511); assuming it absent misses by
0.13.

Run: python -m probes.verify_offset_rule
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from processing import harmonize  # noqa: E402

MEASUREMENTS = Path(__file__).resolve().parent / "calibration" / "calib_measurements.json"

#: The one offset-bearing product in the sample. See the module docstring.
PRESENT = {("chennai-coast", "2022-02-04")}


def truth(row: dict) -> str:
    return "present" if (row["region"], row["date"]) in PRESENT else "absent"


def main() -> int:
    if not MEASUREMENTS.exists():
        print(f"missing {MEASUREMENTS}; run the calibration sweep first")
        return 2

    rows = json.loads(MEASUREMENTS.read_text(encoding="utf-8"))
    wrong, by_basis, inconclusive = [], {}, []

    for row in rows:
        properties = {
            harmonize.BASELINE_KEY: row["baseline"],
            harmonize.OFFSET_FLAG: row["flag"],
        }
        evidence = harmonize.OffsetEvidence(floor_dn=row["p01"],
                                            sample_pixels=10 ** 6)
        decision = harmonize.resolve_offset(evidence, properties)
        got = "present" if decision.present else "absent"

        by_basis[decision.basis] = by_basis.get(decision.basis, 0) + 1
        if not evidence.conclusive:
            inconclusive.append(row)
        if got != truth(row):
            wrong.append((row, got))

    print(f"scenes            : {len(rows)}")
    print(f"resolved by       : {by_basis}")
    print(f"pixel-inconclusive: {len(inconclusive)} "
          f"(fell back to metadata, each carrying a warning)")
    print(f"MISCLASSIFIED     : {len(wrong)}")
    for row, got in wrong:
        print(f"  {row['region']:16} {row['date']:11} base {row['baseline']:6} "
              f"floor {row['p01']:6.0f}  truth {truth(row):8} got {got}")

    # The rule this replaced, for contrast: dark fraction below 1% => present.
    old = sum(1 for r in rows if (r["fractions"]["4"] < 0.01) != (truth(r) == "present"))
    print(f"\nfor contrast, the dark-fraction rule this replaced: {old} misclassified")

    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
