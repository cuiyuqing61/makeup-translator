"""Import at most three official product lines per category with all listed shades.

The Shopify JSON is downloaded separately and stored in ``runtime/catalog-feeds``.
Only configured official domains are accepted. Existing unrelated records remain.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data/product_catalog.json"
SOURCES = ROOT / "data/catalog_shopify_sources.json"
FEEDS = ROOT / "runtime/catalog-feeds"
MAX_LINES = 3

CATEGORY_TERMS = {
    "contour": ("contour", "bronzer", "sculpt", "shading"),
    "highlighter": ("highlighter", "highlight", "brighten", "illuminator"),
    "blush": ("blush", "cheek"),
    "brow": ("eyebrow", "brow ", "brow-", "browgel"),
    "eyeshadow": ("eyeshadow", "eye shadow", "eye palette"),
    "eyeliner": ("eyeliner", "eye liner", "アイライナー"),
    "mascara": ("mascara", "lash primer"),
    "lip": ("lipstick", "lip ", "lip-", "rouge", "lipgloss", "lipcream"),
    "base": ("foundation", "face powder", "pressed powder", "loose powder", "setting powder", "setting spray", "concealer", "primer", "base makeup", "face cushion", "ファンデーション", "プレストパウダー", "ルースパウダー"),
}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def classify(product: dict) -> list[str]:
    # Shopify tags often contain every navigation category and would turn an
    # eyeshadow into contour/base/etc. Classify only explicit product naming.
    text = " ".join([str(product.get("title") or ""), str(product.get("product_type") or "")]).casefold()
    if any(word in text for word in ("remover", "cleansing", "makeup bag", "puff", "brush", "tester", "experience pack", "points-redeemable", "member's only", "not for sale")):
        return []
    if any(word in text for word in ("bundle", "set", "bag", "mirror", "brush", "puff", "refill")):
        # Keep multi-use makeup palettes named "set" only when the title also
        # states a supported makeup function.
        if not any(term in text for terms in CATEGORY_TERMS.values() for term in terms):
            return []
    matches = [category for category, terms in CATEGORY_TERMS.items() if any(term in text for term in terms)]
    # Lip products containing "cushion" must not become base products.
    if "lip" in matches and "base" in matches and not any(x in text for x in ("foundation", "face powder", "concealer", "primer")):
        matches.remove("base")
    return matches


def download(config: dict) -> tuple[bytes, Path]:
    feed_host = (urlparse(config["feed_url"]).hostname or "").removeprefix("www.")
    site_host = (urlparse(config["site_root"]).hostname or "").removeprefix("www.")
    if feed_host != site_host:
        raise ValueError(f"feed host does not match official site: {config['brand']}")
    response = httpx.get(config["feed_url"], follow_redirects=True, timeout=30,
                         headers={"User-Agent": "MakeupTranslatorCatalog/1.0"})
    response.raise_for_status()
    if (urlparse(str(response.url)).hostname or "").removeprefix("www.") != site_host:
        raise ValueError(f"feed redirected away from official site: {config['brand']}")
    FEEDS.mkdir(parents=True, exist_ok=True)
    path = FEEDS / f"{slug(config['brand'])}.json"
    path.write_bytes(response.content)
    return response.content, path


def records(config: dict, raw: bytes) -> list[dict]:
    products = json.loads(raw).get("products")
    if not isinstance(products, list):
        raise ValueError(f"invalid product feed for {config['brand']}")
    selected = {category: [] for category in CATEGORY_TERMS}
    for product in products:
        for category in classify(product):
            if len(selected[category]) < MAX_LINES:
                selected[category].append(product)
    now = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(raw).hexdigest()
    output = []
    for category, lines in selected.items():
        for product in lines:
            handle = product.get("handle")
            product_url = config["site_root"].rstrip("/") + "/products/" + str(handle)
            for variant in product.get("variants") or []:
                shade = str(variant.get("title") or "").strip()
                if shade.casefold() == "default title":
                    shade = None
                variant_id = str(variant.get("id") or slug(shade or product["title"]))
                evidence = {"name": product["title"]}
                if shade:
                    evidence["shade"] = shade
                output.append({
                    "id": f"{slug(config['brand'])}-{category}-{variant_id}",
                    "brand": config["brand"], "name": product["title"], "shade": shade,
                    "market": "国际", "version": None, "category": category,
                    "aliases": [], "source_url": product_url, "feed_url": config["feed_url"],
                    "product_handle": handle, "verified_at": now,
                    "verification_status": "official_listing_observed",
                    "source_kind": config["source_kind"], "store_identity": config["brand"],
                    "selection_basis": config["selection_basis"],
                    "feed_sha256": digest, "evidence": evidence,
                })
    return output


def main() -> None:
    existing = json.loads(CATALOG.read_text()) if CATALOG.exists() else []
    configs = json.loads(SOURCES.read_text())
    imported = []
    for config in configs:
        raw, path = download(config)
        brand_records = records(config, raw)
        if not brand_records:
            raise ValueError(f"no supported makeup records in {path}")
        imported.extend(brand_records)
        print(config["brand"], len(brand_records), "shade records")
    configured_brands = {item["brand"] for item in configs}
    # Replace this importer's prior output as a unit, while preserving older
    # manually verified and brand-specific catalog records.
    merged = [item for item in existing if not (
        item.get("brand") in configured_brands and item.get("source_kind") == "official_site"
    )]
    merged.extend(imported)
    CATALOG.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    print("catalog", len(existing), "->", len(merged), "records")


if __name__ == "__main__":
    main()
