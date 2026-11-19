"""Import up to three CANMAKE product lines per supported category."""
from __future__ import annotations

import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data/product_catalog.json"
SITE = "https://www.canmake.com"
CATEGORY_PAGES = {
    "base": ["/en/item/category/cat01_02/", "/en/item/category/cat01_03/", "/en/item/category/cat01_01/"],
    "brow": ["/en/item/category/cat03_04/"],
    "eyeshadow": ["/en/item/category/cat03_01/"],
    "eyeliner": ["/en/item/category/cat03_05/"],
    "mascara": ["/en/item/category/cat03_02/"],
    "contour": ["/en/item/category/cat01_05/"],
    "highlighter": ["/en/item/category/cat01_04/"],
    "blush": ["/en/item/category/cat02/"],
    "lip": ["/en/item/category/cat04/"],
}


def get(url: str) -> bytes:
    response = httpx.get(url, follow_redirects=True, timeout=30,
                         headers={"User-Agent": "MakeupTranslatorCatalog/1.0"})
    response.raise_for_status()
    if not (response.url.host == "www.canmake.com" or response.url.host == "canmake.com"):
        raise ValueError("CANMAKE source redirected outside official domain")
    return response.content


def product_links(raw: bytes) -> list[tuple[str, str]]:
    text = raw.decode("utf-8", errors="replace")
    output = []
    for href, content in re.findall(r'<a[^>]+href="([^"]*/en/item/detail/[^"]+)"[^>]*>(.*?)</a>', text, re.S | re.I):
        name = html.unescape(re.sub(r"<[^>]+>", " ", content))
        name = re.sub(r"\s+", " ", name).strip()
        name = re.sub(r"^(?:(?:NEW|LIMITED) (?:ITEM|COLOR)\s*)+", "", name, flags=re.I)
        name = re.sub(r"\s*¥[\d,]+.*$", "", name).strip()
        url = urljoin(SITE, href)
        if name and "remover" not in name.casefold() and (url, name) not in output:
            output.append((url, name))
    return output


def shades(raw: bytes) -> list[tuple[str | None, str | None]]:
    text = raw.decode("utf-8", errors="replace")
    match = re.search(r"const colorGroups = (\[.*?\]);\s*$", text, re.M)
    if not match:
        return [(None, None)]
    groups = json.loads(match.group(1))
    values = []
    for item in groups:
        shade = (item.get("item_color_name_en") or item.get("item_color_name") or "").strip() or None
        sku = (item.get("item_color_sku") or item.get("item_color_sku_nail") or "").strip() or None
        values.append((shade, sku))
    return values or [(None, None)]


def safe(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def main() -> None:
    selected = {}
    category_raw = []
    for category, paths in CATEGORY_PAGES.items():
        links = []
        for path in paths:
            raw = get(urljoin(SITE, path))
            category_raw.append(raw)
            for value in product_links(raw):
                if value not in links:
                    links.append(value)
                if len(links) == 3:
                    break
            if len(links) == 3:
                break
        selected[category] = links[:3]
    all_links = {url for links in selected.values() for url, _ in links}
    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = dict(zip(all_links, pool.map(get, all_links)))
    digest = hashlib.sha256(b"".join(category_raw) + b"".join(pages[url] for url in sorted(pages))).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    imported = []
    for category, links in selected.items():
        for url, name in links:
            for shade, sku in shades(pages[url]):
                # A few official pages reuse a SKU across adjacent shades, so
                # shade text is part of the stable catalog identity.
                identifier = safe(f"{sku or name}-{shade or 'default'}")
                evidence = {"name": name}
                if shade:
                    evidence["shade"] = shade
                imported.append({
                    "id": f"canmake-{category}-{identifier}", "brand": "CANMAKE",
                    "name": name, "shade": shade, "market": "日本", "version": None,
                    "category": category, "aliases": [], "source_url": url,
                    "verified_at": now, "verification_status": "official_listing_observed",
                    "source_kind": "official_site", "store_identity": "CANMAKE",
                    "selection_basis": "official_category_page_first_3", "feed_sha256": digest,
                    "evidence": evidence,
                })
    existing = json.loads(CATALOG.read_text())
    merged = [item for item in existing if not (item.get("brand") == "CANMAKE" and item.get("source_kind") == "official_site")]
    merged.extend(imported)
    CATALOG.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    print("CANMAKE", len(imported), "shade records across", sum(bool(v) for v in selected.values()), "categories")
    print("catalog", len(existing), "->", len(merged), "records")


if __name__ == "__main__":
    main()
