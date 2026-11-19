"""Collect full-size product lines and public shade options from flagship stores.

This follows category/brand targets that already have a Taobao flagship-store
identity. Search cards are only candidates: every retained item is opened and
the store identity, product parameters, net content and current SKU shade list
are read from the detail page. Samples and trial listings are rejected.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from playwright.sync_api import sync_playwright

from collect_taobao_rankings import PROFILE, extract_cards, signed_in


ROOT = Path(__file__).resolve().parent.parent
SCOPE = ROOT / "data/catalog_scope.json"
OUTPUT = ROOT / "data/taobao_products.json"
DEBUG = ROOT / "outputs/taobao-products"

CATEGORY_QUERY = {
    "base": "粉底液 底妆",
    "brow": "眉笔 眉粉",
    "eyeshadow": "眼影 眼影盘",
    "eyeliner": "眼线笔 眼线液",
    "mascara": "睫毛膏 睫毛打底",
    "contour": "修容 阴影",
    "highlighter": "高光 提亮",
    "blush": "腮红 胭脂",
    "lip": "口红 唇釉",
}
CATEGORY_TERMS = {
    "base": ("粉底", "气垫", "粉霜", "粉膏", "素颜霜", "隔离"),
    "brow": ("眉笔", "眉粉", "眉膏"),
    "eyeshadow": ("眼影",),
    "eyeliner": ("眼线",),
    "mascara": ("睫毛",),
    "contour": ("修容", "阴影", "古铜"),
    "highlighter": ("高光", "提亮"),
    "blush": ("腮红", "胭脂"),
    "lip": ("口红", "唇膏", "唇釉", "唇泥", "唇彩", "染唇"),
}
EXCLUDED = (
    "小样", "试用", "尝鲜", "赠品", "空盒", "替换装", "分装", "体验装",
    "旅行装", "试色卡", "派样", "联包", "回购券", "天猫u先", "u先",
    "礼盒", "套装", "套组", "组合装",
)
SHADE_NOISE = {
    "切换大图模式", "店长主推", "新品", "热卖", "爆款", "推荐",
    "净含量", "数量", "有货", "加入购物车", "领券购买", "收藏",
}
GENERIC_PRODUCT_NAMES = {
    "粉底液", "全新粉底液", "气垫", "全新气垫", "眼线笔", "眼线液笔",
    "底妆", "彩妆", "口红", "唇釉",
}


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def item_id(url: str) -> str | None:
    return (parse_qs(urlparse(url).query).get("id") or [None])[0]


def is_full_size_candidate(title: str, category: str) -> bool:
    compact = normalize(title)
    return (
        bool(title)
        and not any(normalize(word) in compact for word in EXCLUDED)
        and any(normalize(word) in compact for word in CATEGORY_TERMS[category])
    )


def parameter_value(body: str, labels: tuple[str, ...]) -> str | None:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for index, line in enumerate(lines[:-1]):
        if any(label in line for label in labels):
            value = lines[index + 1]
            if len(value) <= 160 and value not in labels:
                return value
    return None


def extract_product_name(body: str, page_title: str, listing_title: str) -> str:
    labels = (
        "粉底液/膏单品", "隔离霜单品", "气垫单品", "眉笔/眉粉/眉膏单品",
        "眼影单品", "眼线单品", "睫毛膏单品", "修容单品", "高光单品",
        "腮红/胭脂单品", "唇膏/口红单品", "唇彩/唇蜜/唇釉单品",
    )
    name = parameter_value(body, labels)
    if name and normalize(name) not in {normalize(value) for value in GENERIC_PRODUCT_NAMES}:
        return name
    title = re.sub(r"-tmall\.com天猫$|-淘宝网$", "", page_title).strip()
    title = re.sub(r"^【[^】]{1,30}】", "", title).strip()
    return title or listing_title.strip()


def extract_shades(body: str) -> list[str]:
    marker = "\n颜色分类\n"
    start = body.rfind(marker)
    if start < 0:
        return []
    block = body[start + len(marker):]
    ends = [block.find(token) for token in ("\n净含量\n", "\n数量\n", "\n加入购物车")]
    ends = [value for value in ends if value >= 0]
    if ends:
        block = block[:min(ends)]
    result = []
    for raw in block.splitlines():
        value = re.sub(r"\s+", " ", raw).strip()
        if not value or value.casefold() in {x.casefold() for x in SHADE_NOISE}:
            continue
        if re.fullmatch(r"(?:千|万)?[人件]+加购|已售.*|有货.*", value):
            continue
        if len(value) > 100 or value in result:
            continue
        result.append(value)
    return result


def wait_for_manual_challenge(page, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    announced = False
    while time.monotonic() < deadline:
        title = page.title()
        body = page.locator("body").inner_text(timeout=10000)
        challenged = "CAPTCHA Verification" in title or "Please slide to verify" in body or "滑块" in body
        if not challenged:
            return
        if not announced:
            print("请在浏览器中完成淘宝滑块验证，脚本正在等待…", flush=True)
            announced = True
        page.wait_for_timeout(1500)
    raise TimeoutError("等待淘宝滑块验证超时")


def safe_extract_cards(page) -> list[dict]:
    """Return no cards while Taobao replaces the search document.

    Login/challenge completion can navigate or reload the page between the
    browser call and JavaScript evaluation. That is a transient state rather
    than a failed target, so the headed wait loop should keep polling.
    """
    try:
        return extract_cards(page)
    except Exception as exc:
        message = str(exc).casefold()
        if "execution context was destroyed" in message or "navigation" in message:
            return []
        raise


def detail_record(page, card: dict, category: str, expected_store: str, interactive: bool = False) -> dict | None:
    page.goto(card["item_url"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2800)
    if interactive:
        wait_for_manual_challenge(page)
    body = page.locator("body").inner_text(timeout=15000)
    if normalize(expected_store) not in normalize(body):
        return None
    title = re.sub(r"\s+", " ", card["title"]).strip()
    if not is_full_size_candidate(title, category) or any(word in normalize(body) for word in ("商品已失效", "商品不存在")):
        return None
    current_url = page.url
    identifier = item_id(current_url) or item_id(card["item_url"])
    if not identifier:
        return None
    net_content = parameter_value(body, ("净含量",))
    product_name = extract_product_name(body, page.title(), title)
    shades = extract_shades(body)
    return {
        "item_id": identifier,
        "category": category,
        "listing_title": title,
        "product_name": product_name,
        "store_name": expected_store,
        "item_url": current_url,
        "sales_text": card.get("sales_text"),
        "net_content": net_content,
        "shades": shades,
        "shade_count": len(shades),
        "shade_collection_basis": "all currently visible 颜色分类 options on the verified detail page",
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def load_payload() -> dict:
    if OUTPUT.exists():
        return json.loads(OUTPUT.read_text())
    return {"schema_version": 1, "updated_at": None, "targets": []}


def save_payload(payload: dict) -> None:
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def collect(max_pages: int, max_lines: int, limit_targets: int | None, headed: bool) -> None:
    if not PROFILE.exists():
        raise RuntimeError("尚无淘宝登录状态，请先运行 collect_taobao_rankings.py --login")
    scope = json.loads(SCOPE.read_text())
    targets = []
    for category, group in scope["categories"].items():
        for target in group["targets"]:
            if target.get("taobao") and target.get("status") != "ingested":
                targets.append((category, target))
    if limit_targets:
        targets = targets[:limit_targets]
    payload = load_payload()
    by_key = {(row["category"], row["brand"].casefold()): row for row in payload["targets"]}
    DEBUG.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE), headless=not headed, viewport={"width": 1440, "height": 1200}
        )
        if not signed_in(context):
            context.close()
            raise RuntimeError("淘宝登录状态已失效，请重新运行 collect_taobao_rankings.py --login")
        search_page = context.pages[0] if context.pages else context.new_page()
        detail_page = context.new_page()
        for position, (category, target) in enumerate(targets, 1):
            store = target["taobao"]["store_name"]
            store_brand = re.sub(r"(?:品牌)?官方海外旗舰店|海外官方旗舰店|官方旗舰店|旗舰店$", "", store).strip()
            query = f"{store_brand} {CATEGORY_QUERY[category]} 官方旗舰店 正装"
            cards: list[dict] = []
            seen_ids = set()
            for page_index in range(max_pages):
                url = f"https://s.taobao.com/search?q={quote(query)}&s={page_index * 48}"
                search_page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    search_page.wait_for_selector('[class*="shopName"]', timeout=18000)
                except Exception:
                    if headed:
                        print("搜索结果尚未加载；请在浏览器完成验证或刷新页面，脚本等待两分钟…", flush=True)
                        deadline = time.monotonic() + 120
                        while time.monotonic() < deadline and not safe_extract_cards(search_page):
                            search_page.wait_for_timeout(1500)
                        if not safe_extract_cards(search_page):
                            search_page.screenshot(path=str(DEBUG / f"{category}-{position}-failed.png"), full_page=True)
                            break
                    else:
                        search_page.screenshot(path=str(DEBUG / f"{category}-{position}-failed.png"), full_page=True)
                        break
                search_page.mouse.wheel(0, 4500)
                search_page.wait_for_timeout(1200)
                for card in safe_extract_cards(search_page):
                    identifier = item_id(card["item_url"])
                    if (
                        identifier and identifier not in seen_ids
                        and normalize(card["shop_name"]) == normalize(store)
                        and is_full_size_candidate(card["title"], category)
                    ):
                        seen_ids.add(identifier)
                        cards.append(card)
            products = []
            line_names = set()
            for card in cards[:12]:
                try:
                    record = detail_record(detail_page, card, category, store, interactive=headed)
                except Exception as exc:
                    print(" detail failed", item_id(card["item_url"]), type(exc).__name__)
                    continue
                if not record:
                    continue
                line_key = normalize(record["product_name"])
                if line_key in line_names:
                    continue
                shade_keys = {normalize(value) for value in record["shades"]}
                duplicate_variant_set = False
                for saved in products:
                    saved_keys = {normalize(value) for value in saved["shades"]}
                    union = shade_keys | saved_keys
                    if union and len(shade_keys & saved_keys) / len(union) >= 0.8:
                        duplicate_variant_set = True
                        break
                if duplicate_variant_set:
                    continue
                line_names.add(line_key)
                products.append(record)
                if len(products) >= max_lines:
                    break
                time.sleep(0.4)
            row = {
                "category": category,
                "brand": target["brand"],
                "store_name": store,
                "query": query,
                "candidate_count": len(cards),
                "products": products,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
            by_key[(category, target["brand"].casefold())] = row
            payload["targets"] = list(by_key.values())
            save_payload(payload)
            print(f"[{position}/{len(targets)}]", category, target["brand"], len(products), "lines", sum(p["shade_count"] for p in products), "shades", flush=True)
        context.close()
    save_payload(payload)
    print("wrote", OUTPUT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--max-lines", type=int, default=3)
    parser.add_argument("--limit-targets", type=int)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    collect(max(1, min(args.max_pages, 8)), max(1, min(args.max_lines, 3)), args.limit_targets, args.headed)


if __name__ == "__main__":
    main()
