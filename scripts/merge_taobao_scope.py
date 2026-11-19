"""Merge captured Taobao flagship rankings with user seeds and research scope."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCOPE = ROOT / "data/catalog_scope.json"
RANKINGS = ROOT / "data/taobao_rankings.json"

ALIASES = {
    "BOBBI BROWN芭比波朗": "Bobbi Brown", "Lancome兰蔻": "Lancome",
    "欧莱雅彩妆": "L'Oreal Paris", "Estee Lauder雅诗兰黛": "Estee Lauder",
    "美宝莲": "Maybelline", "MAOGEPING毛戈平": "MAOGEPING",
    "shu uemura植村秀": "Shu Uemura", "花西子": "Florasis",
    "GIORGIO ARMANI阿玛尼美妆": "Armani Beauty", "MAC魅可": "MAC",
    "YSL圣罗兰美妆": "YSL Beauty", "DIOR迪奥 香水与美容品": "Dior Beauty",
    "JUDYDOLL橘朵": "Judydoll", "perfectdiary": "Perfect Diary",
    "KATE海外": "KATE", "CANMAKE海外": "CANMAKE", "KISSME奇士美": "Kiss Me Heroine Make",
    "UNNYCLUB": "UNNY CLUB", "3CE": "3CE", "Joocyee酵色": "Joocyee",
    "toocoolforschool": "Too Cool For School", "FOREVERKEY": "FOREVER KEY",
    "outofoffice": "OUT-OF-OFFICE", "girlcult": "Girlcult",
    "FLORTTE花洛莉亚": "FLORTTE", "Mooekiss木柯诗": "MUKK",
    "COLORGRAM海外": "COLORGRAM", "无印良品MUJI": "MUJI",
}


def canonical(value: str) -> str:
    value = value.strip()
    return ALIASES.get(value, re.sub(r"(?:品牌)?海外$", "", value).strip())


def main() -> None:
    scope = json.loads(SCOPE.read_text())
    rankings = json.loads(RANKINGS.read_text())
    old_categories = scope["categories"]
    merged_categories = {}
    for category, old_rows in old_categories.items():
        ranked = []
        seen = set()
        evidence = {}
        for raw in rankings["categories"].get(category, []):
            brand = canonical(raw["brand"])
            key = brand.casefold()
            if key in seen:
                continue
            seen.add(key)
            evidence[key] = raw
            ranked.append(brand)
        # Public Taobao results may expose fewer than 30 flagship brands. Fill
        # the remaining target slots from the sourced social/category scope.
        for row in old_rows:
            brand = row["brand"]
            if brand.casefold() not in seen:
                seen.add(brand.casefold())
                ranked.append(brand)
            if len(ranked) == 30:
                break
        ranked = ranked[:30]
        user_seeds = [row["brand"] for row in old_rows if "user_seed" in row.get("signals", [])]
        targets = []
        for brand in [*ranked, *user_seeds]:
            if any(row["brand"].casefold() == brand.casefold() for row in targets):
                continue
            raw = evidence.get(brand.casefold())
            rank = ranked.index(brand) + 1 if brand in ranked else None
            targets.append({
                "brand": brand,
                "priority": len(targets) + 1,
                "ranking_position": rank,
                "ranking_basis": "taobao_sales_sorted_flagship_result" if raw else "social_or_category_portfolio_fill",
                "user_seed": brand in user_seeds,
                "target_product_lines": 3,
                "status": "pending_collection",
                **({"taobao": {
                    "store_name": raw["store_name"], "item_url": raw["item_url"],
                    "sales_text": raw["sales_text"], "raw_result_position": raw["raw_result_position"],
                    "query": raw["query"], "captured_at": rankings["collected_at"],
                }} if raw else {}),
            })
        merged_categories[category] = {
            "ranked_target_count": 30,
            "user_seed_count": len(user_seeds),
            "collection_target_count": len(targets),
            "targets": targets,
        }
    scope["schema_version"] = 2
    scope["ranking_note"] = "每类前30为淘宝销量排序旗舰店结果与公开榜单补位；用户指定品牌额外保留。淘宝结果受广告、个性化和公开页可见性影响。"
    scope["sources"]["taobao_sales_search"] = {
        "title": "淘宝销量排序搜索页",
        "metric": rankings["metric"],
        "captured_at": rankings["collected_at"],
        "url": "https://s.taobao.com/search",
    }
    scope["categories"] = merged_categories
    SCOPE.write_text(json.dumps(scope, ensure_ascii=False, indent=2) + "\n")
    for category, data in merged_categories.items():
        print(category, data["collection_target_count"], "targets", data["user_seed_count"], "user seeds")


if __name__ == "__main__":
    main()
