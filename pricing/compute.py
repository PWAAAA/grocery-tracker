"""Core computation: unit-price reps and the main standardize_results entry point."""

from __future__ import annotations

from typing import Optional

from .constants import (
    DIMENSION_DEFAULT,
    DIMENSION_FILTER,
    DOZEN_MULTIPLIER,
    UNIT_DIMENSION,
    UNIT_OPTION_ORDER,
    VOLUME_TO_FLOZ,
    WEIGHT_TO_OZ,
)
from .containers import detect_container_label
from .dimension import pick_dimension, product_dimension
from .parsing import parse_native_per_100, parse_native_unit_price, parse_pack_size_combined, parse_priced_per_unit


def total_count(pack: list[tuple[float, str]]) -> Optional[float]:
    """Total individual-item count from a parsed pack. None if no count present.
    Multiplies count terms ('12 Pack, Pack of 2' -> 24).
    Skips 'sheet' — sheets aren't items you buy individually."""
    qty = 1.0
    has = False
    for q, u in pack:
        if u == "sheet":
            continue
        if u == "dozen":
            qty *= q * 12
            has = True
        elif u in ("count", "pack"):
            qty *= q
            has = True
    return qty if has else None


def total_in_dimension(pack: list[tuple[float, str]], dimension: str) -> Optional[float]:
    """Sum a parsed pack into the canonical unit of the requested dimension.

    Multipack handling: if a product has BOTH a count and a volume/weight,
    we multiply (e.g., '12 pack 12 fl oz' -> 12 * 12 = 144 fl oz). This is
    the standard "X pack of Y" convention in Walmart names.
    """
    count_qty = 1.0
    has_count = False
    canon_values: list[float] = []

    for qty, unit in pack:
        dim = UNIT_DIMENSION.get(unit)
        if dim == "count":
            multiplier = DOZEN_MULTIPLIER.get(unit, 1)
            count_qty *= qty * multiplier
            has_count = True
        elif dimension == "volume" and unit in VOLUME_TO_FLOZ:
            canon_values.append(qty * VOLUME_TO_FLOZ[unit])
        elif dimension == "weight" and unit in WEIGHT_TO_OZ:
            canon_values.append(qty * WEIGHT_TO_OZ[unit])

    if dimension == "count":
        return count_qty if has_count else None

    if not canon_values:
        return None

    # Deduplicate near-equivalent entries (same size expressed in different units,
    # e.g. "1L, 946mL (33.8 fl oz)" — all represent the same volume).
    if not has_count and len(canon_values) > 1:
        mn, mx = min(canon_values), max(canon_values)
        canon_total = mx if (mn > 0 and mx / mn < 1.10) else sum(canon_values)
    else:
        canon_total = sum(canon_values)

    return canon_total * count_qty if has_count else canon_total


def _fmt_money_per(value: float, suffix: str) -> str:
    """'$0.04' -> '4.0¢/fl oz'; '$1.50' -> '$1.50/fl oz'."""
    if value < 1:
        return f"{value * 100:.1f}¢/{suffix}"
    return f"${value:.2f}/{suffix}"


def compute_unit_reps(
    price: Optional[float],
    name: Optional[str],
    container_label: str = "ea",
    dimension: Optional[str] = None,
    native_unit_price: Optional[str] = None,
    size: Optional[str] = None,
) -> dict[str, dict]:
    """All unit-price representations we can compute for one product."""
    if price is None or price <= 0:
        return {}

    # Detect "per lb" / "per oz" style sizes (variable-weight items).
    # If we have a native unit price string (e.g. "$2.19 / lb"), use that
    # for the real rate. Otherwise fall back to treating the listed price
    # as the per-unit rate (for stores that don't provide a separate rate).
    priced_per = parse_priced_per_unit(size)
    native = parse_native_unit_price(native_unit_price)
    if priced_per:
        out: dict[str, dict] = {}
        if native and native[1] in WEIGHT_TO_OZ:
            per_oz = native[0] / WEIGHT_TO_OZ[native[1]]
            per_lb = per_oz * 16
            out["per_oz"] = {"value": per_oz, "string": _fmt_money_per(per_oz, "oz")}
            out["per_lb"] = {"value": per_lb, "string": f"${per_lb:.2f}/lb"}
        elif native and native[1] in VOLUME_TO_FLOZ:
            per_floz = native[0] / VOLUME_TO_FLOZ[native[1]]
            per_gal = per_floz * 128
            out["per_fl_oz"] = {"value": per_floz, "string": _fmt_money_per(per_floz, "fl oz")}
            out["per_gal"] = {"value": per_gal, "string": f"${per_gal:.2f}/gal"}
        elif priced_per in WEIGHT_TO_OZ:
            per_oz = price / WEIGHT_TO_OZ[priced_per]
            per_lb = per_oz * 16
            out["per_oz"] = {"value": per_oz, "string": _fmt_money_per(per_oz, "oz")}
            out["per_lb"] = {"value": per_lb, "string": f"${per_lb:.2f}/lb"}
        elif priced_per in VOLUME_TO_FLOZ:
            per_floz = price / VOLUME_TO_FLOZ[priced_per]
            per_gal = per_floz * 128
            out["per_fl_oz"] = {"value": per_floz, "string": _fmt_money_per(per_floz, "fl oz")}
            out["per_gal"] = {"value": per_gal, "string": f"${per_gal:.2f}/gal"}
        elif priced_per == "each":
            out["per_ea"] = {"value": price, "string": f"${price:.2f}/{container_label}"}
        return out

    pack = parse_pack_size_combined(name, size)
    native = parse_native_unit_price(native_unit_price)

    prod_dim = product_dimension(name, size)
    # When query says "volume" but product detected "weight" only from bare "oz",
    # prefer query hint — bare "oz" is ambiguous (could be fl oz for liquids).
    if prod_dim == "weight" and dimension == "volume":
        pack_units = {u for _, u in pack}
        if pack_units <= {"ounce", "count", "pack", "dozen", "sheet"}:
            prod_dim = dimension
    prod_dim = prod_dim or dimension

    # Disambiguate bare "oz": when this product is a volume product,
    # "7.5 oz" should be read as fluid ounces, not weight ounces.
    if prod_dim == "volume":
        pack = [(q, "fl oz") if u == "ounce" else (q, u) for q, u in pack]
        if native and native[1] == "ounce":
            native = (native[0], "fl oz")

    out: dict[str, dict] = {}

    # --- per-each / per-dozen ---
    count = total_count(pack)
    if count and count > 0:
        per_ea = price / count
        out["per_ea"] = {"value": per_ea, "string": f"${per_ea:.2f}/{container_label}"}
        if prod_dim == "count":
            per_dz = per_ea * 12
            out["per_dozen"] = {"value": per_dz, "string": f"${per_dz:.2f}/dozen"}
    elif prod_dim == "count" and native and native[1] == "each":
        per_ea = native[0]
        out["per_ea"] = {"value": per_ea, "string": f"${per_ea:.2f}/{container_label}"}
        out["per_dozen"] = {"value": per_ea * 12, "string": f"${per_ea * 12:.2f}/dozen"}

    # --- per 100 ct (sheets, napkins, etc.) ---
    per_100 = parse_native_per_100(native_unit_price)
    if per_100 is not None:
        out["per_100_ct"] = {"value": per_100, "string": _fmt_money_per(per_100, "100 ct")}
    elif count and count > 0:
        sheet_qty = sum(q for q, u in pack if u == "sheet")
        if sheet_qty > 0:
            non_sheet_count = total_count([(q, u) for q, u in pack if u != "sheet"])
            total_sheets = sheet_qty * (non_sheet_count or 1)
            per_100_val = price / total_sheets * 100
            out["per_100_ct"] = {"value": per_100_val, "string": _fmt_money_per(per_100_val, "100 ct")}

    # --- volume ---
    # Prefer price/size over native unit price to avoid rounding errors
    # (e.g. native "$0.02/fl oz" * 128 = $2.56 instead of actual $2.90/gal)
    per_floz: Optional[float] = None
    vol = total_in_dimension(pack, "volume")
    if vol and vol > 0:
        per_floz = price / vol
    elif native and native[1] in VOLUME_TO_FLOZ:
        per_floz = native[0] / VOLUME_TO_FLOZ[native[1]]
    if per_floz:
        out["per_fl_oz"] = {"value": per_floz, "string": _fmt_money_per(per_floz, "fl oz")}
        per_gal = per_floz * 128
        out["per_gal"] = {"value": per_gal, "string": f"${per_gal:.2f}/gal"}

    # --- weight ---
    per_oz: Optional[float] = None
    wt = total_in_dimension(pack, "weight")
    if wt and wt > 0:
        per_oz = price / wt
    elif native and native[1] in WEIGHT_TO_OZ and prod_dim == "weight":
        per_oz = native[0] / WEIGHT_TO_OZ[native[1]]
    if per_oz:
        out["per_oz"] = {"value": per_oz, "string": _fmt_money_per(per_oz, "oz")}
        per_lb = per_oz * 16
        out["per_lb"] = {"value": per_lb, "string": f"${per_lb:.2f}/lb"}

    return out


def standardize_results(query: str, products: list[dict]) -> dict:
    """Mutate products in-place; return query-wide unit metadata.

    Each product gets:
      std_units — dict of every computable rep (see compute_unit_reps).

    Returns:
      {
        "unit_default":    str | None,
        "unit_options":    list[str],
        "container_label": str,
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
    allowed = DIMENSION_FILTER.get(dimension or "")
    options = [k for k in UNIT_OPTION_ORDER if k in available and (allowed is None or k in allowed)]

    return {
        "unit_default": default,
        "unit_options": options,
        "container_label": container_label,
        "dimension": dimension,
    }
