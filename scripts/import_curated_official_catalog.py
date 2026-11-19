"""Import human-reviewed product lines and complete shade lists from official pages."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data/product_catalog.json"
SOURCE = ROOT / "data/catalog_curated_official.json"
DOMAINS = ROOT / "data/official_domains.json"


def allowed_domains(registry: dict, brand: str) -> set[str]:
    key = next((key for key, item in registry.items() if brand.casefold() in {
        key.casefold(), str(item.get("canonical_name", "")).casefold(),
        *(str(alias).casefold() for alias in item.get("aliases", [])),
    }), None)
    if not key:
        return set()
    return {domain.removeprefix("www.") for values in registry[key]["regions"].values() for domain in values}


def main() -> None:
    rows = json.loads(SOURCE.read_text())
    registry = json.loads(DOMAINS.read_text())
    existing = json.loads(CATALOG.read_text()) if CATALOG.exists() else []
    now = datetime.now(timezone.utc).isoformat()
    imported = []
    checked_urls = set()
    source_status = {}
    for row in rows:
        host = (urlparse(row["source_url"]).hostname or "").removeprefix("www.")
        domains = allowed_domains(registry, row["brand"])
        if not any(host == domain or host.endswith("." + domain) for domain in domains):
            raise ValueError(f"unverified official domain for {row['brand']}: {host}")
        if row["source_url"] not in checked_urls:
            response = httpx.get(row["source_url"], follow_redirects=True, timeout=30,
                                 headers={"User-Agent": "MakeupTranslatorCatalog/1.0"})
            if response.status_code not in {401, 403}:
                response.raise_for_status()
            else:
                print("official page blocks automated fetch", response.status_code, row["source_url"])
            source_status[row["source_url"]] = response.status_code
            checked_urls.add(row["source_url"])
        for shade in row["shades"]:
            digest = hashlib.sha1(f"{row['brand']}|{row['category']}|{row['name']}|{shade}".encode()).hexdigest()[:14]
            imported.append({
                "id": f"official-{digest}", "brand": row["brand"], "name": row["name"],
                "shade": shade, "market": row.get("market"), "version": None,
                "category": row["category"], "aliases": row.get("aliases", []), "source_url": row["source_url"],
                "verified_at": now, "verification_status": "official_listing_observed",
                "source_kind": "official_site", "store_identity": row["brand"],
                "selection_basis": "human_reviewed_official_product_page",
                "net_content": row.get("net_content"),
                "shade_collection_basis": "all shades listed on the cited official page",
                "source_access_status": source_status[row["source_url"]],
                "evidence": {"name": row["name"], "shade": shade},
            })
    curated_brands = {row["brand"].casefold() for row in rows}
    curated_categories = {(row["brand"].casefold(), row["category"]) for row in rows}
    merged = [item for item in existing if not (
        item.get("source_kind") == "official_site"
        and (str(item.get("brand", "")).casefold(), item.get("category")) in curated_categories
    )]
    merged.extend(imported)
    CATALOG.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    print("official curated records", len(imported), "brands", len(curated_brands), "catalog", len(existing), "->", len(merged))


if __name__ == "__main__":
    main()
