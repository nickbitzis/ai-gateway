"""Redact PII from prompt text before it leaves the company network."""

from __future__ import annotations

import re
from collections import Counter

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
PHONE = re.compile(r"(?<![\w-])(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}\b")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact(text: str) -> tuple[str, Counter]:
    """Return (redacted_text, counts by PII type). Order matters: cards before phones."""
    counts: Counter = Counter()

    def sub(pattern: re.Pattern, label: str, s: str, check=None) -> str:
        def repl(m: re.Match) -> str:
            if check and not check(m.group(0)):
                return m.group(0)
            counts[label] += 1
            return f"[REDACTED_{label.upper()}]"

        return pattern.sub(repl, s)

    text = sub(EMAIL, "email", text)
    text = sub(SSN, "ssn", text)
    text = sub(CARD, "card", text, check=lambda s: luhn_ok(re.sub(r"\D", "", s)))
    text = sub(PHONE, "phone", text)
    return text, counts


def redact_content(content) -> tuple[object, Counter]:
    """Redact a Messages API content field: a string or a list of blocks."""
    if isinstance(content, str):
        return redact(content)
    total: Counter = Counter()
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            new_text, c = redact(block.get("text", ""))
            total += c
            block = {**block, "text": new_text}
        out.append(block)
    return out, total
