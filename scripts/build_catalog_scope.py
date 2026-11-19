"""Build the reviewed first-pass brand coverage scope.

Priority is a collection order, not a claimed sales rank. Public social data
uses different metrics, so its original metric and period are kept in sources.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path


USER_SEEDS = {
    "L'Oreal Paris", "Lancome", "YSL Beauty", "Chanel", "Givenchy Beauty",
    "Bobbi Brown", "CANMAKE", "KATE", "Excel", "Flower Knows",
    "Perfect Diary", "Judydoll", "Sephora Collection", "MAOGEPING", "MAC",
}

SCOPES = {
    "base": [
        "NARS", "Estee Lauder", "TIMAGE", "Shu Uemura", "Lancome",
        "Charlotte Tilbury", "YSL Beauty", "Maybelline", "L'Oreal Paris", "Prada Beauty",
        "Cle de Peau Beaute", "Florasis", "MAOGEPING", "Givenchy Beauty", "Chanel",
        "Bobbi Brown", "MAC", "Make Up For Ever", "Carslan", "Perfect Diary",
        "Judydoll", "Sephora Collection", "CANMAKE", "KATE", "Excel",
        "Flower Knows", "Dior Beauty", "Armani Beauty", "Hourglass", "Fenty Beauty",
    ],
    "brow": [
        "SanZiTang", "Shu Uemura", "OETE", "Sephora Collection", "Judydoll",
        "UNNY CLUB", "Kiss Me Heroine Make", "Little Ondine", "TIMAGE", "KATE",
        "Excel", "CANMAKE", "MAC", "Bobbi Brown", "Anastasia Beverly Hills",
        "Benefit", "L'Oreal Paris", "Maybelline", "Perfect Diary", "Flower Knows",
        "MAOGEPING", "Chanel", "Dior Beauty", "YSL Beauty", "Givenchy Beauty",
        "Lancome", "Florasis", "Etude", "ColourPop", "NYX Professional Makeup",
    ],
    "eyeshadow": [
        "Urban Decay", "Judydoll", "3CE", "Charlotte Tilbury", "Tom Ford Beauty",
        "MAOGEPING", "Perfect Diary", "MUKK", "MAC", "Flower Knows",
        "Chanel", "Dior Beauty", "YSL Beauty", "Givenchy Beauty", "Lancome",
        "Bobbi Brown", "CANMAKE", "KATE", "Excel", "Sephora Collection",
        "NARS", "Natasha Denona", "ColourPop", "Huda Beauty", "Pat McGrath Labs",
        "Tarte", "Too Faced", "Etude", "Dasique", "Rom&nd",
    ],
    "eyeliner": [
        "Judydoll", "UNNY CLUB", "UZU", "Little Ondine", "Love Liner",
        "VEECCI", "KATE", "CANMAKE", "Excel", "Flower Knows",
        "Perfect Diary", "Sephora Collection", "MAC", "Bobbi Brown", "Chanel",
        "Dior Beauty", "YSL Beauty", "Givenchy Beauty", "Lancome", "L'Oreal Paris",
        "Maybelline", "Kiss Me Heroine Make", "Stila", "Urban Decay", "3CE",
        "Florasis", "MAOGEPING", "NYX Professional Makeup", "Clio", "Etude",
    ],
    "mascara": [
        "SanZiTang", "UNNY CLUB", "Judydoll", "Kiss Me Heroine Make", "AKF",
        "Maybelline", "KATE", "CANMAKE", "Excel", "Flower Knows",
        "Perfect Diary", "Sephora Collection", "MAC", "Bobbi Brown", "Chanel",
        "Dior Beauty", "YSL Beauty", "Givenchy Beauty", "Lancome", "L'Oreal Paris",
        "Benefit", "Too Faced", "Clinique", "Etude", "Clio",
        "Rom&nd", "MAOGEPING", "Florasis", "Little Ondine", "Hourglass",
    ],
    "contour": [
        "Judydoll", "TIMAGE", "OUT-OF-OFFICE", "Flower Knows", "FOREVER KEY",
        "MAC", "MUKK", "Fenty Beauty", "MAOGEPING", "CANMAKE",
        "KATE", "Excel", "Sephora Collection", "Bobbi Brown", "Chanel",
        "Dior Beauty", "YSL Beauty", "Givenchy Beauty", "Lancome", "L'Oreal Paris",
        "Perfect Diary", "NARS", "Charlotte Tilbury", "Too Cool For School", "Kaleidos",
        "NYX Professional Makeup", "Makeup by Mario", "Westman Atelier", "Rare Beauty", "Hourglass",
    ],
    "highlighter": [
        "Shishinyu", "MAOGEPING", "Yuanqi Formula", "TIMAGE", "Little Ondine",
        "MAC", "Red Chamber", "Girlcult", "Judydoll", "Flower Knows",
        "CANMAKE", "KATE", "Excel", "Sephora Collection", "Bobbi Brown",
        "Chanel", "Dior Beauty", "YSL Beauty", "Givenchy Beauty", "Lancome",
        "L'Oreal Paris", "Perfect Diary", "NARS", "Charlotte Tilbury", "Fenty Beauty",
        "Rare Beauty", "Hourglass", "Benefit", "Florasis", "3CE",
    ],
    "blush": [
        "FOREVER KEY", "MAOGEPING", "Red Chamber", "NARS", "TIMAGE",
        "Flower Knows", "3CE", "Yuanqi Formula", "Chanel", "Dior Beauty",
        "YSL Beauty", "Givenchy Beauty", "Lancome", "L'Oreal Paris", "Bobbi Brown",
        "CANMAKE", "KATE", "Excel", "Perfect Diary", "Judydoll",
        "Sephora Collection", "MAC", "Rare Beauty", "Hourglass", "Clinique",
        "Benefit", "Armani Beauty", "Charlotte Tilbury", "Gucci Beauty", "Fenty Beauty",
    ],
    "lip": [
        "Estee Lauder", "YSL Beauty", "Lancome", "Dior Beauty", "MAC",
        "3CE", "Tom Ford Beauty", "NARS", "Charlotte Tilbury", "Armani Beauty",
        "FOREVER KEY", "MUKK", "Chanel", "INTO YOU", "UNNY CLUB",
        "Givenchy Beauty", "L'Oreal Paris", "Bobbi Brown", "CANMAKE", "KATE",
        "Excel", "Flower Knows", "Perfect Diary", "Judydoll", "Sephora Collection",
        "MAOGEPING", "Joocyee", "Rom&nd", "Florasis", "Maybelline",
    ],
}

XHS_2025 = {
    "base": {"NARS", "Estee Lauder", "TIMAGE", "Shu Uemura", "Lancome", "Charlotte Tilbury", "YSL Beauty", "Maybelline", "L'Oreal Paris", "Prada Beauty", "Cle de Peau Beaute", "Florasis", "MAOGEPING", "3CE", "KATO", "MAC", "Chanel", "Perfect Diary", "Urban Decay", "Givenchy Beauty"},
    "brow": {"SanZiTang", "Shu Uemura", "OETE", "Sephora Collection", "Judydoll", "UNNY CLUB", "Kiss Me Heroine Make", "Little Ondine", "TIMAGE"},
    "eyeshadow": {"Urban Decay", "Judydoll", "3CE", "Charlotte Tilbury", "Tom Ford Beauty", "MAOGEPING", "Perfect Diary", "MUKK", "MAC"},
    "eyeliner": {"Judydoll", "UNNY CLUB", "UZU", "Little Ondine", "Love Liner", "VEECCI"},
    "mascara": {"SanZiTang", "UNNY CLUB", "Judydoll", "Kiss Me Heroine Make", "AKF", "Maybelline", "Little Ondine"},
    "contour": {"Judydoll", "TIMAGE", "OUT-OF-OFFICE", "Flower Knows", "FOREVER KEY", "MAC", "MUKK", "Fenty Beauty", "MAOGEPING"},
    "highlighter": {"Shishinyu", "MAOGEPING", "Yuanqi Formula", "TIMAGE", "Little Ondine", "MAC", "Red Chamber", "Girlcult"},
    "blush": {"FOREVER KEY", "MAOGEPING", "Red Chamber", "NARS", "TIMAGE", "Flower Knows", "3CE", "Yuanqi Formula"},
    "lip": {"Estee Lauder", "YSL Beauty", "Lancome", "Dior Beauty", "MAC", "3CE", "Tom Ford Beauty", "NARS", "Charlotte Tilbury", "Armani Beauty", "FOREVER KEY", "MUKK", "Chanel", "INTO YOU", "UNNY CLUB"},
}

SOURCES = {
    "user_seed": {"metric": "用户指定优先覆盖", "observed_at": str(date.today())},
    "xhs_2025": {
        "title": "小红书 2025 彩妆行业趋势灵感图鉴",
        "metric": "灵犀库单品人群资质榜/热度洞察，非销量",
        "period": "数据截至 2025-01",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202511281790090499_1.pdf?1764364104000.pdf=",
    },
    "douyin_2025": {
        "title": "2025 上半年飞瓜抖音电商与广告投放报告及年度行业报道",
        "metric": "第三方估算销售热度，非抖音官方销量",
        "period": "2025",
        "url": "https://data.eastmoney.com/report/zw_industry.jshtml?infocode=AP202508051721763168",
    },
}


def main() -> None:
    assert set(SCOPES) == {"base", "brow", "eyeshadow", "eyeliner", "mascara", "contour", "highlighter", "blush", "lip"}
    categories = {}
    for category, brands in SCOPES.items():
        assert len(brands) == 30, (category, len(brands))
        assert len(set(brands)) == 30, f"duplicate in {category}"
        categories[category] = [
            {
                "priority": rank,
                "brand": brand,
                "signals": [
                    *(["user_seed"] if brand in USER_SEEDS else []),
                    *(["xhs_2025"] if brand in XHS_2025.get(category, set()) else []),
                    "douyin_2025_or_category_portfolio",
                ],
                "target_product_lines": 3,
                "status": "pending_collection",
            }
            for rank, brand in enumerate(brands, 1)
        ]
    payload = {
        "schema_version": 1,
        "generated_at": str(date.today()),
        "ranking_note": "priority 是综合采集顺序，不宣称为单一平台销量名次。",
        "source_policy": ["official_site", "official_flagship_tmall", "official_flagship_taobao", "authorized_retailer_sephora"],
        "shade_policy": "每个入选产品系列采集来源页面公开列出的全部色号，不推断缺失色号。",
        "sources": SOURCES,
        "categories": categories,
    }
    Path("data/catalog_scope.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {sum(map(len, categories.values()))} category-brand targets")


if __name__ == "__main__":
    main()
