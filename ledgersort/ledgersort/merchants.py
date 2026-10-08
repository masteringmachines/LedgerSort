"""
merchants.py — turn messy bank descriptions into stable merchant keys,
and scrub sensitive digits before anything leaves the machine.

    'SQ *CORNER BAKERY 0042 SEATTLE WA'  ->  'SQ CORNER BAKERY'
    'AMZN Mktp US*2K4TT0'                 ->  'AMZN MKTP US'
    'DELTA AIR 0062345'                   ->  'DELTA AIR'

The key does double duty: rules match against it, and identical keys are
asked about only once per statement.
"""

from __future__ import annotations
import re

_NOISE = {"POS", "DEBIT", "PURCHASE", "CHECKCARD", "CARD", "RECURRING", "ONLINE"}
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_LONG_DIGITS = re.compile(r"\d{5,}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def merchant_key(description: str) -> str:
    tokens = _NON_ALNUM.sub(" ", description.upper()).split()
    # Tokens containing digits are store numbers, reference codes, dates.
    kept = [t for t in tokens if not any(ch.isdigit() for ch in t) and t not in _NOISE]
    if not kept:
        return " ".join(tokens) or description.strip().upper()
    return " ".join(kept[:3])


def redact(description: str) -> str:
    """Hide email addresses and any run of 5+ digits (account, card and
    reference numbers) before text is sent to a hosted model."""
    return _LONG_DIGITS.sub("#####", _EMAIL.sub("<email>", description))
