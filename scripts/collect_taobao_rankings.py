"""Collect sales-sorted Taobao results from verified flagship-store listings.

Taobao renders results behind a signed-in browser session. Run ``--login`` once
in a visible browser, then ``--collect`` reuses that local session. The output
keeps raw rank, shop name, sales text, item URL, query and collection time.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "runtime/taobao-browser-profile"
OUTPUT = ROOT / "data/taobao_rankings.json"
DEBUG = ROOT / "outputs/taobao-ranking"
QUERIES = {
    "base": "底妆 粉底液",
    "brow": "眉笔 眉粉",
    "eyeshadow": "眼影 眼影盘",
    "eyeliner": "眼线笔 眼线液",
    "mascara": "睫毛膏 睫毛打底",
    "contour": "修容 阴影",
    "highlighter": "高光 提亮",
    "blush": "腮红 胭脂",
    "lip": "口红 唇釉",
}


def signed_in(context) -> bool:
    names = {cookie["name"] for cookie in context.cookies("https://taobao.com")}
    # cookie2 may be created before the QR/account flow is actually complete.
    return bool(names & {"tracknick", "unb", "lgc", "_nk_"})


def search_session_ready(page) -> bool:
    """Check the rendered result instead of trusting stale login cookies."""
    try:
        body = page.locator("body").inner_text(timeout=5000)
        return "亲，请登录" not in body and bool(extract_cards(page))
    except Exception:
        return False


def login() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(PROFILE), headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        probe = "https://s.taobao.com/search?q=%E5%8F%A3%E7%BA%A2"
        page.goto(probe, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if search_session_ready(page):
            print("Taobao session is already usable:", PROFILE)
            context.close()
            return
        # Old cookies can survive after Taobao has invalidated the session.
        context.clear_cookies()
        page.goto("https://login.taobao.com/member/login.jhtml", wait_until="domcontentloaded")
        print("请在打开的淘宝窗口登录；登录后脚本会验证搜索结果并自动关闭。", flush=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and not signed_in(context):
            page.wait_for_timeout(1000)
        if not signed_in(context):
            raise TimeoutError("五分钟内未检测到淘宝登录状态")
        page.goto(probe, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        if not search_session_ready(page):
            raise RuntimeError("登录 Cookie 已生成，但淘宝搜索结果仍不可用；请稍后再次登录")
        print("Taobao session saved:", PROFILE)
        context.close()


def brand_from_store(store: str) -> str:
    value = re.sub(r"(?:品牌)?官方海外旗舰店|海外官方旗舰店|官方旗舰店|旗舰店$", "", store).strip()
    return value or store.strip()


def extract_cards(page) -> list[dict]:
    return page.evaluate("""
    () => {
      const roots = Array.from(document.querySelectorAll(
        '[class*="doubleCardWrapper"], [class*="Card--"], [data-name="item"]'
      ));
      const seen = new Set();
      const rows = [];
      for (const root of roots) {
        const link = root.matches('a[href*="item.taobao.com"],a[href*="detail.tmall.com"],a[href*="item.htm"]')
          ? root
          : root.querySelector('a[href*="item.taobao.com"],a[href*="detail.tmall.com"],a[href*="item.htm"]');
        if (!link || seen.has(link.href)) continue;
        seen.add(link.href);
        const shop = root.querySelector('[class*="shopNameText"]') || root.querySelector('[class*="shopName"]');
        const sales = root.querySelector('[class*="realSales"]');
        const title = root.querySelector('[class*="title"]');
        rows.push({
          item_url: link.href,
          title: (title?.innerText || link.innerText || '').trim(),
          shop_name: (shop?.innerText || '').trim(),
          sales_text: (sales?.innerText || '').trim(),
        });
      }
      return rows;
    }
    """)


def collect(max_pages: int, only_category: str | None = None) -> None:
    if not PROFILE.exists():
        raise RuntimeError("尚无淘宝登录状态，请先运行 --login")
    DEBUG.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "metric": "Taobao search results ordered by sales; only Tmall/Taobao store names containing 旗舰店 are retained",
        "categories": {},
    }
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(PROFILE), headless=True, viewport={"width": 1440, "height": 1200})
        if not signed_in(context):
            context.close()
            raise RuntimeError("淘宝登录状态已失效，请重新运行 --login")
        page = context.pages[0] if context.pages else context.new_page()
        selected_queries = {only_category: QUERIES[only_category]} if only_category else QUERIES
        for category, query in selected_queries.items():
            brands: dict[str, dict] = {}
            raw_position = 0
            for page_index in range(max_pages):
                offset = page_index * 48
                url = f"https://s.taobao.com/search?q={quote(query)}&sort=sale-desc&s={offset}"
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_selector('[class*="realSales"],[class*="shopName"]', timeout=20000)
                except Exception:
                    page.screenshot(path=str(DEBUG / f"{category}-{page_index + 1}-failed.png"), full_page=True)
                    break
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1500)
                cards = extract_cards(page)
                for card in cards:
                    raw_position += 1
                    store = card["shop_name"]
                    if "旗舰店" not in store:
                        continue
                    brand = brand_from_store(store)
                    if brand not in brands:
                        brands[brand] = {
                            "rank": len(brands) + 1,
                            "raw_result_position": raw_position,
                            "brand": brand,
                            "store_name": store,
                            **card,
                            "query": query,
                            "source_url": url,
                        }
                    if len(brands) >= 30:
                        break
                if len(brands) >= 30:
                    break
            payload["categories"][category] = list(brands.values())[:30]
            print(category, len(brands), "verified flagship brands")
        context.close()
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print("wrote", OUTPUT)


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--login", action="store_true")
    group.add_argument("--collect", action="store_true")
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--category", choices=sorted(QUERIES))
    args = parser.parse_args()
    login() if args.login else collect(max(1, min(args.max_pages, 20)), args.category)


if __name__ == "__main__":
    main()
