"""
unit_price.py — Standardize unit prices across grocery stores.

Store-supplied unit prices are inconsistent: Walmart prices eggs per ounce,
soda multipacks per can while 2-liters get priced per liter, and different
stores pick different denominators for the same product. The result is unit
prices that aren't comparable across forms — or across stores.

This module picks one canonical unit per *dimension* (volume / weight /
count), then computes price ÷ size in that unit. After standardization,
a Walmart 12-pack and an Aldi 2-liter both show in ¢/fl oz, so they can
actually be compared.

Adding a new store: have its scraper return a list of dicts with at least
`name` and `price`. Optionally include `size` (used for stores like Aldi
that put size separate from the name) and `unit_price_string` (the store's
native unit price — used as a last-resort fallback when our parser can't
read the name).

Approach (in priority order):
  1. Parse one or more (qty, unit) pairs from `name + size`.
  2. Pick the comparison dimension for the *query* using:
       a) keyword map (drinks → volume, eggs → count, etc.)
       b) cross-result vote: whichever dimension most parsed sizes fall in
       c) the single unit on the product itself, if that's all we have
  3. Compute the canonical unit price; fall back to the store's native
     unit price when we can't parse the name.

Public API:
    standardize_results(query, products) -> dict
        Mutates each product dict in-place. Adds:
          - std_units         dict of {"per_fl_oz": {"value": float, "string": str}, ...}
                              Contains every rep we can compute for this product
                              (per_ea, per_dozen, per_fl_oz, per_gal, per_oz, per_lb).

        Returns query-wide metadata:
          - unit_default      key name to show by default ("per_fl_oz" etc.)
          - unit_options      ordered list of keys available across the result set
          - container_label   detected per-item label ("can", "bottle", "egg", ...)

The functions below are also exposed for testing.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional


# ===== Conversion tables =====
# Volume → fluid ounces (canonical for the volume dimension)
VOLUME_TO_FLOZ: dict[str, float] = {
    "fl oz": 1.0,
    "gallon": 128.0,
    "quart": 32.0,
    "pint": 16.0,
    "liter": 33.814,
    "ml": 0.033814,
}

# Weight → ounces (canonical for the weight dimension)
WEIGHT_TO_OZ: dict[str, float] = {
    "ounce": 1.0,
    "pound": 16.0,
    "kilogram": 35.274,
    "gram": 0.035274,
}

# Count units (canonical: each)
COUNT_UNITS: set[str] = {"count", "pack", "dozen"}
DOZEN_MULTIPLIER = {"dozen": 12}

# Reverse lookup: normalized unit → dimension
UNIT_DIMENSION: dict[str, str] = {}
for u in VOLUME_TO_FLOZ:
    UNIT_DIMENSION[u] = "volume"
for u in WEIGHT_TO_OZ:
    UNIT_DIMENSION[u] = "weight"
for u in COUNT_UNITS:
    UNIT_DIMENSION[u] = "count"


# ===== Query → preferred dimension =====
# Add more as we encounter them. Keyword match is substring, lowercased.
QUERY_KEYWORDS: dict[str, list[str]] = {
    "volume": [
        "milk", "juice", "soda", "pop", "cola", "coke", "pepsi", "sprite",
        "water", "beer", "wine", "tea", "lemonade", "kombucha", "broth",
        "stock", "syrup", "oil", "vinegar", "sauce", "cream", "smoothie",
        "gatorade", "powerade", "energy drink", "sports drink",
    ],
    "count": [
        "egg", "cookie", "bagel", "muffin", "donut", "doughnut", "tortilla",
        "burrito", "popsicle", "k-cup", "k cup", "pod", "tampon", "diaper",
        "paper towel", "toilet paper", "napkin", "trash bag", "battery",
        "lightbulb", "bulb", "candy bar", "ice cream bar",
    ],
    "weight": [
        "chicken", "beef", "pork", "bacon", "ham", "turkey", "lamb", "fish",
        "salmon", "tuna", "shrimp", "steak", "sausage", "cheese", "butter",
        "yogurt", "rice", "pasta", "flour", "sugar", "cereal", "oats",
        "oatmeal", "nuts", "peanut", "almond", "walnut", "cashew", "raisin",
        "trail mix", "granola", "chips", "cracker", "pretzel", "popcorn",
        "candy", "chocolate", "apple", "banana", "orange", "grape", "berry",
        "strawberr", "blueberr", "tomato", "potato", "onion", "lettuce",
        "spinach", "broccoli", "carrot", "celery", "cucumber", "pepper",
        "garlic", "ginger", "mushroom", "avocado",
    ],
}


# ===== Name parsing =====
# Matches "12 ct", "1 gallon", "16 fl oz", "1.5 L", "5.3 oz", etc.
# Word-boundary `\b` after the unit prevents matching "g" inside "great" or
# "l" inside "large".
_NUM = r"(\d+(?:\.\d+)?)"
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
    r")\b",
    re.IGNORECASE,
)

# Map raw match → normalized canonical unit
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
}


def _normalize_unit(raw: str) -> Optional[str]:
    key = re.sub(r"\s+", " ", raw.lower().strip())
    return UNIT_ALIASES.get(key)


def parse_pack_size(name: Optional[str]) -> list[tuple[float, str]]:
    """Extract every (qty, normalized_unit) from a product name.

    A name like "Coca-Cola, 12 pack 12 fl oz Cans" returns
    [(12.0, "pack"), (12.0, "fl oz")] — the caller decides what to do
    with multiple units (multipack inference).
    """
    if not name:
        return []
    out: list[tuple[float, str]] = []
    for qty_s, unit_raw in SIZE_RE.findall(name):
        unit = _normalize_unit(unit_raw)
        if unit is None:
            continue
        try:
            qty = float(qty_s)
        except ValueError:
            continue
        out.append((qty, unit))
    return out


# ===== Native (store-supplied) unit-price parsing =====
# Walmart: "44.1 ¢/ea", "$0.21/fl oz", "$2.99/lb"
# Aldi:    "$0.02/fl oz", "$0.37/each"
# Same shape — single regex covers both.
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

    '44.1 ¢/ea' → (0.441, 'each'); '$3.99/gal' → (3.99, 'gallon');
    '$0.02/fl oz' → (0.02, 'fl oz').
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


# ===== Dimension picking =====

def query_dimension_hint(query: str) -> Optional[str]:
    """Look up the query in the keyword map. Returns the matched dimension
    or None if no keyword matches."""
    q = (query or "").lower()
    for dim, keywords in QUERY_KEYWORDS.items():
        for kw in keywords:
            if kw in q:
                return dim
    return None


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
    2. Query keyword map ("milk" → volume).
    3. Cross-product vote on parsed product text.
    """
    typed = product_dimension(query)
    if typed:
        return typed
    hint = query_dimension_hint(query)
    if hint:
        return hint
    return auto_dimension(products)


# ===== Container detection =====
# Per-query: scan product names for the word that names the unit ("can",
# "bottle", "egg"...) so the per-item rep can be labeled correctly.

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


# ===== Per-product unit reps =====

def total_count(pack: list[tuple[float, str]]) -> Optional[float]:
    """Total individual-item count from a parsed pack. None if no count present.
    Multiplies count terms ('12 Pack, Pack of 2' → 24)."""
    qty = 1.0
    has = False
    for q, u in pack:
        if u == "dozen":
            qty *= q * 12
            has = True
        elif u in ("count", "pack"):
            qty *= q
            has = True
    return qty if has else None


def _fmt_money_per(value: float, suffix: str) -> str:
    """'$0.04' → '4.0¢/fl oz'; '$1.50' → '$1.50/fl oz'."""
    if value < 1:
        return f"{value * 100:.1f}¢/{suffix}"
    return f"${value:.2f}/{suffix}"


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


def compute_unit_reps(
    price: Optional[float],
    name: Optional[str],
    container_label: str = "ea",
    dimension: Optional[str] = None,
    native_unit_price: Optional[str] = None,
    size: Optional[str] = None,
) -> dict[str, dict]:
    """All unit-price representations we can compute for one product.

    Keys present depend on what's available from the name/size text and the
    store's native unit-price string:

      per_ea       — when a count is parseable (or native says /each, count-dim)
      per_dozen    — only when dimension is "count"
      per_fl_oz,   — volume reps. Prefers the store's native unit price when
      per_gal        its denominator is a volume (Aldi-style multipacks where
                     pack count isn't in the name still resolve correctly).
      per_oz,      — weight reps. Same preference logic.
      per_lb
    """
    if price is None or price <= 0:
        return {}

    pack = parse_pack_size_combined(name, size)
    native = parse_native_unit_price(native_unit_price)

    # Per-product dimension takes precedence over the query-wide hint for
    # the disambiguation rules below: what THIS product is determines how
    # to read its own "oz", regardless of what the broader query is about.
    prod_dim = product_dimension(name, size) or dimension

    # Disambiguate bare "oz": when this product is a volume product (sodas,
    # juice, etc.) "7.5 oz" should be read as fluid ounces, not weight ounces.
    # The size parser maps "oz" → "ounce" (weight) by default, so we rewrite
    # those occurrences here once we know the chosen dimension.
    if prod_dim == "volume":
        pack = [(q, "fl oz") if u == "ounce" else (q, u) for q, u in pack]
        if native and native[1] == "ounce":
            native = (native[0], "fl oz")

    out: dict[str, dict] = {}

    # --- per-each / per-dozen (only count is parseable per-item) ---
    count = total_count(pack)
    if count and count > 0:
        per_ea = price / count
        out["per_ea"] = {"value": per_ea, "string": f"${per_ea:.2f}/{container_label}"}
        if prod_dim == "count":
            per_dz = per_ea * 12
            out["per_dozen"] = {"value": per_dz, "string": f"${per_dz:.2f}/dozen"}
    elif prod_dim == "count" and native and native[1] == "each":
        # No count in the name but the store says "/each" — trust it.
        per_ea = native[0]
        out["per_ea"] = {"value": per_ea, "string": f"${per_ea:.2f}/{container_label}"}
        out["per_dozen"] = {"value": per_ea * 12, "string": f"${per_ea * 12:.2f}/dozen"}

    # --- volume ---
    per_floz: Optional[float] = None
    if native and native[1] in VOLUME_TO_FLOZ:
        # Native is in volume — store's official calc, prefer it over our parse.
        per_floz = native[0] / VOLUME_TO_FLOZ[native[1]]
    else:
        vol = total_in_dimension(pack, "volume")
        if vol and vol > 0:
            per_floz = price / vol
    if per_floz:
        out["per_fl_oz"] = {"value": per_floz, "string": _fmt_money_per(per_floz, "fl oz")}
        per_gal = per_floz * 128
        out["per_gal"] = {"value": per_gal, "string": f"${per_gal:.2f}/gal"}

    # --- weight ---
    per_oz: Optional[float] = None
    if native and native[1] in WEIGHT_TO_OZ and prod_dim == "weight":
        # Only trust native /oz or /lb when this query *is* weight.
        # A /oz on a soda product (volume dim) would be wrong — discard it.
        per_oz = native[0] / WEIGHT_TO_OZ[native[1]]
    else:
        wt = total_in_dimension(pack, "weight")
        if wt and wt > 0:
            per_oz = price / wt
    if per_oz:
        out["per_oz"] = {"value": per_oz, "string": _fmt_money_per(per_oz, "oz")}
        per_lb = per_oz * 16
        out["per_lb"] = {"value": per_lb, "string": f"${per_lb:.2f}/lb"}

    return out


# ===== Standardization =====

def total_in_dimension(pack: list[tuple[float, str]], dimension: str) -> Optional[float]:
    """Sum a parsed pack into the canonical unit of the requested dimension.

    Multipack handling: if a product has BOTH a count and a volume/weight,
    we multiply (e.g., '12 pack 12 fl oz' → 12 * 12 = 144 fl oz). This is
    the standard "X pack of Y" convention in Walmart names.
    """
    count_qty = 1.0
    has_count = False
    canon_total = 0.0

    for qty, unit in pack:
        dim = UNIT_DIMENSION.get(unit)
        if dim == "count":
            multiplier = DOZEN_MULTIPLIER.get(unit, 1)
            count_qty *= qty * multiplier
            has_count = True
        elif dimension == "volume" and unit in VOLUME_TO_FLOZ:
            canon_total += qty * VOLUME_TO_FLOZ[unit]
        elif dimension == "weight" and unit in WEIGHT_TO_OZ:
            canon_total += qty * WEIGHT_TO_OZ[unit]

    if dimension == "count":
        return count_qty if has_count else None

    if canon_total == 0:
        return None
    return canon_total * count_qty if has_count else canon_total


# Default rep per dimension and the order options should appear in the toggle.
DIMENSION_DEFAULT: dict[str, str] = {
    "volume": "per_fl_oz",
    "weight": "per_lb",
    "count": "per_ea",
}
UNIT_OPTION_ORDER: list[str] = [
    "per_ea", "per_dozen", "per_fl_oz", "per_gal", "per_oz", "per_lb",
]


def standardize_results(query: str, products: list[dict]) -> dict:
    """Mutate products in-place; return query-wide unit metadata.

    Each product gets:
      std_units — dict of every computable rep (see compute_unit_reps).

    Returns:
      {
        "unit_default":    str | None,   # e.g. "per_fl_oz"
        "unit_options":    list[str],    # ordered, only keys present in results
        "container_label": str,          # e.g. "can"
        "dimension":       str | None,
      }
    """
    if not products:
        return {"unit_default": None, "unit_options": [], "container_label": "ea", "dimension": None}

    dimension = pick_dimension(query, products)
    container_label = detect_container_label(products)

    available: set[str] = set()
    for p in products:
        reps = compute_unit_reps(
            price=p.get("price"),
            name=p.get("name"),
            size=p.get("size"),
            native_unit_price=p.get("unit_price_string"),
            container_label=container_label,
            dimension=dimension,
        )
        p["std_units"] = reps
        available.update(reps.keys())

    default = DIMENSION_DEFAULT.get(dimension or "")
    if default not in available:
        default = next((k for k in UNIT_OPTION_ORDER if k in available), None)
    options = [k for k in UNIT_OPTION_ORDER if k in available]

    return {
        "unit_default": default,
        "unit_options": options,
        "container_label": container_label,
        "dimension": dimension,
    }
