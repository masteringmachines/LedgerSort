"""
report.py — the end-of-run summary. Totals use Decimal, so they add up to the cent.
"""

from __future__ import annotations
from collections import defaultdict
from decimal import Decimal

from .classify import Result, Stats
from .money import fmt


def summary(results: list[Result], stats: Stats, mock: bool = False) -> str:
    by_status = defaultdict(int)
    totals: dict[str, Decimal] = defaultdict(Decimal)
    counts: dict[str, int] = defaultdict(int)
    review_gross = Decimal("0")
    for r in results:
        by_status[r.status] += 1
        if r.status == "review":
            review_gross += abs(r.row.amount)
        else:
            totals[r.category] += r.row.amount
            counts[r.category] += 1

    lines = []
    if mock:
        lines.append("*** MOCK MODE: keyword stand-in, not real Jev answers ***")
    lines.append(
        f"{stats.rows} rows: {by_status['rule']} by rule, {by_status['auto']} auto-booked, "
        f"{by_status['review']} need review"
    )
    lines.append(
        f"{stats.groups} merchant group(s) asked about -> {stats.calls} API call(s), "
        f"{stats.cache_hits} answered from cache"
    )
    lines.append("")
    width = max([len(c) for c in totals] + [8])
    lines.append(f"{'category':<{width}}  {'rows':>5}  {'total':>12}")
    lines.append("-" * (width + 21))
    for category in sorted(totals):
        lines.append(f"{category:<{width}}  {counts[category]:>5}  {fmt(totals[category]):>12}")
    lines.append("-" * (width + 21))
    lines.append(f"{'net booked':<{width}}  {sum(counts.values()):>5}  {fmt(sum(totals.values(), Decimal('0'))):>12}")
    if by_status["review"]:
        lines.append(
            f"\n{by_status['review']} row(s) in the review queue, {fmt(review_gross)} gross. "
            "Fill in the blank 'category' cells (Jev's top guesses are in 'suggested'), "
            "then run `ledgersort learn`."
        )
    return "\n".join(lines)
