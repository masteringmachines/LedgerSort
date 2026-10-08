"""
classify.py — rules first, then Jev, then a confidence gate.

For each statement row:
  1. A rule you've confirmed before matches the merchant  -> booked, no API call.
  2. Otherwise the row is grouped with others from the same merchant
     (same direction of money), and ONE Jev Choice question is asked per
     group. Questions are batched into requests over a shared state.
  3. Jev's answer is booked only if it names a real category AND its
     confidence clears --min-confidence. Anything else lands in the
     review queue with Jev's top two guesses attached, category blank.

Leaving the category blank on review rows is deliberate: a row nobody
looked at must never be mistaken for a decision.
"""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .cache import Cache
from .client import JevError, Transport
from .csvio import Row
from .merchants import merchant_key, redact
from .rules import OTHER, Rules

INSTRUCTION = (
    "Which bookkeeping category best fits the bank transaction at `transactions[{i}]`? "
    "It comes from a freelancer's or small business's account. A negative amount is money "
    "spent; a positive amount is money received."
)


@dataclass
class Result:
    row: Row
    key: str
    category: str = ""
    status: str = "review"      # rule | auto | review
    confidence: float | None = None
    suggested: str = ""


@dataclass
class Stats:
    rows: int = 0
    rule_hits: int = 0
    groups: int = 0
    calls: int = 0
    cache_hits: int = 0


def build_payload(group_rows: list[Row], categories: dict[str, str], model: str, redact_text: bool) -> dict:
    transactions = [
        {"id": i, "description": redact(r.description) if redact_text else r.description, "amount": str(r.amount)}
        for i, r in enumerate(group_rows)
    ]
    questions = {
        f"q{i}": {"type": "choice", "instructions": INSTRUCTION.format(i=i), "criteria": categories}
        for i in range(len(group_rows))
    }
    return {"state": {"transactions": transactions}, "model": model, "questions": questions}


def decide(answer: dict | None, categories: dict[str, str], min_confidence: float) -> tuple[str, str, float | None, str]:
    """(category, status, confidence, suggested) from one Choice answer."""
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return "", "review", None, "no usable answer"
    probs = {c: float(p) for c, p in (answer.get("probabilities") or {}).items() if c in categories}
    choice = answer.get("choice")
    confidence = answer.get("confidence")
    confidence = float(confidence) if confidence is not None else probs.get(choice, 0.0)
    top2 = sorted(((c, p) for c, p in probs.items() if c != OTHER), key=lambda cp: -cp[1])[:2]
    suggested = "; ".join(f"{c} ({p:.2f})" for c, p in top2)
    if choice in categories and choice != OTHER and confidence >= min_confidence:
        return choice, "auto", confidence, suggested
    return "", "review", confidence, suggested


def classify(
    rows: list[Row],
    categories: dict[str, str],
    rules: Rules,
    transport: Transport,
    cache: Cache,
    model: str,
    min_confidence: float = 0.8,
    batch_size: int = 20,
    workers: int = 3,
    redact_text: bool = True,
    dedupe: bool = True,
) -> tuple[list[Result], Stats]:
    stats = Stats(rows=len(rows))
    results = [Result(row=r, key=merchant_key(r.description)) for r in rows]

    # 1. Rules you've confirmed before.
    pending: list[Result] = []
    for res in results:
        category = rules.match(res.key)
        if category is not None:
            res.category, res.status, res.confidence = category, "rule", 1.0
            stats.rule_hits += 1
        else:
            pending.append(res)

    # 2. One question per merchant-and-direction (or per row with dedupe off).
    groups: dict[object, list[Result]] = {}
    for i, res in enumerate(pending):
        gkey = (res.key, res.row.amount >= 0) if dedupe else i
        groups.setdefault(gkey, []).append(res)
    group_list = list(groups.values())
    stats.groups = len(group_list)

    chunks = [group_list[i:i + batch_size] for i in range(0, len(group_list), batch_size)]

    def run_chunk(chunk: list[list[Result]]) -> tuple[dict, bool]:
        payload = build_payload([g[0].row for g in chunk], categories, model, redact_text)
        key = Cache.key(payload)
        response = cache.get(key)
        called = False
        if response is None:
            response = transport(payload)
            cache.put(key, response)
            called = True
        answers = response.get("answers") if isinstance(response, dict) else None
        if not isinstance(answers, dict):
            raise JevError("response has no 'answers' object; the API may have changed shape")
        return answers, called

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        outcomes = list(pool.map(run_chunk, chunks))

    # 3. Gate each answer; every row in a group shares its group's decision.
    for chunk, (answers, called) in zip(chunks, outcomes):
        stats.calls += 1 if called else 0
        stats.cache_hits += 0 if called else 1
        for i, group in enumerate(chunk):
            category, status, conf, suggested = decide(answers.get(f"q{i}"), categories, min_confidence)
            for res in group:
                res.category, res.status, res.confidence, res.suggested = category, status, conf, suggested
    return results, stats
