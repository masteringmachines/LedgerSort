"""
cli.py

    ledgersort categorize statement.csv --categories categories.json --rules rules.json
    # open statement.categorized.csv, fill in the blank categories, save
    ledgersort learn statement.categorized.csv --categories categories.json --rules rules.json
"""

from __future__ import annotations
import argparse
import json
import os
import sys

from .cache import Cache
from .classify import build_payload, classify
from .client import DEFAULT_MODEL, JevError, transport_from_env
from .csvio import read_reviewed, read_statement, write_results
from .merchants import merchant_key
from .mock import mock_transport
from .report import summary
from .rules import ConfigError, Rules, learn, load_categories

LEARNABLE = {"review", "corrected"}


def cmd_categorize(args) -> int:
    categories = load_categories(args.categories)
    rules = Rules.load(args.rules, categories)
    rows = read_statement(args.statement, args.desc_col)
    if not rows:
        print("ledgersort: the statement has no rows", file=sys.stderr)
        return 2

    if args.dry_run:
        pending = [r for r in rows if rules.match(merchant_key(r.description)) is None]
        print(f"# {len(rows) - len(pending)} of {len(rows)} rows are covered by rules; "
              f"the first request would cover up to {args.batch_size} merchant groups.", file=sys.stderr)
        seen, firsts = set(), []
        for r in pending:
            k = (merchant_key(r.description), r.amount >= 0)
            if k not in seen:
                seen.add(k)
                firsts.append(r)
        print(json.dumps(build_payload(firsts[:args.batch_size], categories, args.model, not args.no_redact), indent=2))
        return 0

    transport = mock_transport if args.mock else transport_from_env()
    cache = Cache(None if (args.no_cache or args.mock) else args.cache)
    results, stats = classify(
        rows, categories, rules, transport, cache, args.model,
        min_confidence=args.min_confidence, batch_size=args.batch_size, workers=args.workers,
        redact_text=not args.no_redact, dedupe=not args.per_row,
    )
    cache.save()

    out = args.out or os.path.splitext(args.statement)[0] + ".categorized.csv"
    write_results(out, results)
    print(summary(results, stats, mock=args.mock))
    print(f"\nWrote {out}")
    return 0


def cmd_learn(args) -> int:
    categories = load_categories(args.categories)
    rules = Rules.load(args.rules, categories)
    reviewed, unfilled = [], 0
    for rec in read_reviewed(args.reviewed):
        status = (rec.get("status") or "").strip().lower()
        category = (rec.get("category") or "").strip()
        if status not in LEARNABLE:
            continue
        if not category:
            unfilled += 1
            continue
        key = (rec.get("merchant_key") or "").strip() or merchant_key(rec.get("description") or "")
        reviewed.append((key, category))

    report = learn(reviewed, rules, categories)
    rules.save(args.rules)

    print(f"Added {len(report.added)} rule(s); {report.already_covered} merchant(s) were already covered.")
    for key, cat in report.added:
        print(f"  + {key} -> {cat}")
    for key, cats in report.ambiguous.items():
        print(f"  ! {key}: confirmed as {', '.join(sorted(cats))}; not made a rule", file=sys.stderr)
    for key, cat in report.unknown_category:
        print(f"  ! {key}: '{cat}' is not in your categories file (typo?); skipped", file=sys.stderr)
    if unfilled:
        print(f"{unfilled} review row(s) still have a blank category and were skipped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ledgersort", description="Categorize a bank statement; learn from your corrections.")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("categorize", help="categorize a statement CSV")
    c.add_argument("statement")
    c.add_argument("--categories", required=True, help="categories JSON")
    c.add_argument("--rules", help="rules JSON (read if it exists)")
    c.add_argument("--out", help="output CSV (default: <statement>.categorized.csv)")
    c.add_argument("--desc-col", help="description column name if it isn't auto-detected")
    c.add_argument("--model", default=DEFAULT_MODEL, help="pin a version (e.g. jev-1.13.0) for repeatable runs")
    c.add_argument("--min-confidence", type=float, default=0.8, help="auto-book at or above this confidence (default 0.8)")
    c.add_argument("--batch-size", type=int, default=20, help="merchant groups per request (default 20)")
    c.add_argument("--workers", type=int, default=3)
    c.add_argument("--no-redact", action="store_true", help="send descriptions as-is (default hides emails and 5+ digit runs)")
    c.add_argument("--per-row", action="store_true", help="ask about every row instead of once per merchant")
    c.add_argument("--cache", default=".ledgersort-cache.json")
    c.add_argument("--no-cache", action="store_true")
    c.add_argument("--mock", action="store_true", help="keyword stand-in for Jev; no key, no network")
    c.add_argument("--dry-run", action="store_true", help="print the first request and exit; sends nothing")
    c.set_defaults(func=cmd_categorize)

    l = sub.add_parser("learn", help="turn your reviewed rows into rules")
    l.add_argument("reviewed", help="the categorized CSV after you filled in the blanks")
    l.add_argument("--categories", required=True)
    l.add_argument("--rules", required=True, help="rules JSON to create or extend")
    l.set_defaults(func=cmd_learn)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, JevError, ValueError, OSError, json.JSONDecodeError, KeyError) as err:
        print(f"ledgersort: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
