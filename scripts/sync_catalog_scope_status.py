"""Annotate collection targets with current catalog coverage."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    scope_path = ROOT / "data/catalog_scope.json"
    catalog = json.loads((ROOT / "data/product_catalog.json").read_text())
    scope = json.loads(scope_path.read_text())
    coverage = defaultdict(list)
    for item in catalog:
        coverage[item["brand"].casefold()].append(item)
    for category, group in scope["categories"].items():
        for target in group["targets"]:
            items = [item for item in coverage[target["brand"].casefold()] if item["category"] == category]
            target["shade_record_count"] = len(items)
            target["product_line_count"] = len({item["name"] for item in items})
            if items:
                target["status"] = "ingested"
            elif target.get("taobao"):
                target["status"] = "candidate_listing_pending_review"
            else:
                target["status"] = "pending_collection"
    scope_path.write_text(json.dumps(scope, ensure_ascii=False, indent=2) + "\n")
    ingested = sum(target["status"] == "ingested" for group in scope["categories"].values() for target in group["targets"])
    total = sum(len(group["targets"]) for group in scope["categories"].values())
    print("coverage", ingested, "/", total, "category-brand targets")


if __name__ == "__main__":
    main()
