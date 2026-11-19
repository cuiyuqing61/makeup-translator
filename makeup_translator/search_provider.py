"""Bounded product-source search adapters.

AIHubMix uses the documented ``:surfing`` model suffix. Search output is only
candidate data: domain filtering and page-body evidence remain code-enforced.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from openai import OpenAI

from .product_enrichment import (
    QwenProductEnricher,
    SOCIAL_DOMAINS,
    SearchController,
    _host_matches,
    _normalized_url,
    load_search_budget,
)
from .product_models import SearchAttempt
from .providers import _extract_json


class AIHubMixProductSearch:
    """Search through AIHubMix/Tavily and return filtered source candidates."""

    def __init__(self, progress=None) -> None:
        api_key = os.getenv('DASHSCOPE_API_KEY')
        if not api_key:
            raise RuntimeError('需要在 .env 中配置 AIHubMix API Key')
        base_url = os.getenv('QWEN_BASE_URL', 'https://aihubmix.com/v1')
        self.model = os.getenv('SEARCH_MODEL') or os.getenv('QWEN_AGENT_MODEL', 'glm-5.3-flash')
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        self.budget = load_search_budget()
        self.progress = progress or (lambda *_args: None)
        self.identity_domains: list[str] = []
        self.last_search_usage: dict[str, Any] | None = None

    @property
    def surfing_model(self) -> str:
        return self.model if self.model.endswith(':surfing') else self.model + ':surfing'

    def _search(self, prompt: str, *, timeout: float, sites: list[str], scope: str):
        restriction = (
            '只返回以下域名中的结果：' + '、'.join(sites)
            if sites else
            '品牌官网候选尚未核验；返回最可能的品牌官网页面，但不要声称其已通过官网认证。'
        )
        response = self.client.with_options(timeout=max(.1, timeout)).chat.completions.create(
            model=self.surfing_model,
            temperature=0,
            messages=[
                {'role': 'system', 'content': (
                    '你是美妆来源搜索器。搜索网页，只返回搜索中实际出现的链接。'
                    '不要根据标题拼接、补全或猜测 URL。返回单个 JSON 对象：'
                    '{"results":[{"title":"页面标题","url":"https://完整链接",'
                    '"snippet":"搜索摘要；没有则为空"}]}。最多返回 8 条。'
                )},
                {'role': 'user', 'content': f'搜索类型：{scope}\n{restriction}\n查询：{prompt}'},
            ],
        )
        self.last_search_usage = response.usage.model_dump() if response.usage else None
        payload = _extract_json(response.choices[0].message.content or '')
        items = payload.get('results')
        if not isinstance(items, list):
            raise ValueError('AIHubMix 搜索结果缺少 results 数组')
        deduplicated: dict[str, dict[str, Any]] = {}
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            url = str(item.get('url') or '').strip()
            parsed = urlparse(url)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
                continue
            if sites and not _host_matches(parsed.hostname, sites):
                continue
            normalized = _normalized_url(url)
            deduplicated.setdefault(normalized, {
                'title': str(item.get('title') or url)[:500],
                'url': url,
                'snippet': str(item.get('snippet') or '')[:4000],
            })
        return list(deduplicated.values())

    def _run_search(self, controller: SearchController, stage: str, prompt: str, timeout: float):
        controller.claim(stage)
        started = time.monotonic()
        scope = 'identity' if stage == 'identity_search' else 'review'
        sites = self.identity_domains if scope == 'identity' else SOCIAL_DOMAINS
        try:
            results = self._search(prompt, timeout=timeout, sites=sites, scope=scope)
            tagged = [dict(item, _search_scope=scope) for item in results]
            return tagged, SearchAttempt(
                stage=stage,
                status='success',
                duration_ms=round((time.monotonic() - started) * 1000),
                result_count=len(tagged),
                detail='限定站点内没有可核验结果' if not tagged else None,
            )
        except (httpx.TimeoutException, TimeoutError) as exc:
            status = 'timeout'
            detail = exc.__class__.__name__
        except Exception as exc:
            status = 'failed'
            detail = f'{exc.__class__.__name__}: {str(exc)[:180]}'
        return [], SearchAttempt(
            stage=stage,
            status=status,
            duration_ms=round((time.monotonic() - started) * 1000),
            result_count=0,
            detail=detail,
        )


def create_product_search(progress=None):
    provider = os.getenv('SEARCH_PROVIDER', 'auto').strip().lower()
    base_url = os.getenv('QWEN_BASE_URL', '')
    if provider == 'auto':
        provider = 'aihubmix' if 'aihubmix.com' in base_url.lower() else 'dashscope'
    if provider == 'aihubmix':
        return AIHubMixProductSearch(progress=progress)
    if provider == 'dashscope':
        return QwenProductEnricher(progress=progress)
    raise RuntimeError(f'不支持的 SEARCH_PROVIDER：{provider}')
