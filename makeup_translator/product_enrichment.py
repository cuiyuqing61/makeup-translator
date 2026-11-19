from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from openai import OpenAI
from pydantic import ValidationError

from .product_models import (
    ProductEnrichment,
    ProductRecord,
    SearchAttempt,
    SearchAudit,
    SearchBudget,
    ProductSource,
    SourceType,
    SourceValidation,
)
from .providers import _extract_json


PRODUCT_SEARCH_SYSTEM_PROMPT = """你是美妆产品资料核验工具。你的任务是根据用户原始输入联网查找产品，
优先使用品牌官网或品牌官方旗舰店，并将官方事实与第三方测评严格分开。禁止猜测不存在的色号、
质地、成分和功效。每个关键字段必须能由 sources 中的来源支持；无法确认时写入 unresolved_fields。
只输出一个 JSON 对象，不输出 Markdown。"""


PRODUCT_SEARCH_USER_PROMPT = """请强制联网搜索下面的美妆产品，并输出待用户确认的产品候选。

来源规则：
1. 首选品牌官网具体产品页，不要把搜索结果页当官网产品页。
2. 找不到官网时可使用品牌官方旗舰店，但 source_type 必须是 official_store。
3. 零售商只能辅助确认规格；测评只能写入 review_observations。
4. 不得把测评主观感受写成官方事实。
5. official_url 如非 null，必须同时完整出现在 sources 中。
6. URL 只能原样复制搜索工具实际返回的链接，禁止根据标题或商品ID自行拼接链接。

严格输出：
{
  "match_status": "exact_match|likely_match|ambiguous",
  "canonical_brand": "标准品牌名",
  "canonical_name": "标准产品名",
  "product_line": "系列名或null",
  "model_or_shade": "型号/色号或null",
  "market": "地区版本或null",
  "category": "base|brow|eyeshadow|eyeliner|mascara|contour|highlighter|blush|lip|other",
  "color_family": ["颜色家族，只在来源支持时填写"],
  "finish": ["matte|satin|shimmer|glossy|velvet等，只在来源支持时填写"],
  "official_description": "官方信息概括或null",
  "official_usage": "官方使用方式或null",
  "official_url": "官网产品页URL或null",
  "review_observations": ["带有限定语的测评观察"],
  "unresolved_fields": ["尚不能确认的字段"],
  "conflicts": ["来源冲突"],
  "alternatives": [
    {
      "brand": "品牌",
      "product_name": "产品名",
      "model_or_shade": "型号/色号或null",
      "market": "地区或null",
      "url": "候选链接",
      "difference": "与主要候选的差别"
    }
  ],
  "sources": [
    {
      "title": "来源标题",
      "url": "完整http或https链接",
      "source_type": "official|official_store|retailer|review|other",
      "supports_fields": ["该来源实际支持的字段名"],
      "note": "必要说明或null"
    }
  ]
}

若不能唯一定位，match_status 必须为 ambiguous，并给出 alternatives；仍需选择最可能的主候选，
但不得把它写成已经确认。"""


class SearchDeadlineExceeded(RuntimeError):
    pass


class SearchController:
    """Deterministic call counter and wall-clock deadline for the search harness."""

    _LIMIT_FIELDS = {
        "identity_search": "qwen_identity_search_calls",
        "review_search": "qwen_review_search_calls",
        "extraction": "qwen_extraction_calls",
        "schema_repair": "schema_repair_calls",
    }

    def __init__(self, budget: SearchBudget):
        self.budget = budget
        self.started = time.monotonic()
        self._counts = {stage: 0 for stage in self._LIMIT_FIELDS}
        self._attempts: list[SearchAttempt] = []
        self._lock = threading.Lock()

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float:
        return max(0.0, self.budget.hard_deadline_seconds - self.elapsed())

    def claim(self, stage: str) -> None:
        with self._lock:
            if self.remaining() <= 0:
                raise SearchDeadlineExceeded(
                    f"联网补全已达到 {self.budget.hard_deadline_seconds:g} 秒总时限"
                )
            field = self._LIMIT_FIELDS[stage]
            limit = getattr(self.budget, field)
            if self._counts[stage] >= limit:
                raise RuntimeError(f"search budget exhausted for {stage}: limit={limit}")
            self._counts[stage] += 1

    def add_attempt(self, attempt: SearchAttempt) -> None:
        with self._lock:
            self._attempts.append(attempt)

    def audit(self, status: str) -> SearchAudit:
        elapsed_ms = round(self.elapsed() * 1000)
        return SearchAudit(
            status=status,
            elapsed_ms=elapsed_ms,
            crossed_foreground_boundary=(
                self.elapsed() > self.budget.foreground_wait_seconds
            ),
            budget=self.budget,
            attempts=list(self._attempts),
        )


def _normalize_enrichment_payload(payload: dict[str, Any]) -> dict[str, Any]:
    category_aliases = {
        "foundation": "base",
        "concealer": "base",
        "complexion": "base",
        "粉底": "base",
        "底妆": "base",
        "eyebrow": "brow",
        "眉笔": "brow",
        "眉部": "brow",
        "eye shadow": "eyeshadow",
        "眼影": "eyeshadow",
        "eye liner": "eyeliner",
        "眼线": "eyeliner",
        "睫毛膏": "mascara",
        "contour": "contour",
        "bronzer": "contour",
        "修容": "contour",
        "阴影": "contour",
        "highlighter": "highlighter",
        "highlight": "highlighter",
        "高光": "highlighter",
        "提亮": "highlighter",
        "腮红": "blush",
        "lipstick": "lip",
        "lip gloss": "lip",
        "lip tint": "lip",
        "口红": "lip",
        "唇膏": "lip",
        "唇釉": "lip",
    }
    category = str(payload.get("category", "other")).strip().lower().replace("-", " ")
    payload["category"] = category_aliases.get(category, category)

    source_aliases = {
        "brand official": "official",
        "official website": "official",
        "官网": "official",
        "official flagship store": "official_store",
        "官方旗舰店": "official_store",
        "ecommerce": "retailer",
        "e commerce": "retailer",
        "零售商": "retailer",
        "测评": "review",
    }
    for source in payload.get("sources") or []:
        raw = str(source.get("source_type", "other")).strip().lower().replace("-", " ")
        source["source_type"] = source_aliases.get(raw, raw)
    return payload


DEFAULT_DOMAIN_REGISTRY = Path(__file__).resolve().parent.parent / "data" / "official_domains.json"
DEFAULT_SEARCH_BUDGET_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "search_budget.json"
)


def load_search_budget(path: Path = DEFAULT_SEARCH_BUDGET_PATH) -> SearchBudget:
    if not path.exists():
        return SearchBudget()
    return SearchBudget.model_validate_json(path.read_text(encoding="utf-8"))


def _host_matches(host: str, allowed_domains: list[str]) -> bool:
    normalized = host.lower().split(":", 1)[0]
    return any(
        normalized == domain.lower() or normalized.endswith("." + domain.lower())
        for domain in allowed_domains
    )


SOCIAL_DOMAINS = ['xiaohongshu.com', 'douyin.com', 'bilibili.com']


def brand_domains(brand, registry, market=None):
    import unicodedata
    def normalize(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', value).casefold() if c.isalnum())
    aliases = {'欧莱雅': "L'OREAL PARIS", 'loreal': "L'OREAL PARIS", 'ysl': 'YSL BEAUTY', '圣罗兰': '圣罗兰美妆', 'lancomeparis': 'LANCOME'}
    key = normalize(brand)
    candidates = {key, normalize(aliases.get(key, brand))}
    for name, entry in registry.items():
        if isinstance(entry, list):
            if normalize(name) in candidates:
                return entry
        elif candidates & {normalize(n) for n in [name, entry.get('chinese_name') or '', *entry.get('aliases', [])]}:
            regions = entry['regions']
            if market and market in regions:
                return regions[market]
            return list(dict.fromkeys(d for domains in regions.values() for d in domains))
    return []


def _normalized_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl().rstrip("/")


def _search_text(value: Any) -> str:
    return "".join(str(value or "").lower().split())


def reconcile_search_sources(
    payload: dict[str, Any],
    search_results: list[dict[str, Any]],
    domain_registry_path: Path = DEFAULT_DOMAIN_REGISTRY,
) -> dict[str, Any]:
    """Only retain URLs independently returned by DashScope search_info."""
    registry = json.loads(domain_registry_path.read_text(encoding="utf-8"))
    brand = str(payload.get("canonical_brand") or "")
    allowed_domains = brand_domains(brand, registry)
    actual_by_url = {
        _normalized_url(str(item["url"])): item
        for item in search_results
        if item.get("url")
    }
    model_sources = {
        _normalized_url(str(item["url"])): item
        for item in payload.get("sources") or []
        if item.get("url")
    }

    brand_term = _search_text(payload.get("canonical_brand"))
    name_term = _search_text(payload.get("canonical_name"))
    irrelevant_count = 0
    reconciled: list[dict[str, Any]] = []
    for normalized, search_item in actual_by_url.items():
        url = str(search_item["url"])
        host = urlparse(url).hostname or ""
        result_text = _search_text(
            " ".join(
                str(search_item.get(key) or "")
                for key in ("title", "snippet", "content", "url")
            )
        )
        is_official_domain = _host_matches(host, allowed_domains)
        is_identity_relevant = is_official_domain or (
            bool(brand_term) and brand_term in result_text
        ) or (bool(name_term) and name_term in result_text)
        if not is_identity_relevant:
            irrelevant_count += 1
            continue
        claimed = model_sources.get(normalized)
        if claimed:
            source_type = claimed.get("source_type", "other")
            supports_fields = claimed.get("supports_fields") or []
            note = claimed.get("note")
        else:
            source_type = (
                "official"
                if is_official_domain
                else "review"
                if search_item.get("_search_scope") == "review"
                else "other"
            )
            supports_fields = []
            note = "由 DashScope search_info 返回；尚未绑定到具体字段。"
        # A model cannot declare a non-brand domain as an official website.
        if source_type == "official" and not _host_matches(host, allowed_domains):
            source_type = "other"
            note = "模型曾标为官网，但域名不在品牌官方域名表中。"
        if search_item.get('_search_scope') == 'review':
            if not _host_matches(host, SOCIAL_DOMAINS):
                continue
            source_type = 'review'
        if source_type == 'review':
            supports_fields = [f for f in supports_fields if f == 'review_observations']
            note = '公开搜索摘要；未读取完整帖子或观看视频。'
        elif source_type != 'official':
            supports_fields = [f for f in supports_fields if f not in {'official_description', 'official_usage'}]
        reconciled.append(
            {
                "title": str(search_item.get("title") or claimed.get("title") if claimed else search_item.get("title") or url),
                "url": url,
                "image_url": search_item.get("image_url") if str(search_item.get("image_url", "")).startswith("https://") else None,
                "source_type": source_type,
                "supports_fields": supports_fields,
                "note": note,
            }
        )

    rejected_urls = sorted(set(model_sources) - set(actual_by_url))
    warnings = list(payload.get("validation_warnings") or [])
    if irrelevant_count:
        warnings.append(f"已丢弃 {irrelevant_count} 条与品牌或产品身份无关的搜索结果")
    if rejected_urls:
        warnings.append("已丢弃模型自行生成、但未出现在 search_info 中的链接：" + "；".join(rejected_urls))

    official_url = payload.get("official_url")
    if official_url:
        normalized_official = _normalized_url(str(official_url))
        host = urlparse(str(official_url)).hostname or ""
        if normalized_official not in actual_by_url or not _host_matches(host, allowed_domains):
            payload["official_url"] = None
            unresolved = list(payload.get("unresolved_fields") or [])
            unresolved.append("official_url")
            payload["unresolved_fields"] = list(dict.fromkeys(unresolved))

    if payload.get('official_url') and not any(s['url'] == payload['official_url'] and s['source_type'] == 'official' for s in reconciled):
        payload['official_url'] = None
    if not any(s['source_type'] in {'official', 'official_store', 'retailer'} for s in reconciled):
        payload['match_status'] = 'ambiguous'
        payload['unresolved_fields'] = list(dict.fromkeys([*payload.get('unresolved_fields', []), 'canonical_name', 'model_or_shade']))
    has_official_source = any(
        source["source_type"] == "official" for source in reconciled
    )
    if payload.get("match_status") == "exact_match" and not has_official_source:
        payload["match_status"] = "likely_match"
        warnings.append("没有品牌官网搜索来源，匹配状态已从 exact_match 降为 likely_match")

    supported_fields = {
        field
        for source in reconciled
        for field in source.get("supports_fields", [])
    }
    for field, empty_value in (
        ("color_family", []),
        ("finish", []),
        ("official_description", None),
        ("official_usage", None),
    ):
        if payload.get(field) and field not in supported_fields:
            payload[field] = empty_value
            unresolved = list(payload.get("unresolved_fields") or [])
            unresolved.append(field)
            payload["unresolved_fields"] = list(dict.fromkeys(unresolved))

    if not any(s['source_type'] == 'review' for s in reconciled):
        payload['review_observations'] = []
        warnings.append('暂无可验证测评；可以补充更完整的产品名或色号后重试。')
    payload['alternatives'] = [a for a in payload.get('alternatives', []) if _normalized_url(a['url']) in actual_by_url][:2]
    payload["sources"] = reconciled
    payload["validation_warnings"] = list(dict.fromkeys(warnings))
    return payload


def validate_enrichment_sources(
    enrichment: ProductEnrichment,
    domain_registry_path: Path = DEFAULT_DOMAIN_REGISTRY,
    *,
    max_pages: int = 2,
    concurrency: int = 2,
    page_timeout_seconds: float = 5,
    total_timeout_seconds: float | None = None,
) -> ProductEnrichment:
    registry = json.loads(domain_registry_path.read_text(encoding="utf-8"))
    allowed_domains = brand_domains(enrichment.canonical_brand, registry)
    warnings = list(enrichment.validation_warnings)
    max_pages = max(0, min(max_pages, len(enrichment.sources)))

    official = [
        source
        for source in enrichment.sources
        if source.source_type == SourceType.official
        or (enrichment.official_url and source.url == enrichment.official_url)
    ]
    official.sort(
        key=lambda source: 0
        if enrichment.official_url and source.url == enrichment.official_url
        else 1
    )
    identity = [
        source
        for source in enrichment.sources
        if source.source_type in {SourceType.official_store, SourceType.retailer, SourceType.other}
        and source not in official
    ]
    identity_fields = {"canonical_name", "model_or_shade", "category"}
    type_priority = {
        SourceType.official_store: 0,
        SourceType.retailer: 1,
        SourceType.other: 2,
    }
    identity.sort(
        key=lambda source: (
            not bool(identity_fields & set(source.supports_fields)),
            type_priority.get(source.source_type, 3),
        )
    )
    reviews = [
        source for source in enrichment.sources if source.source_type == SourceType.review
    ]
    selected: list[ProductSource] = []
    if official:
        selected.append(official[0])
        selected.extend((reviews or official[1:] or identity)[:1])
    else:
        selected.extend(identity[:max_pages])
        if len(selected) < max_pages:
            selected.extend(reviews[: max_pages - len(selected)])
    selected = selected[:max_pages]

    def check(source: ProductSource) -> tuple[str, SourceValidation]:
        timeout = page_timeout_seconds
        if total_timeout_seconds is not None:
            timeout = max(0.1, min(timeout, total_timeout_seconds))
        http_status: int | None = None
        final_url: str | None = None
        error_note: str | None = None
        try:
            headers = {"User-Agent": "MakeupTranslatorPrototype/0.1 source-verification"}
            with httpx.Client(
                follow_redirects=True,
                max_redirects=3,
                timeout=timeout,
                headers=headers,
            ) as client:
                with client.stream("GET", source.url) as response:
                    http_status = response.status_code
                    final_url = str(response.url)
            reachable = http_status < 400
        except httpx.HTTPError as exc:
            reachable = False
            error_note = f"request failed: {exc.__class__.__name__}"
        checked_host = urlparse(final_url or source.url).hostname or ""
        return source.url, SourceValidation(
            reachable=reachable,
            http_status=http_status,
            final_url=final_url,
            official_domain_verified=(
                source.source_type == SourceType.official
                and bool(allowed_domains)
                and _host_matches(checked_host, allowed_domains)
            ),
            note=error_note,
        )

    validation_by_url: dict[str, SourceValidation] = {}
    if selected:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(selected))) as executor:
            futures = [executor.submit(check, source) for source in selected]
            for future in as_completed(futures):
                url, validation = future.result()
                validation_by_url[url] = validation

    validated_sources: list[ProductSource] = []
    for source in enrichment.sources:
        validation = validation_by_url.get(source.url)
        if validation is not None:
            if not validation.reachable:
                warnings.append(
                    f"来源不可访问（HTTP {validation.http_status}）：{source.url}"
                )
            if source.source_type == SourceType.official and not validation.official_domain_verified:
                checked_host = urlparse(validation.final_url or source.url).hostname or ""
                warnings.append(f"官方来源域名尚未验证：{checked_host}")
        payload = source.model_dump()
        payload["validation"] = validation
        validated_sources.append(ProductSource.model_validate(payload))

    payload = enrichment.model_dump()
    payload["sources"] = validated_sources
    payload["validation_warnings"] = list(dict.fromkeys(warnings))
    return ProductEnrichment.model_validate(payload)


class QwenProductEnricher:
    def __init__(
        self,
        budget: SearchBudget | None = None,
        progress: Callable[[int, str, str], None] | None = None,
    ) -> None:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for product enrichment")
        self.model = os.getenv("QWEN_SEARCH_MODEL", "qwen-plus")
        self.extraction_model = os.getenv(
            "QWEN_EXTRACTION_MODEL", "qwen-turbo"
        )
        self.client = OpenAI(
            api_key=api_key,
            max_retries=0,
            base_url=os.getenv(
                "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
        )
        self.api_key = api_key
        self.generation_url = os.getenv(
            "DASHSCOPE_GENERATION_URL",
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
        )
        self.budget = budget or load_search_budget()
        self.progress = progress or (lambda _percent, _stage, _message: None)

    def _search(
        self, messages: list[dict[str, str]], *, timeout: float, sites=None
    ) -> tuple[str, list[dict[str, Any]]]:
        response = httpx.post(
            self.generation_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "input": {"messages": messages},
                "parameters": {
                    "temperature": 0.1,
                    "result_format": "message",
                    "enable_search": True,
                    "search_options": {
                        "forced_search": True,
                        "search_strategy": "turbo",
                        **({"assigned_site_list": sites} if sites else {}),
                        "enable_source": True,
                        "enable_citation": True,
                        "citation_format": "[ref_<number>]",
                    },
                },
            },
            timeout=max(0.1, timeout),
        )
        response.raise_for_status()
        body = response.json()
        self.last_search_usage = body.get('usage')
        output = body.get("output") or {}
        choices = output.get("choices") or []
        if not choices:
            raise RuntimeError(f"DashScope search returned no answer: {body.get('message', 'unknown error')}")
        content = choices[0].get("message", {}).get("content", "")
        search_results = (output.get("search_info") or {}).get("search_results") or []
        if 'search_info' not in output:
            raise RuntimeError("搜索服务未返回 search_info，无法核验来源；请补充产品信息或稍后重试")
        return content, search_results

    def _run_search(
        self,
        controller: SearchController,
        stage: str,
        prompt: str,
        timeout: float,
    ) -> tuple[list[dict[str, Any]], SearchAttempt]:
        controller.claim(stage)
        started = time.monotonic()
        try:
            _, results = self._search(
                [{"role": "user", "content": prompt}], timeout=timeout,
                sites=SOCIAL_DOMAINS if stage == "review_search" else self.identity_domains
            )
            scope = "identity" if stage == "identity_search" else "review"
            sites = SOCIAL_DOMAINS if scope == 'review' else self.identity_domains
            if sites:
                results = [item for item in results if _host_matches(urlparse(str(item.get('url', ''))).hostname or '', sites)]
            tagged = [dict(item, _search_scope=scope) for item in results]
            attempt = SearchAttempt(
                stage=stage,
                status="success",
                duration_ms=round((time.monotonic() - started) * 1000),
                result_count=len(tagged),
                detail='限定站点内没有可核验结果' if not tagged else None,
            )
            return tagged, attempt
        except (httpx.TimeoutException, TimeoutError) as exc:
            return [], SearchAttempt(
                stage=stage,
                status="timeout",
                duration_ms=round((time.monotonic() - started) * 1000),
                result_count=0,
                detail=exc.__class__.__name__,
            )
        except Exception as exc:
            return [], SearchAttempt(
                stage=stage,
                status="failed",
                duration_ms=round((time.monotonic() - started) * 1000),
                result_count=0,
                detail=exc.__class__.__name__,
            )

    def enrich(self, record: ProductRecord, photo_paths: list[str] | None = None) -> ProductEnrichment:
        self.identity_domains = brand_domains(record.raw_brand, json.loads(DEFAULT_DOMAIN_REGISTRY.read_text()), record.raw_market)
        controller = SearchController(self.budget)
        self.progress(6, "web_search", "正在并发搜索产品身份和测评信息")
        raw_input = {
            "brand": record.raw_brand,
            "product_name": record.raw_name,
            "model_or_shade": record.raw_model_or_shade,
            "market": record.raw_market,
        }
        base_query = " ".join(
            filter(
                None,
                [
                    record.raw_brand,
                    record.raw_name,
                    record.raw_model_or_shade,
                    record.raw_market,
                ],
            )
        )
        identity_prompt = (
            "请精确搜索以下美妆产品。优先寻找品牌官网具体产品页，其次寻找官方旗舰店；"
            "不要扩展到美妆榜单或无关推荐。\n检索词："
            + base_query
            + " 品牌官网 官方产品页"
        )
        review_prompt = (
            "请精确搜索以下美妆产品的实测或试色，仅返回与该产品和色号直接相关的测评页；"
            "不要用测评页确认官方产品身份。\n检索词："
            + base_query
            + " 测评 试色 质地 显色度 持妆"
        )
        # Reserve ten seconds for extraction/repair and concurrent page validation.
        search_timeout = max(0.1, controller.remaining() - 10)
        search_results_by_stage: dict[str, list[dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(
                    self._run_search,
                    controller,
                    "identity_search",
                    identity_prompt,
                    search_timeout,
                ): "identity_search",
                executor.submit(
                    self._run_search,
                    controller,
                    "review_search",
                    review_prompt,
                    search_timeout,
                ): "review_search",
            }
            for future in as_completed(futures):
                results, attempt = future.result()
                controller.add_attempt(attempt)
                search_results_by_stage[futures[future]] = results
                completed_searches = len(search_results_by_stage)
                self.progress(
                    18 + completed_searches * 14,
                    "web_search",
                    f"已完成 {completed_searches}/2 路联网搜索",
                )

        search_results = (
            search_results_by_stage.get("identity_search", [])
            + search_results_by_stage.get("review_search", [])
        )
        deduplicated: dict[str, dict[str, Any]] = {}
        for item in search_results:
            if item.get("url"):
                deduplicated.setdefault(_normalized_url(str(item["url"])), item)
        search_results = list(deduplicated.values())
        if not search_results:
            audit = controller.audit(
                "timeout"
                if any(item.status == "timeout" for item in controller._attempts)
                else "failed"
            )
            raise RuntimeError(
                "两次有界搜索均未返回来源，产品保持 draft。"
                f"搜索耗时 {audit.elapsed_ms / 1000:.1f} 秒。"
            )
        messages = [
            {"role": "system", "content": PRODUCT_SEARCH_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": PRODUCT_SEARCH_USER_PROMPT
                + "\n\n用户原始输入：\n"
                + json.dumps(raw_input, ensure_ascii=False)
                + "\n\nDashScope search_info 返回的唯一可用来源：\n"
                + json.dumps(search_results, ensure_ascii=False),
            },
        ]
        self.progress(52, "extraction", "正在从来源中提取产品资料")
        controller.claim("extraction")
        extraction_started = time.monotonic()
        try:
            extraction_response = self.client.with_options(
                timeout=max(0.1, controller.remaining()),
                max_retries=0,
            ).chat.completions.create(
                model=self.extraction_model,
                temperature=0.1,
                messages=messages,
            )
            raw_text = extraction_response.choices[0].message.content or ""
            controller.add_attempt(
                SearchAttempt(
                    stage="extraction",
                    status="success",
                    duration_ms=round((time.monotonic() - extraction_started) * 1000),
                )
            )
        except Exception as exc:
            controller.add_attempt(
                SearchAttempt(
                    stage="extraction",
                    status="timeout" if controller.remaining() <= 0 else "failed",
                    duration_ms=round((time.monotonic() - extraction_started) * 1000),
                    detail=exc.__class__.__name__,
                )
            )
            raise RuntimeError("产品资料提取失败，产品保持 draft") from exc
        try:
            payload = reconcile_search_sources(
                _normalize_enrichment_payload(_extract_json(raw_text)), search_results
            )
            if not payload.get('sources'):
                raise RuntimeError('未找到可核验的产品来源。请补充系列名、色号或修改购买地区后重试；产品保留为草稿。')
            enrichment = ProductEnrichment.model_validate(payload)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            # One bounded repair call: no second web search, only schema repair.
            self.progress(70, "schema_repair", "正在修复资料格式，不会再次联网")
            controller.claim("schema_repair")
            repair_started = time.monotonic()
            repair_response = self.client.with_options(
                timeout=max(0.1, controller.remaining()),
                max_retries=0,
            ).chat.completions.create(
                model=self.extraction_model,
                temperature=0,
                messages=[
                    {"role": "system", "content": PRODUCT_SEARCH_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "以下联网搜索结果没有通过数据校验。只修复 JSON 结构，禁止新增事实或来源。\n"
                            f"校验错误：{exc}\n原始结果：{raw_text}"
                        ),
                    },
                ],
            )
            repaired = _normalize_enrichment_payload(
                _extract_json(repair_response.choices[0].message.content or "")
            )
            controller.add_attempt(
                SearchAttempt(
                    stage="schema_repair",
                    status="success",
                    duration_ms=round((time.monotonic() - repair_started) * 1000),
                )
            )
            enrichment = ProductEnrichment.model_validate(
                reconcile_search_sources(repaired, search_results)
            )
        self.progress(82, "source_validation", "正在并发校验来源页面")
        validation_started = time.monotonic()
        if controller.remaining() > 0:
            enrichment = validate_enrichment_sources(
                enrichment,
                max_pages=self.budget.pages_to_validate,
                concurrency=self.budget.page_validation_concurrency,
                page_timeout_seconds=self.budget.page_timeout_seconds,
                total_timeout_seconds=controller.remaining(),
            )
        validated_count = sum(
            source.validation is not None for source in enrichment.sources
        )
        controller.add_attempt(
            SearchAttempt(
                stage="page_validation",
                status="success" if validated_count else "skipped",
                duration_ms=round((time.monotonic() - validation_started) * 1000),
                result_count=validated_count,
            )
        )
        enrichment.image_comparison = '未上传包装照片，未进行图片比对。'
        if photo_paths:
            candidates = [source for source in enrichment.sources if source.image_url and source.source_type == SourceType.official][:2]
            enrichment.image_comparison = '来源没有提供可核验产品图，跳过比对；请对照包装确认。'
            if candidates and controller.remaining() < 8:
                enrichment.image_comparison = '剩余搜索时间不足，跳过图片比对；请对照包装确认。'
            elif candidates:
                import base64
                import mimetypes
                started = time.monotonic()
                content = [{'type': 'text', 'text': '前面的图片是用户包装照，后面的图片是来源候选图。只比较包装文字、容量、型号和版本。不要根据照片颜色判断细微色差，不自动确认身份。如果图片不是产品图或有包装换代、冲突，要明确待用户确认。用简短中文描述每个候选差别。'}]
                for path in photo_paths[:2]:
                    content.append({'type':'image_url', 'image_url':{'url':'data:' + (mimetypes.guess_type(path)[0] or 'image/jpeg') + ';base64,' + base64.b64encode(Path(path).read_bytes()).decode()}})
                for source in candidates:
                    content.extend([{'type':'text','text': f'候选：{source.title}，来源 {source.url}'}, {'type':'image_url','image_url':{'url':source.image_url}}])
                try:
                    response = self.client.with_options(timeout=min(8, controller.remaining()), max_retries=0).chat.completions.create(model=os.getenv('QWEN_VISION_MODEL','qwen3-vl-plus'), messages=[{'role':'user','content':content}], temperature=0)
                    enrichment.image_comparison = response.choices[0].message.content or '比对没有返回观察，请手动确认。'
                    enrichment.match_status = 'ambiguous'
                    controller.add_attempt(SearchAttempt(stage='image_comparison', status='success', duration_ms=round((time.monotonic()-started)*1000)))
                except Exception:
                    enrichment.image_comparison = '图片无法读取或比对超时，未作身份判断；请手动确认。'
                    controller.add_attempt(SearchAttempt(stage='image_comparison', status='failed', duration_ms=round((time.monotonic()-started)*1000)))
        status = "completed"
        if controller.remaining() <= 0:
            status = "timeout"
        elif any(attempt.status != "success" for attempt in controller._attempts):
            status = "partial"
        payload = enrichment.model_dump()
        payload["search_audit"] = controller.audit(status)
        self.progress(96, "finalizing", "正在整理待确认的产品卡片")
        return ProductEnrichment.model_validate(payload)
