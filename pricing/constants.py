"""Conversion tables and keyword maps for unit-price standardization."""

from __future__ import annotations

# Volume -> fluid ounces (canonical for the volume dimension)
VOLUME_TO_FLOZ: dict[str, float] = {
    "fl oz": 1.0,
    "gallon": 128.0,
    "quart": 32.0,
    "pint": 16.0,
    "liter": 33.814,
    "ml": 0.033814,
}

# Weight -> ounces (canonical for the weight dimension)
WEIGHT_TO_OZ: dict[str, float] = {
    "ounce": 1.0,
    "pound": 16.0,
    "kilogram": 35.274,
    "gram": 0.035274,
}

# Count units (canonical: each)
COUNT_UNITS: set[str] = {"count", "pack", "dozen", "sheet"}
DOZEN_MULTIPLIER = {"dozen": 12}

# Reverse lookup: normalized unit -> dimension
UNIT_DIMENSION: dict[str, str] = {}
for u in VOLUME_TO_FLOZ:
    UNIT_DIMENSION[u] = "volume"
for u in WEIGHT_TO_OZ:
    UNIT_DIMENSION[u] = "weight"
for u in COUNT_UNITS:
    UNIT_DIMENSION[u] = "count"


# ===== Query -> preferred dimension =====
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
        "paper towel", "toilet paper", "tissue", "napkin", "trash bag", "battery",
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

# Default rep per dimension and the order options should appear in the toggle.
DIMENSION_DEFAULT: dict[str, str] = {
    "volume": "per_fl_oz",
    "weight": "per_lb",
    "count": "per_100_ct",
}
UNIT_OPTION_ORDER: list[str] = [
    "per_ea", "per_dozen", "per_100_ct", "per_fl_oz", "per_gal", "per_oz", "per_lb",
]

DIMENSION_FILTER: dict[str, set[str]] = {
    "volume": {"per_ea", "per_fl_oz", "per_gal"},
    "weight": {"per_ea", "per_oz", "per_lb"},
    "count":  {"per_ea", "per_dozen", "per_100_ct"},
}
