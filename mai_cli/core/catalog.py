"""Catalog search and merchant/product persistence."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from mai_cli.db.session import decode_json, encode_json, now_iso


def parse_tags(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    parts = re.split(r"[,;，；、\n]+", str(value))
    return [part.strip() for part in parts if part.strip()]


def tokenize(value: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[\w\u4e00-\u9fff]+", value or "")]


def require_merchant(conn: sqlite3.Connection, merchant_id: str) -> sqlite3.Row:
    row = conn.execute("select * from merchants where id = ?", (merchant_id,)).fetchone()
    if row is None:
        raise SystemExit(f"Unknown merchant: {merchant_id}")
    return row


def require_product(conn: sqlite3.Connection, sku: str) -> sqlite3.Row:
    row = conn.execute("select * from products where sku = ?", (sku,)).fetchone()
    if row is None:
        raise SystemExit(f"Unknown product SKU: {sku}")
    return row


def create_merchant(
    conn: sqlite3.Connection,
    merchant_id: str,
    name: str,
    city: str = "",
    service_area: str = "",
    contact: str = "",
    hours: str = "",
    automation_boundaries: str = "",
    tags: str | list[str] | None = None,
    delivery_fee: float = 0,
    delivery_eta_minutes: int = 0,
    delivery_radius_km: float = 0,
) -> dict[str, Any]:
    now = now_iso()
    conn.execute(
        """
        insert into merchants(
            id, name, city, service_area, contact, hours, automation_boundaries,
            tags_json, created_at, updated_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            merchant_id,
            name,
            city,
            service_area,
            contact,
            hours,
            automation_boundaries,
            encode_json(parse_tags(tags)),
            now,
            now,
        ),
    )
    upsert_delivery_rule(
        conn,
        merchant_id,
        service_area=service_area,
        fee=delivery_fee,
        eta_minutes=delivery_eta_minutes,
        radius_km=delivery_radius_km,
    )
    return merchant_summary(conn, merchant_id)


def update_merchant(
    conn: sqlite3.Connection,
    merchant_id: str,
    name: str | None = None,
    city: str | None = None,
    service_area: str | None = None,
    contact: str | None = None,
    hours: str | None = None,
    automation_boundaries: str | None = None,
    tags: str | list[str] | None = None,
    delivery_fee: float | None = None,
    delivery_eta_minutes: int | None = None,
    delivery_radius_km: float | None = None,
) -> dict[str, Any]:
    merchant = require_merchant(conn, merchant_id)
    updates: list[str] = []
    values: list[Any] = []
    field_map = {
        "name": name,
        "city": city,
        "service_area": service_area,
        "contact": contact,
        "hours": hours,
        "automation_boundaries": automation_boundaries,
    }
    for column, value in field_map.items():
        if value is not None:
            updates.append(f"{column} = ?")
            values.append(value)
    if tags is not None:
        updates.append("tags_json = ?")
        values.append(encode_json(parse_tags(tags)))
    if updates:
        updates.append("updated_at = ?")
        values.append(now_iso())
        values.append(merchant_id)
        conn.execute(f"update merchants set {', '.join(updates)} where id = ?", values)

    delivery = delivery_rule(conn, merchant_id)
    if any(value is not None for value in (service_area, delivery_fee, delivery_eta_minutes, delivery_radius_km)):
        upsert_delivery_rule(
            conn,
            merchant_id,
            service_area=service_area if service_area is not None else delivery["service_area"] or merchant["service_area"],
            fee=delivery_fee if delivery_fee is not None else delivery["fee"],
            eta_minutes=delivery_eta_minutes if delivery_eta_minutes is not None else delivery["eta_minutes"],
            radius_km=delivery_radius_km if delivery_radius_km is not None else delivery["radius_km"],
            notes=delivery["notes"],
            currency=delivery["currency"],
        )
    return merchant_summary(conn, merchant_id)


def upsert_delivery_rule(
    conn: sqlite3.Connection,
    merchant_id: str,
    service_area: str = "",
    fee: float = 0,
    eta_minutes: int = 0,
    radius_km: float = 0,
    notes: str = "",
    currency: str = "CNY",
) -> dict[str, Any]:
    require_merchant(conn, merchant_id)
    now = now_iso()
    conn.execute(
        """
        insert into delivery_rules(
            merchant_id, service_area, fee, currency, eta_minutes, radius_km,
            notes, created_at, updated_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(merchant_id) do update set
            service_area = excluded.service_area,
            fee = excluded.fee,
            currency = excluded.currency,
            eta_minutes = excluded.eta_minutes,
            radius_km = excluded.radius_km,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (merchant_id, service_area, fee, currency, eta_minutes, radius_km, notes, now, now),
    )
    return delivery_rule(conn, merchant_id)


def create_product(
    conn: sqlite3.Connection,
    merchant_id: str,
    sku: str,
    title: str,
    price: float,
    stock: int,
    currency: str = "CNY",
    category: str = "",
    tags: str | list[str] | None = None,
    description: str = "",
    delivery_attributes: str | list[str] | None = None,
) -> dict[str, Any]:
    if price < 0:
        raise SystemExit("--price must be non-negative")
    if stock < 0:
        raise SystemExit("--stock must be non-negative")
    require_merchant(conn, merchant_id)
    now = now_iso()
    conn.execute(
        """
        insert into products(
            sku, merchant_id, title, description, category, tags_json, price,
            currency, stock, delivery_attributes_json, active, created_at, updated_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            sku,
            merchant_id,
            title,
            description,
            category,
            encode_json(parse_tags(tags)),
            float(price),
            currency,
            int(stock),
            encode_json(parse_tags(delivery_attributes)),
            now,
            now,
        ),
    )
    return product_summary(conn, sku)


def update_product(
    conn: sqlite3.Connection,
    sku: str,
    merchant_id: str = "",
    title: str | None = None,
    price: float | None = None,
    stock: int | None = None,
    currency: str | None = None,
    category: str | None = None,
    tags: str | list[str] | None = None,
    description: str | None = None,
    delivery_attributes: str | list[str] | None = None,
) -> dict[str, Any]:
    product = require_product(conn, sku)
    if merchant_id and product["merchant_id"] != merchant_id:
        raise SystemExit(f"Product {sku} does not belong to merchant {merchant_id}")
    if price is not None and price < 0:
        raise SystemExit("--price must be non-negative")
    if stock is not None and stock < 0:
        raise SystemExit("--stock must be non-negative")
    updates: list[str] = []
    values: list[Any] = []
    field_map = {
        "title": title,
        "price": price,
        "stock": stock,
        "currency": currency,
        "category": category,
        "description": description,
    }
    for column, value in field_map.items():
        if value is not None:
            updates.append(f"{column} = ?")
            values.append(value)
    if tags is not None:
        updates.append("tags_json = ?")
        values.append(encode_json(parse_tags(tags)))
    if delivery_attributes is not None:
        updates.append("delivery_attributes_json = ?")
        values.append(encode_json(parse_tags(delivery_attributes)))
    if updates:
        updates.append("updated_at = ?")
        values.append(now_iso())
        values.append(sku)
        conn.execute(f"update products set {', '.join(updates)} where sku = ?", values)
    return product_summary(conn, sku)


def set_stock(conn: sqlite3.Connection, sku: str, stock: int, merchant_id: str = "") -> dict[str, Any]:
    if stock < 0:
        raise SystemExit("--stock must be non-negative")
    product = require_product(conn, sku)
    if merchant_id and product["merchant_id"] != merchant_id:
        raise SystemExit(f"Product {sku} does not belong to merchant {merchant_id}")
    conn.execute(
        "update products set stock = ?, updated_at = ? where sku = ?",
        (int(stock), now_iso(), sku),
    )
    return product_summary(conn, sku)


def delivery_rule(conn: sqlite3.Connection, merchant_id: str) -> dict[str, Any]:
    row = conn.execute("select * from delivery_rules where merchant_id = ?", (merchant_id,)).fetchone()
    if row is None:
        return {
            "service_area": "",
            "fee": 0.0,
            "currency": "CNY",
            "eta_minutes": 0,
            "radius_km": 0.0,
            "notes": "",
        }
    return {
        "service_area": row["service_area"],
        "fee": float(row["fee"]),
        "currency": row["currency"],
        "eta_minutes": int(row["eta_minutes"]),
        "radius_km": float(row["radius_km"]),
        "notes": row["notes"],
    }


def merchant_summary(conn: sqlite3.Connection, merchant_id: str) -> dict[str, Any]:
    merchant = require_merchant(conn, merchant_id)
    product_count = conn.execute(
        "select count(*) from products where merchant_id = ? and active = 1",
        (merchant_id,),
    ).fetchone()[0]
    return {
        "id": merchant["id"],
        "name": merchant["name"],
        "city": merchant["city"],
        "service_area": merchant["service_area"],
        "contact": merchant["contact"],
        "hours": merchant["hours"],
        "automation_boundaries": merchant["automation_boundaries"],
        "tags": decode_json(merchant["tags_json"], []),
        "delivery": delivery_rule(conn, merchant_id),
        "product_count": product_count,
    }


def product_summary(conn: sqlite3.Connection, sku: str) -> dict[str, Any]:
    product = require_product(conn, sku)
    merchant = merchant_summary(conn, product["merchant_id"])
    return {
        "sku": product["sku"],
        "merchant_id": product["merchant_id"],
        "title": product["title"],
        "description": product["description"],
        "category": product["category"],
        "tags": decode_json(product["tags_json"], []),
        "price": float(product["price"]),
        "currency": product["currency"],
        "stock": int(product["stock"]),
        "delivery_attributes": decode_json(product["delivery_attributes_json"], []),
        "merchant": merchant,
        "delivery": merchant["delivery"],
        "warnings": product_warnings(product, merchant),
    }


def product_warnings(product: sqlite3.Row, merchant: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if int(product["stock"]) <= 0:
        warnings.append("out of stock")
    elif int(product["stock"]) <= 2:
        warnings.append("low stock")
    if not merchant.get("contact"):
        warnings.append("merchant contact missing")
    if not merchant.get("delivery", {}).get("service_area"):
        warnings.append("delivery rule missing")
    return warnings


def _search_text(product: sqlite3.Row, merchant: sqlite3.Row) -> str:
    fields = [
        product["sku"],
        product["title"],
        product["description"],
        product["category"],
        " ".join(decode_json(product["tags_json"], [])),
        merchant["name"],
        merchant["city"],
        merchant["service_area"],
        " ".join(decode_json(merchant["tags_json"], [])),
    ]
    return " ".join(str(field) for field in fields if field)


def _match_score(query: str, product: sqlite3.Row, merchant: sqlite3.Row) -> float:
    query_lower = query.lower()
    searchable = _search_text(product, merchant).lower()
    query_tokens = tokenize(query_lower)
    product_tokens = tokenize(searchable)
    score = 0.0
    for token in query_tokens:
        if token in searchable:
            score += 10
    for token in product_tokens:
        if len(token) >= 2 and token in query_lower:
            score += 8
    if int(product["stock"]) > 0:
        score += 5
    score -= float(product["price"]) / 1000
    return round(score, 4)


def search_products(
    conn: sqlite3.Connection,
    query: str = "",
    city: str = "",
    area: str = "",
    max_price: float | None = None,
    include_out_of_stock: bool = False,
    limit: int = 10,
) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    city = str(city or "").strip()
    area = str(area or "").strip()
    rows = conn.execute(
        """
        select p.*, m.name as merchant_name, m.city as merchant_city, m.service_area as merchant_service_area,
               m.contact as merchant_contact, m.hours as merchant_hours, m.tags_json as merchant_tags_json
        from products p
        join merchants m on m.id = p.merchant_id
        where p.active = 1
        """
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        merchant = require_merchant(conn, row["merchant_id"])
        if city and merchant["city"].lower() != city.lower():
            continue
        if max_price is not None and float(row["price"]) > max_price:
            continue
        if not include_out_of_stock and int(row["stock"]) <= 0:
            continue
        score = _match_score(query, row, merchant)
        if query and score <= (5 if int(row["stock"]) > 0 else 0):
            continue
        summary = product_summary(conn, row["sku"])
        if area and area not in summary["merchant"].get("service_area", ""):
            summary.setdefault("warnings", []).append("requested area may need merchant confirmation")
        summary["match_score"] = score
        results.append(summary)
    return sorted(results, key=lambda item: (-item["match_score"], item["price"], item["sku"]))[:limit]


def search_merchants(
    conn: sqlite3.Connection,
    query: str = "",
    city: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    city = str(city or "").strip()
    query_lower = query.lower()
    query_tokens = tokenize(query_lower)
    rows = conn.execute("select * from merchants order by name, id").fetchall()
    results: list[dict[str, Any]] = []
    for merchant in rows:
        if city and merchant["city"].lower() != city.lower():
            continue
        searchable = " ".join(
            [
                merchant["id"],
                merchant["name"],
                merchant["city"],
                merchant["service_area"],
                " ".join(decode_json(merchant["tags_json"], [])),
            ]
        ).lower()
        merchant_tokens = tokenize(searchable)
        score = 0.0
        for token in query_tokens:
            if token in searchable:
                score += 10
        for token in merchant_tokens:
            if len(token) >= 2 and token in query_lower:
                score += 8
        if query and score <= 0:
            continue
        summary = merchant_summary(conn, merchant["id"])
        summary["match_score"] = round(score, 4)
        results.append(summary)
    return sorted(results, key=lambda item: (-item["match_score"], item["name"], item["id"]))[:limit]
