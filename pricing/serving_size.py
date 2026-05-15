"""Serving size parser — extracts density (oz per cup) from nutrition label strings.

Nutrition labels typically say something like "Serving Size: 1/4 cup (45g)".
This module parses that into an oz-per-cup density value that can replace
the hardcoded DENSITY_OZ_PER_CUP table in cooking.py.

Priority chain for density:
    1. Parsed from product serving size label (this module)
    2. Hardcoded DENSITY_OZ_PER_CUP table (cooking.py fallback)
    3. Future: USDA FoodData Central API
"""

import re
from typing import Optional

# Grams to ounces conversion factor
GRAMS_TO_OZ = 0.035274

# Volume unit to cups conversion
_VOLUME_TO_CUPS = {
    "cup": 1.0,
    "cups": 1.0,
    "tbsp": 1 / 16,
    "tablespoon": 1 / 16,
    "tablespoons": 1 / 16,
    "tsp": 1 / 48,
    "teaspoon": 1 / 48,
    "teaspoons": 1 / 48,
}


def _parse_fraction(s: str) -> Optional[float]:
    """Parse a string that may contain a fraction or mixed number.

    Examples:
        "1/4"   -> 0.25
        "1 1/2" -> 1.5
        "2"     -> 2.0
        "0.5"   -> 0.5
    """
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    # Mixed number: "1 1/2"
    mixed = re.match(r'^(\d+)\s+(\d+)/(\d+)$', s)
    if mixed:
        whole = int(mixed.group(1))
        num = int(mixed.group(2))
        den = int(mixed.group(3))
        if den == 0:
            return None
        return whole + num / den
    # Simple fraction: "1/4"
    frac = re.match(r'^(\d+)/(\d+)$', s)
    if frac:
        num = int(frac.group(1))
        den = int(frac.group(2))
        if den == 0:
            return None
        return num / den
    return None


def parse_serving_size_density(serving_size_str: str) -> Optional[float]:
    """Parse a serving size string into oz_per_cup density.

    Looks for a volume component (cup, tbsp, tsp) and a weight component
    (grams or oz in parentheses), then computes weight_oz / volume_cups.

    Returns None if either component is missing (e.g., liquid serving sizes
    that only show ml, or strings with no volume reference).

    Examples:
        "1/4 cup (45g)"         -> ~6.35
        "2 tbsp (30g)"          -> ~16.93 oz/cup
        "About 1/3 cup (56g)"   -> ~5.93
        "1 cup (240ml)"         -> None  (no weight)
        "28g"                   -> None  (no volume)
        "1/2 cup (70 g)"        -> ~4.94
        "3 tbsp (36g)"          -> ~6.78
    """
    if not serving_size_str:
        return None

    text = serving_size_str.strip()

    # Extract volume portion: optional number + fraction + volume unit
    # Handles: "1/4 cup", "2 tbsp", "1 1/2 cups", "About 1/3 cup"
    vol_pattern = re.compile(
        r'(?:about\s+)?'                          # optional "About"
        r'((?:\d+\s+)?\d+(?:/\d+)?(?:\.\d+)?)'   # number (mixed, fraction, or decimal)
        r'\s*'
        r'(cups?|tbsp|tablespoons?|tsp|teaspoons?)',  # volume unit
        re.IGNORECASE,
    )
    vol_match = vol_pattern.search(text)
    if not vol_match:
        return None

    vol_amount = _parse_fraction(vol_match.group(1))
    vol_unit = vol_match.group(2).lower()
    if vol_amount is None or vol_amount <= 0:
        return None

    cups_factor = _VOLUME_TO_CUPS.get(vol_unit)
    if cups_factor is None:
        return None
    volume_cups = vol_amount * cups_factor

    # Extract weight portion in parentheses: "(45g)" or "(1.5 oz)" or "(70 g)"
    weight_pattern = re.compile(
        r'\(\s*(\d+\.?\d*)\s*(g|oz|grams?|ounces?)\s*\)',
        re.IGNORECASE,
    )
    weight_match = weight_pattern.search(text)
    if not weight_match:
        return None

    weight_amount = float(weight_match.group(1))
    weight_unit = weight_match.group(2).lower()

    if weight_amount <= 0:
        return None

    # Convert weight to oz
    if weight_unit.startswith('g'):
        weight_oz = weight_amount * GRAMS_TO_OZ
    elif weight_unit.startswith('o'):
        weight_oz = weight_amount
    else:
        return None

    # Compute oz per cup
    if volume_cups <= 0:
        return None

    density = weight_oz / volume_cups
    return round(density, 2)
