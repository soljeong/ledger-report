"""Load and validate analysis-layer adjustment specifications."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


NON_INVENTORY_CONSUMABLE = "non_inventory_consumable"
SUPPORTED_INVENTORY_ADJUSTMENTS = {NON_INVENTORY_CONSUMABLE}


def empty_analysis_spec() -> dict[str, Any]:
    return {"version": 1, "inventory_adjustments": []}


def validate_analysis_spec(spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("analysis spec must be a mapping")
    if spec.get("version", 1) != 1:
        raise ValueError("analysis spec version must be 1")
    adjustments = spec.get("inventory_adjustments", [])
    if not isinstance(adjustments, list):
        raise ValueError("analysis spec inventory_adjustments must be a list")

    seen_ids: set[str] = set()
    seen_product_ids: set[Any] = set()
    normalized: list[dict[str, Any]] = []
    for index, adjustment in enumerate(adjustments, start=1):
        if not isinstance(adjustment, dict):
            raise ValueError(f"inventory adjustment #{index} must be a mapping")
        adjustment_id = adjustment.get("id")
        product_id = adjustment.get("product_id")
        treatment = adjustment.get("treatment")
        if not isinstance(adjustment_id, str) or not adjustment_id.strip():
            raise ValueError(f"inventory adjustment #{index} requires a nonempty id")
        if product_id in (None, ""):
            raise ValueError(f"inventory adjustment {adjustment_id!r} requires product_id")
        if treatment not in SUPPORTED_INVENTORY_ADJUSTMENTS:
            raise ValueError(
                f"inventory adjustment {adjustment_id!r} treatment must be one of "
                f"{sorted(SUPPORTED_INVENTORY_ADJUSTMENTS)!r}"
            )
        if adjustment_id in seen_ids:
            raise ValueError(f"duplicate inventory adjustment id: {adjustment_id!r}")
        if product_id in seen_product_ids:
            raise ValueError(f"duplicate inventory adjustment product_id: {product_id!r}")
        seen_ids.add(adjustment_id)
        seen_product_ids.add(product_id)
        normalized.append(
            {
                "id": adjustment_id,
                "product_id": product_id,
                "treatment": treatment,
                "description": str(adjustment.get("description") or "").strip(),
            }
        )
    return {"version": 1, "inventory_adjustments": normalized}


def load_analysis_spec(path: Path | None) -> dict[str, Any]:
    if path is None:
        return empty_analysis_spec()
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return validate_analysis_spec(loaded if loaded is not None else empty_analysis_spec())
