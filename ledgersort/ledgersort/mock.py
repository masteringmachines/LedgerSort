"""
mock.py — a stand-in for Jev so you can try the full workflow offline.

NOT a model: a tiny keyword table answering in the same shape the real
API returns. Some merchants are answered confidently, a couple are
deliberately wishy-washy, and the rest are unknown, so you can watch
rules, auto-booking, the review queue, and `learn` all do their jobs.
Its answers say nothing about how real Jev would perform.
"""

from __future__ import annotations

# keyword in description -> (category id, probability on that category)
KEYWORDS = {
    "ADOBE": ("software", 0.93), "GITHUB": ("software", 0.95), "SLACK": ("software", 0.92),
    "DELTA": ("travel", 0.9), "UBER": ("travel", 0.88), "MARRIOTT": ("travel", 0.9),
    "STAPLES": ("office_supplies", 0.9), "SHELL": ("vehicle_fuel", 0.94),
    "COMCAST": ("internet_phone", 0.92), "BAKERY": ("meals", 0.86), "COFFEE": ("meals", 0.9),
    "STRIPE": ("income", 0.91), "ZELLE FROM": ("income", 0.8), "MONTHLY FEE": ("bank_fees", 0.95),
    "AMZN": ("office_supplies", 0.45),   # deliberately unsure: Amazon is all over the place
}


def mock_transport(payload: dict) -> dict:
    categories = list(next(iter(payload["questions"].values()))["criteria"])
    answers: dict[str, dict] = {}
    for i, tx in enumerate(payload["state"]["transactions"]):
        text = tx["description"].upper()
        hit = next((v for k, v in KEYWORDS.items() if k in text and v[0] in categories), None)
        if hit:
            top, p = hit
            rest = [c for c in categories if c != top]
            probs = {top: p, **{c: (1 - p) / len(rest) for c in rest}}
            confidence = p
        else:  # unknown merchant: no idea
            probs = {c: 1 / len(categories) for c in categories}
            top, confidence = categories[0], 1 / len(categories)
        answers[f"q{i}"] = {
            "type": "choice",
            "choice": top,
            "probabilities": {c: round(v, 4) for c, v in probs.items()},
            "confidence": round(confidence, 3),
        }
    return {"model": "mock (not Jev)", "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0}}
