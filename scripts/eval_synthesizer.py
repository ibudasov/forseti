#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.settings import get_settings
from tests.fixtures.synthesizer.loader import (
    FIXTURE_DIR,
    evaluate_synthesizer_cases,
    load_synthesizer_cases,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE_DIR)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--live", action="store_true", help="Use Gemini instead of recorded responses.")
    parser.add_argument("--confirm-cost", default="", help="Required for --live. Must be '1'.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.live:
        _require_live_confirmation(args.confirm_cost)
    cases = load_synthesizer_cases(args.fixture)
    result = evaluate_synthesizer_cases(cases, live=args.live)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_render_markdown(result.to_dict()), end="")
    return 0 if result.passed else 1


def _require_live_confirmation(confirm_cost: str) -> None:
    if confirm_cost != "1":
        raise RuntimeError("Live evaluation requires --confirm-cost 1.")
    if not get_settings().VERTEX_AI_PROJECT:
        raise RuntimeError("Live evaluation requires VERTEX_AI_PROJECT.")


def _render_markdown(report: dict) -> str:
    metrics = report["metrics"]
    lines = [
        "# Decision Synthesizer evaluation",
        "",
        f"- Passed: {report['passed']}",
        f"- Cases: {report['total_cases']}",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        f"| Valid-schema rate | {metrics['valid_schema_rate']:.1%} |",
        f"| Citation precision | {metrics['citation_precision']:.1%} |",
        f"| Numeric-claim accuracy | {metrics['numeric_claim_accuracy']:.1%} |",
        f"| Downgrade-only compliance | {metrics['downgrade_only_compliance']:.1%} |",
        f"| Fallback rate | {metrics['fallback_rate']:.1%} |",
        f"| Money-field count | {report['money_field_count']} |",
        f"| Promotion count | {report['promotion_count']} |",
        f"| Fallback memos served | {report['fallback_memo_count']} |",
        f"| Rejected memos without fallback (must be 0) | {report['rejected_without_fallback_count']} |",
        "",
        "| Case | Fallback | Expected | Reasons |",
        "| --- | ---: | ---: | --- |",
    ]
    for case in report["cases"]:
        reason_codes = ", ".join(case["reason_codes"]) or "none"
        lines.append(
            f"| {case['name']} | {case['used_fallback']} | "
            f"{case['expected_fallback']} | {reason_codes} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
