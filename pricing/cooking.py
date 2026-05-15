"""Cooking-unit-to-store-unit bridge for recipe costing.

Converts recipe measurements (cups, tbsp, tsp) into store pricing units
(oz, fl oz, lb) so we can compute ingredient costs from product unit prices.

Key challenge: "1 cup rice" is a volume measurement, but stores price rice
by weight (per lb). We need ingredient-specific density data to cross
the volume->weight boundary for dry goods.
"""

from __future__ import annotations

from typing import Optional

from .constants import VOLUME_TO_FLOZ, WEIGHT_TO_OZ


# ── Cooking volume units → fluid ounces ────────────────────────────
COOKING_TO_FLOZ: dict[str, float] = {
    "cup": 8.0,
    "tbsp": 0.5,
    "tablespoon": 0.5,
    "tsp": 0.1667,
    "teaspoon": 0.1667,
}

# ── Special cooking units → weight in oz ───────────────────────────
COOKING_SPECIAL_TO_OZ: dict[str, float] = {
    "stick": 4.0,       # 1 stick butter = 4 oz = 1/2 cup
    "clove": 0.18,      # 1 clove garlic ~ 3g ~ 0.1 oz
}

# ── Ingredient density: oz (weight) per cup ────────────────────────
# Used when a recipe calls for "1 cup X" but the store prices X by weight.
# Values are approximate dry/uncooked weights per US cup.
DENSITY_OZ_PER_CUP: dict[str, float] = {
    # Flours
    "flour": 4.25,
    "all-purpose flour": 4.25,
    "all purpose flour": 4.25,
    "ap flour": 4.25,
    "bread flour": 4.5,
    "whole wheat flour": 4.25,
    "cake flour": 4.0,
    "almond flour": 3.4,
    "coconut flour": 4.5,
    "cornmeal": 5.5,
    "cornstarch": 4.5,

    # Sugars
    "sugar": 7.05,
    "granulated sugar": 7.05,
    "white sugar": 7.05,
    "brown sugar": 7.75,
    "powdered sugar": 4.0,
    "confectioners sugar": 4.0,

    # Grains & pasta
    "rice": 7.05,
    "white rice": 7.05,
    "brown rice": 6.5,
    "jasmine rice": 6.5,
    "basmati rice": 6.5,
    "pasta": 4.0,
    "macaroni": 4.0,
    "penne": 4.0,
    "spaghetti": 4.0,     # loosely packed dry
    "oats": 3.0,
    "rolled oats": 3.0,
    "quinoa": 6.0,
    "couscous": 6.0,
    "breadcrumbs": 4.0,
    "panko": 2.0,

    # Dairy & fats
    "butter": 8.0,
    "cream cheese": 8.0,
    "sour cream": 8.5,
    "shredded cheese": 4.0,
    "cheese": 4.0,
    "parmesan": 3.0,
    "grated parmesan": 3.0,

    # Nuts & seeds
    "nuts": 4.5,
    "peanuts": 5.0,
    "almonds": 5.0,
    "walnuts": 4.0,
    "pecans": 3.75,
    "cashews": 5.0,
    "sunflower seeds": 5.0,
    "sesame seeds": 5.0,

    # Spreads & thick liquids (priced by weight)
    "peanut butter": 9.0,
    "cocoa powder": 3.0,
    "cocoa": 3.0,
    "chocolate chips": 6.0,

    # Beans & legumes (dry)
    "beans": 6.5,
    "black beans": 6.5,
    "pinto beans": 6.5,
    "kidney beans": 6.5,
    "chickpeas": 6.5,
    "lentils": 7.0,

    # Liquid condiments (weight oz per cup — these are liquids priced by "oz" which is really fl oz)
    "soy sauce": 8.5,
    "vinegar": 8.4,
    "olive oil": 7.6,
    "vegetable oil": 7.7,
    "canola oil": 7.7,
    "sesame oil": 7.6,
    "maple syrup": 11.0,
    "hot sauce": 8.5,
    "worcestershire": 8.5,
    "worcestershire sauce": 8.5,
    "fish sauce": 8.8,
    "teriyaki sauce": 8.5,
    "oyster sauce": 9.0,

    # Misc
    "salt": 10.0,
    "coconut": 3.0,       # shredded
    "shredded coconut": 3.0,
    "raisins": 5.5,
    "dried cranberries": 5.0,
}

# Units that indicate "count" (each)
COUNT_COOKING_UNITS = {"each", "ea", "whole", "piece", "slice", "clove"}


def _normalize_cooking_unit(unit: str) -> str:
    """Lowercase, strip whitespace, handle common abbreviations."""
    u = unit.lower().strip().rstrip("s")  # "cups" -> "cup", "tbsps" -> "tbsp"
    # Fix over-stripped
    if u == "tbsp" or u == "tablespoon":
        return "tbsp"
    if u == "tsp" or u == "teaspoon":
        return "tsp"
    if u in ("cup", "c"):
        return "cup"
    if u in ("stick",):
        return "stick"
    if u in ("clove",):
        return "clove"
    if u in ("lb", "lbs", "pound"):
        return "pound"
    if u in ("oz", "ounce"):
        return "ounce"
    if u in ("g", "gram"):
        return "gram"
    if u in ("kg", "kilogram"):
        return "kilogram"
    if u in ("fl oz", "fl. oz", "floz", "fluid ounce"):
        return "fl oz"
    if u in ("gallon", "gal"):
        return "gallon"
    if u in ("quart", "qt"):
        return "quart"
    if u in ("pint", "pt"):
        return "pint"
    if u in ("liter", "litre", "l"):
        return "liter"
    if u in ("ml", "milliliter"):
        return "ml"
    if u in ("each", "ea", "whole", "piece", "slice"):
        return "each"
    if u in ("dozen", "dz"):
        return "dozen"
    return u


def _find_density(ingredient_name: str) -> Optional[float]:
    """Find the best matching density for an ingredient name."""
    name = ingredient_name.lower().strip()
    # Exact match first
    if name in DENSITY_OZ_PER_CUP:
        return DENSITY_OZ_PER_CUP[name]
    # Substring match: check if any density key appears in the ingredient name
    for key, density in DENSITY_OZ_PER_CUP.items():
        if key in name:
            return density
    # Check if ingredient name appears in any density key
    words = name.split()
    for key, density in DENSITY_OZ_PER_CUP.items():
        if any(w in key for w in words if len(w) > 2):
            return density
    return None


def compute_ingredient_cost(
    recipe_qty: float,
    recipe_unit: str,
    product: dict,
    ingredient_name: str = "",
    density_override: Optional[float] = None,
) -> Optional[float]:
    """Compute the cost of a recipe quantity using a product's std_units.

    Args:
        recipe_qty: How much the recipe calls for (e.g. 1.0)
        recipe_unit: The cooking unit (e.g. "cup", "lb", "tsp")
        product: A product dict that has been through standardize_results()
                 (must have "std_units" and "price" keys)
        ingredient_name: The ingredient name, used for density lookups
                         when converting cups of dry goods to weight.
        density_override: If provided, oz-per-cup density parsed from the
                         product's serving size label. Takes priority over
                         the hardcoded DENSITY_OZ_PER_CUP table.

    Returns:
        The dollar cost for this recipe quantity, or None if we can't compute it.
    """
    std = product.get("std_units") or {}
    price = product.get("price")
    if not std and price is None:
        return None

    unit = _normalize_cooking_unit(recipe_unit)

    # ── Direct weight units (lb, oz, g, kg) ────────────────────────
    if unit in WEIGHT_TO_OZ:
        oz_needed = recipe_qty * WEIGHT_TO_OZ[unit]
        if "per_oz" in std:
            return oz_needed * std["per_oz"]["value"]
        if "per_lb" in std:
            return (oz_needed / 16) * std["per_lb"]["value"]
        return None

    # ── Direct volume units (fl oz, gallon, quart, pint, liter, ml) ─
    if unit in VOLUME_TO_FLOZ:
        floz_needed = recipe_qty * VOLUME_TO_FLOZ[unit]
        if "per_fl_oz" in std:
            return floz_needed * std["per_fl_oz"]["value"]
        if "per_gal" in std:
            return (floz_needed / 128) * std["per_gal"]["value"]
        return None

    # ── Count units (each, piece, slice) ───────────────────────────
    if unit == "each":
        if "per_ea" in std:
            return recipe_qty * std["per_ea"]["value"]
        # If product only has a total price (no per_ea), use total price
        if price is not None:
            return recipe_qty * price
        return None

    if unit == "dozen":
        if "per_dozen" in std:
            return recipe_qty * std["per_dozen"]["value"]
        if "per_ea" in std:
            return recipe_qty * 12 * std["per_ea"]["value"]
        return None

    # ── Special units (stick, clove) ───────────────────────────────
    if unit in COOKING_SPECIAL_TO_OZ:
        oz_needed = recipe_qty * COOKING_SPECIAL_TO_OZ[unit]
        if "per_oz" in std:
            return oz_needed * std["per_oz"]["value"]
        if "per_lb" in std:
            return (oz_needed / 16) * std["per_lb"]["value"]
        return None

    # ── Cooking volume (cup, tbsp, tsp) ────────────────────────────
    # This is the cross-dimension bridge.
    if unit in COOKING_TO_FLOZ:
        floz_needed = recipe_qty * COOKING_TO_FLOZ[unit]

        # Strategy 1: Product is a liquid (has per_fl_oz) → direct volume
        if "per_fl_oz" in std:
            return floz_needed * std["per_fl_oz"]["value"]

        # Strategy 2: Product is priced by weight → use density
        # Priority: serving size label > hardcoded table
        if "per_oz" in std or "per_lb" in std:
            density = density_override or _find_density(ingredient_name)
            if density is not None:
                # Convert cups to oz via density
                cups = floz_needed / 8.0  # back to cups from fl oz
                oz_needed = cups * density
                if "per_oz" in std:
                    return oz_needed * std["per_oz"]["value"]
                if "per_lb" in std:
                    return (oz_needed / 16) * std["per_lb"]["value"]

        # Strategy 3: treat as fl oz anyway (last resort)
        if "per_gal" in std:
            return (floz_needed / 128) * std["per_gal"]["value"]

        return None

    return None


def format_ingredient_cost_breakdown(
    recipe_qty: float,
    recipe_unit: str,
    product: dict,
    ingredient_name: str = "",
    density_override: Optional[float] = None,
) -> Optional[str]:
    """Human-readable breakdown of how the cost was computed.

    Returns something like:
      "1 cup white rice = 7.05 oz x $0.06/oz = $0.39"
    """
    std = product.get("std_units") or {}
    unit = _normalize_cooking_unit(recipe_unit)

    if unit in WEIGHT_TO_OZ:
        oz_needed = recipe_qty * WEIGHT_TO_OZ[unit]
        if "per_oz" in std:
            cost = oz_needed * std["per_oz"]["value"]
            return f"{recipe_qty:g} {recipe_unit} = {oz_needed:.1f} oz x {std['per_oz']['string']} = ${cost:.2f}"
        if "per_lb" in std:
            lbs = oz_needed / 16
            cost = lbs * std["per_lb"]["value"]
            return f"{recipe_qty:g} {recipe_unit} = {lbs:.2f} lb x {std['per_lb']['string']} = ${cost:.2f}"

    if unit in VOLUME_TO_FLOZ:
        floz = recipe_qty * VOLUME_TO_FLOZ[unit]
        if "per_fl_oz" in std:
            cost = floz * std["per_fl_oz"]["value"]
            return f"{recipe_qty:g} {recipe_unit} = {floz:.1f} fl oz x {std['per_fl_oz']['string']} = ${cost:.2f}"

    if unit in COOKING_TO_FLOZ:
        floz = recipe_qty * COOKING_TO_FLOZ[unit]
        if "per_fl_oz" in std:
            cost = floz * std["per_fl_oz"]["value"]
            return f"{recipe_qty:g} {recipe_unit} = {floz:.1f} fl oz x {std['per_fl_oz']['string']} = ${cost:.2f}"
        if "per_oz" in std or "per_lb" in std:
            density = density_override or _find_density(ingredient_name)
            if density is not None:
                cups = floz / 8.0
                oz_needed = cups * density
                if "per_oz" in std:
                    cost = oz_needed * std["per_oz"]["value"]
                    return f"{recipe_qty:g} {recipe_unit} ({oz_needed:.1f} oz by weight) x {std['per_oz']['string']} = ${cost:.2f}"
                if "per_lb" in std:
                    lbs = oz_needed / 16
                    cost = lbs * std["per_lb"]["value"]
                    return f"{recipe_qty:g} {recipe_unit} ({oz_needed:.1f} oz by weight) x {std['per_lb']['string']} = ${cost:.2f}"

    if unit == "each" and "per_ea" in std:
        cost = recipe_qty * std["per_ea"]["value"]
        return f"{recipe_qty:g} x {std['per_ea']['string']} = ${cost:.2f}"

    if unit in COOKING_SPECIAL_TO_OZ:
        oz_needed = recipe_qty * COOKING_SPECIAL_TO_OZ[unit]
        if "per_oz" in std:
            cost = oz_needed * std["per_oz"]["value"]
            return f"{recipe_qty:g} {recipe_unit} ({oz_needed:.1f} oz) x {std['per_oz']['string']} = ${cost:.2f}"

    return None


# All recognized cooking units for the frontend dropdown
COOKING_UNITS = [
    "cup", "tbsp", "tsp",
    "oz", "lb", "g", "kg",
    "fl oz", "gallon", "quart", "pint", "liter", "ml",
    "each", "dozen",
    "stick", "clove",
    "whole", "piece", "slice",
]
