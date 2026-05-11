"""Import catalog data from the pre-MVP Mai JSON store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from mai_cli.core.catalog import create_merchant, create_product


def import_json_store(conn: sqlite3.Connection, source: str | Path) -> dict[str, Any]:
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    imported = {"merchants": 0, "products": 0}
    for merchant_id, merchant in data.get("merchants", {}).items():
        create_merchant(
            conn,
            merchant_id=str(merchant.get("id") or merchant_id),
            name=str(merchant.get("name") or merchant_id),
            city=str(merchant.get("city") or ""),
            service_area=str(merchant.get("service_area") or merchant.get("serviceArea") or ""),
            contact=str(merchant.get("contact") or ""),
            hours=str(merchant.get("hours") or ""),
            tags=merchant.get("tags") or [],
        )
        imported["merchants"] += 1
    for sku, product in data.get("products", {}).items():
        create_product(
            conn,
            merchant_id=str(product.get("merchant_id") or product.get("merchant") or ""),
            sku=str(product.get("sku") or sku),
            title=str(product.get("title") or sku),
            description=str(product.get("description") or ""),
            category=str(product.get("category") or ""),
            tags=product.get("tags") or [],
            price=float(product.get("price") or 0),
            currency=str(product.get("currency") or "CNY"),
            stock=int(product.get("stock") or 0),
            delivery_attributes=str(product.get("shipping") or ""),
        )
        imported["products"] += 1
    return {"ok": True, "imported": imported}
