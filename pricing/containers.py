"""Container label detection from product names."""

from __future__ import annotations

import re
from collections import Counter


CONTAINER_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("can", re.compile(r"\bcans?\b", re.IGNORECASE)),
    ("bottle", re.compile(r"\bbottles?\b", re.IGNORECASE)),
    ("jar", re.compile(r"\bjars?\b", re.IGNORECASE)),
    ("box", re.compile(r"\bboxes?\b", re.IGNORECASE)),
    ("carton", re.compile(r"\bcartons?\b", re.IGNORECASE)),
    ("jug", re.compile(r"\bjugs?\b", re.IGNORECASE)),
    ("tub", re.compile(r"\btubs?\b", re.IGNORECASE)),
    ("cup", re.compile(r"\bcups?\b", re.IGNORECASE)),
    ("bar", re.compile(r"\bbars?\b", re.IGNORECASE)),
    ("egg", re.compile(r"\beggs?\b", re.IGNORECASE)),
    ("pod", re.compile(r"\bpods?\b", re.IGNORECASE)),
    ("pouch", re.compile(r"\bpouches?\b", re.IGNORECASE)),
    ("stick", re.compile(r"\bsticks?\b", re.IGNORECASE)),
    ("tablet", re.compile(r"\btablets?\b", re.IGNORECASE)),
    ("bag", re.compile(r"\bbags?\b", re.IGNORECASE)),
]


def detect_container_label(products: list[dict]) -> str:
    """Pick the most common container word across product names. 'ea' fallback."""
    counter: Counter[str] = Counter()
    for p in products:
        name = p.get("name") or ""
        for label, pat in CONTAINER_PATTERNS:
            if pat.search(name):
                counter[label] += 1
                break
    return counter.most_common(1)[0][0] if counter else "ea"
