"""Size/unit parsing from product names and store-native unit-price strings."""

from __future__ import annotations

import re
from typing import Optional


# Matches "12 ct", "1 gallon", "16 fl oz", "1.5 L", "5.3 oz", etc.
_NUM = r"(\d+(?:\.\d+)?)"
# Strips weight/volume ranges like "4.7-6.1 lb" or "2.75 - 4.0 lb" before
# the main regex runs, so variable-weight products don't get a bogus size.
_RANGE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*"
    r"(?:fl\.?\s*oz|floz|gallons?|gal|liters?|litres?|l|milliliters?|ml"
    r"|quarts?|qt|pints?|pt|pounds?|lbs?|kilograms?|kg|grams?|g|ounces?|oz)\b",
    re.IGNORECASE,
)
SIZE_RE = re.compile(
    rf"{_NUM}\s*-?\s*("
    r"fl\.?\s*oz|floz"
    r"|gallons?|gal"
    r"|liters?|litres?|l"
    r"|milliliters?|ml"
    r"|quarts?|qt"
    r"|pints?|pt"
    r"|pounds?|lbs?"
    r"|kilograms?|kg"
    r"|grams?|g"
    r"|ounces?|oz"
    r"|counts?|ct"
    r"|packs?|pk"
    r"|dozen|dz"
    r"|sheets?"
    r")\b",
    re.IGNORECASE,
)

# Map raw match -> normalized canonical unit
UNIT_ALIASES: dict[str, str] = {
    "fl oz": "fl oz", "fl. oz": "fl oz", "fl.oz": "fl oz", "floz": "fl oz",
    "gallon": "gallon", "gallons": "gallon", "gal": "gallon",
    "liter": "liter", "liters": "liter", "litre": "liter", "litres": "liter", "l": "liter",
    "milliliter": "ml", "milliliters": "ml", "ml": "ml",
    "quart": "quart", "quarts": "quart", "qt": "quart",
    "pint": "pint", "pints": "pint", "pt": "pint",
    "pound": "pound", "pounds": "pound", "lb": "pound", "lbs": "pound",
    "kilogram": "kilogram", "kilograms": "kilogram", "kg": "kilogram",
    "gram": "gram", "grams": "gram", "g": "gram",
    "ounce": "ounce", "ounces": "ounce", "oz": "ounce",
    "count": "count", "counts": "count", "ct": "count",
    "pack": "pack", "packs": "pack", "pk": "pack",
    "dozen": "dozen", "dz": "dozen",
    "sheet": "sheet", "sheets": "sheet",
}


def _normalize_unit(raw: str) -> Optional[str]:
    key = re.sub(r"\s+", " ", raw.lower().strip())
    return UNIT_ALIASES.get(key)


def parse_pack_size(name: Optional[str]) -> list[tuple[float, str]]:
    """Extract every (qty, normalized_unit) from a product name.

    A name like "Coca-Cola, 12 pack 12 fl oz Cans" returns
    [(12.0, "pack"), (12.0, "fl oz")] — the caller decides what to do
    with multiple units (multipack inference).

    Ranges like "4.7-6.1 lb" are stripped first — they indicate
    variable-weight products, not a fixed pack size.
    """
    if not name:
        return []
    # Remove ranges before matching so "4.7-6.1 lb" doesn't parse as "6.1 lb"
    cleaned = _RANGE_RE.sub("", name)
    out: list[tuple[float, str]] = []
    for qty_s, unit_raw in SIZE_RE.findall(cleaned):
        unit = _normalize_unit(unit_raw)
        if unit is None:
            continue
        try:
            qty = float(qty_s)
        except ValueError:
            continue
        out.append((qty, unit))
    return out


def parse_pack_size_combined(
    name: Optional[str], size: Optional[str] = None
) -> list[tuple[float, str]]:
    """Parse name + size separately, then dedupe identical (qty, unit) pairs.

    Avoids the double-count when a store puts the same size in both name
    and size fields (e.g. Aldi: name='Coca-Cola 20 fl oz', size='20 fl oz').
    """
    seen: set[tuple[float, str]] = set()
    pack: list[tuple[float, str]] = []
    for q, u in parse_pack_size(name) + parse_pack_size(size):
        if (q, u) not in seen:
            seen.add((q, u))
            pack.append((q, u))
    return pack


# ===== Native (store-supplied) unit-price parsing =====
# Walmart: "44.1 ¢/ea", "$0.21/fl oz", "$2.99/lb"
# Aldi:    "$0.02/fl oz", "$0.37/each"
NATIVE_UNIT_RE = re.compile(
    r"(?P<currency>\$)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<cents>¢)?\s*/\s*"
    r"(?P<denom>fl\.?\s*oz|floz|gallon|gal|liter|litre|l|ml|"
    r"pound|lbs?|kilogram|kg|gram|g|ounce|oz|each|ea|count|ct)\b",
    re.IGNORECASE,
)

DENOM_TO_UNIT: dict[str, str] = {
    **{k: "fl oz" for k in ("fl oz", "fl. oz", "fl.oz", "floz")},
    **{k: "gallon" for k in ("gallon", "gal")},
    **{k: "liter" for k in ("liter", "litre", "l")},
    "ml": "ml",
    **{k: "pound" for k in ("pound", "lb", "lbs")},
    **{k: "kilogram" for k in ("kilogram", "kg")},
    **{k: "gram" for k in ("gram", "g")},
    **{k: "ounce" for k in ("ounce", "oz")},
    **{k: "each" for k in ("each", "ea", "count", "ct")},
}


def parse_native_unit_price(s: Optional[str]) -> Optional[tuple[float, str]]:
    """Parse a store-native unit-price string.

    '44.1 ¢/ea' -> (0.441, 'each'); '$3.99/gal' -> (3.99, 'gallon');
    '$0.02/fl oz' -> (0.02, 'fl oz').
    """
    if not s:
        return None
    m = NATIVE_UNIT_RE.search(s)
    if not m:
        return None
    try:
        value = float(m.group("value"))
    except ValueError:
        return None
    if m.group("cents"):
        value /= 100.0
    denom_raw = re.sub(r"\s+", " ", m.group("denom").lower().strip())
    unit = DENOM_TO_UNIT.get(denom_raw)
    if unit is None:
        return None
    return value, unit


# ===== "Priced per unit" detection =====
# Some stores (Aldi) mark variable-weight items with size="per lb" instead
# of a concrete weight.  The listed price IS the per-unit rate, not a total.
_PRICED_PER_RE = re.compile(
    r"^(?:priced\s+)?per\s+"
    r"(fl\.?\s*oz|floz|gallon|gal|liter|litre|l|ml|"
    r"pound|lbs?|kilogram|kg|gram|g|ounce|oz|each|ea)\s*$",
    re.IGNORECASE,
)


_PER_100_RE = re.compile(
    r"(?P<currency>\$)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<cents>¢)?\s*/\s*100\s*(?:ct|count|sheets?)\b",
    re.IGNORECASE,
)


def parse_native_per_100(s: Optional[str]) -> Optional[float]:
    """Parse '$1.06/100 ct' native prices. Returns the dollar value per 100."""
    if not s:
        return None
    m = _PER_100_RE.search(s)
    if not m:
        return None
    try:
        value = float(m.group("value"))
    except ValueError:
        return None
    if m.group("cents"):
        value /= 100.0
    return value


def parse_priced_per_unit(size: Optional[str]) -> Optional[str]:
    """Detect 'per lb' / 'per oz' style size strings.

    Returns the normalized unit if the size indicates the price is already
    a per-unit rate, otherwise None.
    """
    if not size:
        return None
    m = _PRICED_PER_RE.match(size.strip())
    if not m:
        return None
    return _normalize_unit(m.group(1))
