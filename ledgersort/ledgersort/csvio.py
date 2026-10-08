"""
csvio.py — read bank-export CSVs (every bank names its columns differently)
and write the categorized result.
"""

from __future__ import annotations
import csv
from dataclasses import dataclass
from decimal import Decimal

from .money import parse_amount

DATE_NAMES = ("date", "posted", "posting date", "transaction date", "post date")
DESC_NAMES = ("description", "memo", "payee", "details", "name", "merchant")
AMOUNT_NAMES = ("amount", "amt", "transaction amount")
DEBIT_NAMES = ("debit", "withdrawal", "withdrawals", "money out")
CREDIT_NAMES = ("credit", "deposit", "deposits", "money in")

OUT_FIELDS = ["date", "description", "amount", "category", "status", "confidence", "suggested", "merchant_key"]


@dataclass
class Row:
    line: int
    date: str
    description: str
    amount_text: str
    amount: Decimal


def _find(headers: dict[str, str], names: tuple[str, ...]) -> str | None:
    for n in names:
        if n in headers:
            return headers[n]
    return None


def read_statement(path: str, desc_col: str | None = None) -> list[Row]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("statement CSV has no header row")
        headers = {h.strip().lower(): h for h in reader.fieldnames}
        desc = headers.get(desc_col.lower()) if desc_col else _find(headers, DESC_NAMES)
        date = _find(headers, DATE_NAMES)
        amount = _find(headers, AMOUNT_NAMES)
        debit, credit = _find(headers, DEBIT_NAMES), _find(headers, CREDIT_NAMES)
        if desc is None:
            raise ValueError(f"no description column found (tried {', '.join(DESC_NAMES)}; use --desc-col)")
        if amount is None and not (debit and credit):
            raise ValueError("no amount column found (need 'amount', or both debit and credit columns)")

        rows: list[Row] = []
        for n, rec in enumerate(reader, start=2):  # line 1 is the header
            if not any((v or "").strip() for v in rec.values()):
                continue
            try:
                if amount is not None:
                    text = rec[amount]
                    value = parse_amount(text)
                else:  # separate columns: money in minus money out
                    value = parse_amount(rec[credit]) - parse_amount(rec[debit])
                    text = str(value)
            except ValueError as err:
                raise ValueError(f"line {n}: {err}") from None
            rows.append(Row(n, (rec.get(date) or "") if date else "", (rec[desc] or "").strip(), text or "", value))
        return rows


def write_results(path: str, results) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for r in results:
            w.writerow({
                "date": r.row.date,
                "description": r.row.description,
                "amount": r.row.amount_text if r.row.amount_text else str(r.row.amount),
                "category": r.category,
                "status": r.status,
                "confidence": "" if r.confidence is None else f"{r.confidence:.2f}",
                "suggested": r.suggested,
                "merchant_key": r.key,
            })


def read_reviewed(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
