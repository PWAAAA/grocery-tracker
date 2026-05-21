"""Dimension-picking logic: query keywords, per-product, and cross-product vote."""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from .constants import QUERY_KEYWORDS, UNIT_DIMENSION
from .parsing import parse_pack_size_combined


def query_dimension_hint(query: str) -> Optional[str]:
    """Look up the query in the keyword map. Returns the matched dimension
    or None if no keyword matches.

    Multi-word keywords (e.g. 'toilet paper') are matched as substrings.
    Single-word keywords require a word boundary so 'oil' doesn't match 'toilet'.
    Longer keyword matches take priority over shorter ones.
    """
    q = (query or "").lower()
    best: tuple[int, str] | None = None  # (keyword length, dimension)
    for dim, keywords in QUERY_KEYWORDS.items():
        for kw in keywords:
            if " " in kw:
                if kw in q:
                    if best is None or len(kw) > best[0]:
                        best = (len(kw), dim)
            else:
                if re.search(rf"\b{re.escape(kw)}", q):
                    if best is None or len(kw) > best[0]:
                        best = (len(kw), dim)
    return best[1] if best else None


def product_dimension(
    name: Optional[str], size: Optional[str] = None
) -> Optional[str]:
    """Pick this product's dimension purely from its own name + size text.

    Physical-quantity units (volume, weight) take priority over count when
    both are present — a '12 pack 12 fl oz' is naturally a volume product
    that happens to come in a multipack, and comparing per fl oz beats
    comparing per can. Returns None when nothing parseable is found, so
    callers can fall back to the query-level hint.
    """
    pack = parse_pack_size_combined(name, size)
    if not pack:
        return None
    dims = {UNIT_DIMENSION.get(u) for _, u in pack}
    if "volume" in dims:
        return "volume"
    if "weight" in dims:
        return "weight"
    if "count" in dims:
        return "count"
    return None


def auto_dimension(products: list[dict]) -> Optional[str]:
    """Pick a dimension by majority vote of per-product dimensions."""
    votes: Counter[str] = Counter()
    for p in products:
        d = product_dimension(p.get("name"), p.get("size"))
        if d:
            votes[d] += 1
    if not votes:
        return None
    return votes.most_common(1)[0][0]


def pick_dimension(query: str, products: list[dict]) -> Optional[str]:
    """Resolve dimension in priority order:

    1. Explicit unit token typed in the query ("12 pack", "2 liter", "1 lb")
       — strongest signal, the user told us what they care about.
    2. Query keyword map ("milk" -> volume).
    3. Cross-product vote on parsed product text.
    """
    typed = product_dimension(query)
    if typed:
        return typed
    hint = query_dimension_hint(query)
    if hint:
        return hint
    return auto_dimension(products)
