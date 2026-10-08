"""
rules.py — your categories, and the rules that grow as you review.

Rules are checked BEFORE the model: a merchant you've already confirmed
costs no API call and can't be misread. Each review session makes the
next statement cheaper and more accurate.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field

OTHER = "other"


class ConfigError(ValueError):
    pass


def load_categories(path: str) -> dict[str, str]:
    """{category id: plain-English description}. An 'other' option is
    always present so the model has a way to say 'none of these'."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cats = data.get("categories") if isinstance(data, dict) else None
    if not isinstance(cats, dict) or not cats:
        raise ConfigError("categories file needs a non-empty 'categories' object")
    out = {str(k): str(v) for k, v in cats.items()}
    out.setdefault(OTHER, "None of the above, or unclear")
    return out


@dataclass
class Rules:
    rules: list[dict] = field(default_factory=list)  # {"match": KEY, "category": id}

    @classmethod
    def load(cls, path: str | None, categories: dict[str, str]) -> "Rules":
        if not path or not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rules = data.get("rules", []) if isinstance(data, dict) else []
        for r in rules:
            if "match" not in r or "category" not in r:
                raise ConfigError("every rule needs 'match' and 'category'")
            if r["category"] not in categories:
                raise ConfigError(f"rule '{r['match']}' uses unknown category '{r['category']}'")
        return cls([{"match": r["match"].upper(), "category": r["category"]} for r in rules])

    def match(self, key: str) -> str | None:
        """Category of the longest rule that appears in the merchant key as
        whole words, so the rule 'CHECK' can't capture 'CHECKERS' or 'PAYCHECK'."""
        padded = f" {key} "
        hits = [r for r in self.rules if f" {r['match']} " in padded]
        return max(hits, key=lambda r: len(r["match"]))["category"] if hits else None

    def add(self, key: str, category: str) -> None:
        self.rules.append({"match": key.upper(), "category": category})

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"rules": sorted(self.rules, key=lambda r: r["match"])}, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)


@dataclass
class LearnReport:
    added: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: dict[str, set[str]] = field(default_factory=dict)
    unknown_category: list[tuple[str, str]] = field(default_factory=list)
    already_covered: int = 0


def learn(reviewed: list[tuple[str, str]], rules: Rules, categories: dict[str, str]) -> LearnReport:
    """reviewed: (merchant_key, category) pairs a human has confirmed.

    A merchant only becomes a rule if every confirmation agrees. One that
    got two different categories (an Amazon order that was supplies once
    and equipment another) is reported and left to the model and to you,
    rather than frozen into a rule that would be wrong half the time."""
    report = LearnReport()
    seen: dict[str, set[str]] = {}
    for key, category in reviewed:
        if category not in categories:
            report.unknown_category.append((key, category))
            continue
        seen.setdefault(key, set()).add(category)
    for key, cats in sorted(seen.items()):
        if len(cats) > 1:
            report.ambiguous[key] = cats
        elif rules.match(key) is not None:
            report.already_covered += 1
        else:
            (category,) = cats
            rules.add(key, category)
            report.added.append((key, category))
    return report
