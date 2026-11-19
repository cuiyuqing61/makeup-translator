"""Seed one source-backed product per captured Taobao/Tmall flagship brand.

These entries broaden identity coverage while official-site importers continue
to add richer product lines and shade variants. Promotional samples and sets
are excluded instead of being mistaken for standard products.
"""
from __future__ import annotations

import json
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from merge_taobao_scope import canonical


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data/product_catalog.json"
RANKINGS = ROOT / "data/taobao_rankings.json"
DETAILS = ROOT / "data/taobao_products.json"
EXCLUDED = ("小样", "试用", "尝鲜", "赠品", "空盒", "替换装", "分装", "体验装", "旅行装", "试色卡", "派样", "礼盒", "套装", "套组", "组合装")


def main() -> None:
    existing = json.loads(CATALOG.read_text())
    rankings = json.loads(RANKINGS.read_text())
    now = datetime.now(timezone.utc).isoformat()
    imported = []
    detailed_ids = set()
    if DETAILS.exists():
        details = json.loads(DETAILS.read_text())
        for target in details.get("targets", []):
            for product in target.get("products", []):
                if any(word.casefold() in product.get("listing_title", "").casefold() for word in EXCLUDED):
                    continue
                detailed_ids.add(product["item_id"])
                shades = product.get("shades") or [None]
                for shade in shades:
                    shade_key = hashlib.sha1((shade or "none").encode()).hexdigest()[:10]
                    parsed = urlparse(product["item_url"])
                    imported.append({
                        "id": f"taobao-{target['category']}-{product['item_id']}-{shade_key}",
                        "brand": target["brand"], "name": product["product_name"],
                        "shade": shade, "market": "中国大陆", "version": None,
                        "category": target["category"],
                        "aliases": ([product["listing_title"]]
                                    if product.get("listing_title") and product["listing_title"] != product["product_name"]
                                    else []),
                        "source_url": product["item_url"], "verified_at": product["verified_at"],
                        "verification_status": "official_listing_observed",
                        "source_kind": "official_flagship_tmall" if parsed.hostname == "detail.tmall.com" else "official_flagship_taobao",
                        "store_identity": target["store_name"], "store_identity_verified": True,
                        "selection_basis": "targeted_official_flagship_full_size_search",
                        "taobao_sales_text": product.get("sales_text"),
                        "net_content": product.get("net_content"),
                        "shade_collection_basis": product.get("shade_collection_basis"),
                        "evidence": {"name": product["product_name"], "shade": shade},
                    })
    for category, rows in rankings["categories"].items():
        for row in rows:
            title = re.sub(r"\s+", " ", row["title"]).strip()
            if not title or any(word in title for word in EXCLUDED):
                continue
            parsed = urlparse(row["item_url"])
            if parsed.hostname not in {"detail.tmall.com", "item.taobao.com"}:
                continue
            item_id = (parse_qs(parsed.query).get("id") or [None])[0]
            if not item_id or "旗舰店" not in row["store_name"]:
                continue
            if item_id in detailed_ids:
                continue
            brand = canonical(row["brand"])
            imported.append({
                "id": f"taobao-{category}-{item_id}", "brand": brand,
                "name": title, "shade": None, "market": "中国大陆", "version": None,
                "category": category, "aliases": [], "source_url": row["item_url"],
                "verified_at": now, "verification_status": "official_listing_observed",
                "source_kind": "official_flagship_tmall" if parsed.hostname == "detail.tmall.com" else "official_flagship_taobao",
                "store_identity": row["store_name"], "store_identity_verified": True,
                "selection_basis": "taobao_sales_sorted_flagship_result",
                "taobao_sales_text": row["sales_text"],
                "evidence": {"name": title},
            })
    merged = [item for item in existing if item.get("source_kind") not in {"official_flagship_tmall", "official_flagship_taobao"}]
    known_ids = {item["id"] for item in merged}
    merged.extend(item for item in imported if item["id"] not in known_ids)
    CATALOG.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    print("flagship records", len(imported), "catalog", len(existing), "->", len(merged))


if __name__ == "__main__":
    main()
