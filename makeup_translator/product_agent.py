"""Catalog-first identity agent, with evidence permissions enforced outside prompts."""
from __future__ import annotations

import ipaddress
import json
import socket
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, urljoin

import httpx
from pydantic import Field

from .agent_runtime import AgentRuntime, EmptyInput, FunctionTool
from .models import StrictModel
from .cache import ArtifactCache
from .product_enrichment import (QwenProductEnricher, SearchController, DEFAULT_DOMAIN_REGISTRY,
                                 brand_domains, _host_matches, SOCIAL_DOMAINS, reconcile_search_sources)
from .product_models import ProductEnrichment, ProductSource, SourceValidation
from .search_provider import create_product_search

CATALOG_PATH = Path(__file__).resolve().parent.parent / 'data' / 'product_catalog.json'


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value or '').casefold() if c.isalnum() and c != 'ー')


def canonical_brand(value):
    for name, entry in json.loads(DEFAULT_DOMAIN_REGISTRY.read_text()).items():
        if isinstance(entry, dict) and normalize(value) in {normalize(x) for x in [name, entry.get('canonical_name', ''), entry.get('chinese_name', ''), *entry.get('aliases', [])]}:
            return name
    return value


def _words(value):
    """Comparable words for product-name matching across punctuation/case."""
    normalized = unicodedata.normalize('NFKC', value or '').casefold()
    return {part for part in re.findall(r'[a-z0-9]+|[\u3400-\u9fff]+', normalized) if part}


def _cjk_bigrams(value):
    text = unicodedata.normalize('NFKC', value or '')
    pairs = set()
    for segment in re.findall(r'[\u3400-\u9fff]+', text):
        pairs.update(segment[index:index + 2] for index in range(len(segment) - 1))
    return pairs


def _name_match_score(query, item):
    query_key = normalize(query)
    if not query_key:
        return 0
    candidates = [item.get('name', ''), *item.get('aliases', [])]
    candidate_keys = [normalize(value) for value in candidates if value]
    if query_key in candidate_keys:
        return 100
    # A normal consumer entry is often a shorter product-line name, e.g.
    # "Studio Fix" versus the complete official listing title.
    if len(query_key) >= 3 and any(query_key in value for value in candidate_keys):
        return 85
    if any(len(value) >= 3 and value in query_key for value in candidate_keys):
        return 80
    query_pairs = _cjk_bigrams(query)
    if len(query_pairs) >= 2:
        for candidate in candidates:
            candidate_pairs = _cjk_bigrams(candidate)
            if len(query_pairs & candidate_pairs) / len(query_pairs) >= .6:
                return 70
    query_words = _words(query)
    if not query_words:
        return 0
    best = 0
    for candidate in candidates:
        candidate_words = _words(candidate)
        if not candidate_words:
            continue
        overlap = len(query_words & candidate_words)
        if overlap and overlap / len(query_words) >= .6:
            best = max(best, 60 + min(overlap, 5))
    return best


def _shade_codes(value):
    """Extract shade identifiers while preserving their letter prefix.

    Leading zeroes are presentation details in common shade codes: PO-01 and
    PO1 identify the same option, while N4 and LN4 must remain distinct.
    """
    text = unicodedata.normalize('NFKC', value or '').casefold()
    codes = set()
    for letters, number in re.findall(r'(?<![a-z0-9])([a-z]+)[\s#_\-\u30fc]*(\d+(?:\.\d+)?)', text):
        integer, dot, decimal = number.partition('.')
        number_key = str(int(integer)) + (dot + decimal.rstrip('0') if dot and decimal.rstrip('0') else '')
        codes.add(letters + number_key)
    # Some ranges use a bare numeric code (e.g. Lancôme 100). Do not add the
    # numeric tail of an alphanumeric code, which would make N4 match LN4.
    if not codes:
        for number in re.findall(r'(?<![a-z0-9])0*(\d+(?:\.\d+)?)(?![a-z0-9])', text):
            integer, dot, decimal = number.partition('.')
            codes.add(str(int(integer)) + (dot + decimal.rstrip('0') if dot and decimal.rstrip('0') else ''))
    return codes


def _shade_match_score(query, candidate):
    if not query:
        return 1
    if not candidate:
        return 0
    query_codes = _shade_codes(query)
    candidate_codes = _shade_codes(candidate)
    if query_codes and candidate_codes:
        return 100 if query_codes & candidate_codes else 0
    query_key = normalize(query)
    candidate_key = normalize(candidate)
    if query_key == candidate_key:
        return 100
    if len(query_key) >= 2 and query_key in candidate_key:
        return 80
    if len(candidate_key) >= 2 and candidate_key in query_key:
        return 75
    return 0


def lookup_catalog(record):
    entries = json.loads(CATALOG_PATH.read_text()) if CATALOG_PATH.exists() else []
    hits = []
    for item in entries:
        if normalize(canonical_brand(record.raw_brand)) != normalize(item['brand']):
            continue
        name_score = _name_match_score(record.raw_name, item)
        if not name_score:
            continue
        shade_score = _shade_match_score(record.raw_model_or_shade, item.get('shade'))
        if not shade_score:
            continue
        # Region/version differences should be visible to the user, not make a
        # known product disappear. Prefer the requested market without using it
        # as a hard filter.
        market_score = 1 if record.raw_market and item.get('market') == record.raw_market else 0
        hits.append(((name_score, shade_score, market_score), item))
    hits.sort(key=lambda pair: (pair[0], pair[1].get('name', ''), pair[1].get('shade') or ''), reverse=True)
    return [item for _, item in hits[:3]]


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []
        self.structured = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'script':
            self.structured = attributes.get('type') == 'application/ld+json'
        if ':variants' in attributes:
            try:
                for variant in json.loads(attributes[':variants']):
                    self.parts.extend(str(variant[k]) for k in ['productName', 'name'] if variant.get(k))
            except (ValueError, TypeError):
                pass
        if tag in {'script', 'style', 'noscript'}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag == 'script':
            self.structured = False
        if tag in {'script', 'style', 'noscript'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if self.structured:
            try:
                def extract(value):
                    if isinstance(value, list):
                        for v in value:
                            extract(v)
                    elif isinstance(value, dict):
                        for key, item in value.items():
                            if key in {'name', 'description', 'sku', 'color', 'size'} and isinstance(item, str):
                                self.parts.append(item)
                            elif isinstance(item, (dict, list)):
                                extract(item)
                extract(json.loads(data))
            except ValueError:
                pass
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def read_public_page(url, timeout=5):
    """Bound body size, redirects and address ranges; never fetch arbitrary tool URLs."""
    for _ in range(3):
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            raise ValueError('不支持的来源地址')
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('来源必须使用公网地址')
        with httpx.stream('GET', url, timeout=timeout, follow_redirects=False, headers={'User-Agent': 'MakeupTranslator/0.2'}) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers['location'])
                continue
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 1_500_000:
                    break
            parser = TextParser()
            parser.feed(bytes(content).decode(response.encoding or 'utf-8', errors='replace'))
            text = ' '.join(parser.parts)[:60000]
            if len(text) < 100:
                raise ValueError('页面正文不可读取，可能需要 JavaScript 或登录')
            return {'url': url, 'text': text, 'checked_at': datetime.now(timezone.utc).isoformat()}
    raise ValueError('来源重定向过多')


class SearchInput(StrictModel):
    include_reviews: bool = False


class PageInput(StrictModel):
    urls: list[str] = Field(min_length=1, max_length=2)


class ClarificationInput(StrictModel):
    question: str = Field(min_length=4, max_length=300)
    choices: list[str] = Field(default_factory=list, max_length=3)


def partial_product_card(record, checkpoint, reason='核验达到 60 秒上限'):
    """Build an evidence-limited card without another model call."""
    state = checkpoint or {}
    verified = state.get('verified_fields') or {}
    evidenced_urls = {item.get('url') for item in verified.values()
                      if isinstance(item, dict) and item.get('url')}
    sources = [{key: value for key, value in source.items() if key in
                ('url', 'title', 'validation', 'supports_fields')}
               for source in state.get('sources', []) if isinstance(source, dict)
               and source.get('url') in evidenced_urls]
    field_names = ('canonical_name', 'model_or_shade', 'category',
                   'color_family', 'finish', 'official_usage')
    missing = ['model_or_shade'] if not record.raw_model_or_shade and 'model_or_shade' not in verified else []
    return {'status': 'partial', 'input': {'brand': record.raw_brand,
        'name': record.raw_name, 'model_or_shade': record.raw_model_or_shade,
        'market': record.raw_market}, 'verified_fields': verified,
        'pending_fields': [name for name in field_names if name not in verified and name not in missing]
            if evidenced_urls else [], 'missing_fields': missing if evidenced_urls else [],
        'sources': sources, 'search_progress': state.get('stage', '尚未取得可信来源'),
        'reason': reason, 'user_confirmed': False,
        'product_updated_at': record.model_dump(mode='json')['updated_at']}


def run_product_agent(record, photo_paths, report, *, answer=None, round_number=0, checkpoint=None, llm=None, force_refresh=False):
    cache = ArtifactCache(Path(os.getenv('MAKEUP_CACHE_PATH', 'runtime/agent-cache.sqlite3')))
    key = cache.key('product-agent-v4-60s-checkpoint', canonical_brand(record.raw_brand), normalize(record.raw_name),
                    normalize(record.raw_model_or_shade), record.raw_market, os.getenv('QWEN_AGENT_MODEL', 'qwen-plus'),
                    CATALOG_PATH.read_text() if CATALOG_PATH.exists() else '', DEFAULT_DOMAIN_REGISTRY.read_text(), Path(__file__).read_text())
    with cache.lock(key):
        cached = None if force_refresh or answer or llm or photo_paths or checkpoint else cache.get(key)
        if cached:
            event = {'name': 'product_cache', 'status': 'success', 'duration_ms': 0, 'cache_hit': True, 'usage': None}
            cached['agent_trace'] = [event]
            emit = getattr(report, 'event', None)
            if emit:
                emit('trace', event)
            return cached
        result = _run_product_agent(record, photo_paths, report, answer=answer, round_number=round_number,
                                    checkpoint=checkpoint, llm=llm, force_refresh=force_refresh)
        if not llm and not photo_paths and not answer and not result.get('_waiting_user') and result['match_status'] != 'ambiguous':
            cache.put(key, result)
        return result


def _run_product_agent(record, photo_paths, report, *, answer=None, round_number=0, checkpoint=None, llm=None, force_refresh=False):
    runtime = AgentRuntime(seconds=60, report=report, llm=llm, max_calls=6)
    state = dict(checkpoint) if checkpoint else {'sources': [], 'pages': {}, 'catalog': []}
    state['product_updated_at'] = record.model_dump(mode='json')['updated_at']
    state['looked_up'] = bool(state.get('catalog'))

    def snapshot(stage):
        state['stage'] = stage
        emit = getattr(report, 'event', None)
        if emit:
            emit('checkpoint', json.loads(json.dumps(state, ensure_ascii=False, default=str)))

    def catalog(_):
        state['looked_up'] = True
        state['catalog'] = lookup_catalog(record)
        for item in state['catalog']:
            if not any(s['url'] == item['source_url'] for s in state['sources']):
                state['sources'].append({'url': item['source_url'], 'title': item['name'], '_search_scope': 'identity'})
        if len(state['catalog']) == 1 and not force_refresh:
            item = state['catalog'][0]
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(item['verified_at'])).total_seconds()
            if (item.get('verification_status') == 'official_listing_observed' and 0 <= age <= 7*86400
                    and _name_match_score(record.raw_name, item) >= 60
                    and ((not record.raw_model_or_shade and not item.get('shade'))
                         or _shade_match_score(record.raw_model_or_shade, item.get('shade')) >= 75)):
                url = item['source_url']
                domains = brand_domains(record.raw_brand, json.loads(DEFAULT_DOMAIN_REGISTRY.read_text()))
                source_kind = item.get('source_kind', 'official_site')
                official_site = source_kind == 'official_site' and _host_matches(urlparse(url).hostname or '', domains)
                official_store = source_kind in {'official_flagship_tmall', 'official_flagship_taobao'} and item.get('store_identity_verified') is True
                sephora_source = source_kind == 'authorized_retailer_sephora' and item.get('store_identity_verified') is True
                if official_site or official_store or sephora_source:
                    source_type = 'official' if official_site else ('official_store' if official_store else 'retailer')
                    runtime.result = ProductEnrichment(match_status='likely_match', canonical_brand=item['brand'],
                        canonical_name=item['name'], model_or_shade=item['shade'], category=item['category'],
                        official_url=url if official_site else None, unresolved_fields=['market', 'version', 'finish', 'official_usage'],
                        validation_warnings=[('命中最近七天核对过的官网目录；' if official_site else ('命中最近七天核对过的官方旗舰店目录；' if official_store else '命中最近七天核对过的丝芙兰商品目录；')) + '购买地区和包装版本仍需你确认。', '暂无可验证测评。'],
                        field_evidence={field: {'url':url,'excerpt':item['evidence'][key],'checked_at':item['verified_at']}
                                        for field,key in [('canonical_name','name'),('model_or_shade','shade')]
                                        if item.get('evidence', {}).get(key)},
                        sources=[ProductSource(title=item['name'],url=url,source_type=source_type,
                            supports_fields=['canonical_name','model_or_shade'], note='缓存的公开目录，非本次实时抓取',
                            validation=SourceValidation(reachable=True,official_domain_verified=official_site,checked_at=item['verified_at'],
                                note=('官网域名已验证' if official_site else ('官方旗舰店身份已验证：' if official_store else '丝芙兰来源已验证：') + str(item.get('store_identity')))))])
        snapshot('本地目录已检查')
        return {'candidates': state['catalog'], 'previous_sources': state['sources'], 'previous_pages': state['pages']}

    def search(args):
        if runtime.remaining() <= 8:
            raise ValueError('已进入结果整理时间，停止新的搜索')
        if not state['looked_up']:
            raise ValueError('先查询本地目录')
        if state.get('searched'):
            return state['sources']
        query = ' '.join(filter(None, [record.raw_brand, record.raw_name, record.raw_model_or_shade, record.raw_market, answer]))
        stages = ['identity_search'] + (['review_search'] if args.include_reviews else [])
        def perform(stage):
            client = create_product_search(progress=report)
            client.identity_domains = brand_domains(record.raw_brand, json.loads(DEFAULT_DOMAIN_REGISTRY.read_text()), record.raw_market)
            controller = SearchController(client.budget)
            results, attempt = client._run_search(controller, stage, query + (' 官网产品页' if stage == 'identity_search' else ' 试色测评'), min(45, max(.1, runtime.remaining()-15)))
            return results, attempt.model_dump(mode='json'), getattr(client, 'last_search_usage', None)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(perform, stages))
        for sources, attempt, usage in results:
            state['sources'].extend(sources)
            runtime.event(attempt['stage'], attempt['status'], time.monotonic(), usage=usage, internal_model_call=True, reported_duration_ms=attempt['duration_ms'], result_count=attempt['result_count'])
        state['searched'] = True
        snapshot('来源搜索已完成')
        return state['sources']

    def pages(args):
        if runtime.remaining() <= 8:
            raise ValueError('已进入结果整理时间，停止读取新页面')
        allowed = {s['url'] for s in state['sources']}
        if any(url not in allowed for url in args.urls):
            raise ValueError('只能读取目录或搜索实际返回的 URL')
        output = []
        for url in args.urls:
            if url in state['pages']:
                output.append(state['pages'][url])
                continue
            if state.get('page_count', 0) >= 2:
                raise ValueError('最多读取两个来源页面')
            state['page_count'] = state.get('page_count', 0) + 1
            try:
                page = read_public_page(url, min(5, max(.1, runtime.remaining()-8)))
                state['pages'][url] = page
                output.append(page)
            except Exception as exc:
                output.append({'url': url, 'error': str(exc)[:200], 'text': ''})
        snapshot('来源页面已读取')
        return output

    def clarify(args):
        if round_number >= 2:
            raise ValueError('已达两轮澄清上限；请编辑产品资料后重新发起任务')
        runtime.waiting = {'question': args.question, 'choices': args.choices, 'round': round_number + 1,
                           'checkpoint': state}
        return {'waiting_user': True}

    def compare(args):
        if not photo_paths:
            return {'status': 'skipped', 'reason': '未上传包装照片'}
        if runtime.remaining() < 8:
            return {'status': 'skipped', 'reason': '剩余时限不足，保留待确认'}
        candidates = {s['url']:s for s in state['sources'] if s.get('image_url')}
        if any(url not in candidates for url in args.urls):
            raise ValueError('候选必须有实际来源提供的产品图片，不能猜测图片地址')
        import base64
        import mimetypes
        from openai import OpenAI
        content = [{'type':'text','text':'前两张以内为用户包装照片，其余是来源候选。只比较包装文字、容量、系列与版本；不能依据图片颜色判断细微色差，不自动确认。若有冲突或无法区分请说明。'}]
        for path in photo_paths[:2]:
            content.append({'type':'image_url','image_url':{'url':'data:'+ (mimetypes.guess_type(path)[0] or 'image/jpeg')+';base64,'+base64.b64encode(Path(path).read_bytes()).decode()}})
        for url in args.urls:
            content.append({'type':'text','text':candidates[url].get('title',url)})
            content.append({'type':'image_url','image_url':{'url':candidates[url]['image_url']}})
        started = time.monotonic()
        client = OpenAI(api_key=os.getenv('DASHSCOPE_API_KEY'),base_url=os.getenv('QWEN_BASE_URL','https://dashscope.aliyuncs.com/compatible-mode/v1'),max_retries=0,timeout=min(8,runtime.remaining()))
        response = client.chat.completions.create(model=os.getenv('QWEN_VISION_MODEL','qwen3-vl-plus'),messages=[{'role':'user','content':content}],temperature=0)
        state['image_comparison'] = response.choices[0].message.content or '未返回可用比对观察，请手动确认'
        snapshot('包装图片已比较')
        runtime.event('packaging_vision', 'success', started,internal_model_call=True,usage=response.usage.model_dump() if response.usage else None)
        return {'status':'needs_user_confirmation','observation':state['image_comparison']}

    def submit(enrichment):
        if canonical_brand(enrichment.canonical_brand) != canonical_brand(record.raw_brand):
            raise ValueError('产品品牌与输入不一致，请澄清而不是替换品牌')
        payload = reconcile_search_sources(enrichment.model_dump(mode='json'), state['sources'])
        if not payload['sources']:
            raise ValueError('缺少实际来源，请请求用户补充包装系列或色号')
        domains = brand_domains(record.raw_brand, json.loads(DEFAULT_DOMAIN_REGISTRY.read_text()), record.raw_market)
        evidence = {}
        for field, item in enrichment.field_evidence.items():
            page = state['pages'].get(item.get('url'))
            excerpt = item.get('excerpt', '')
            if page and excerpt and excerpt in page['text'] and _host_matches(urlparse(page['url']).hostname or '', domains):
                evidence[field] = dict(url=item['url'], excerpt=excerpt, checked_at=page['checked_at'])
        for field, empty in [('official_description', None), ('official_usage', None), ('color_family', []), ('finish', [])]:
            if field not in evidence:
                payload[field] = empty
                payload['unresolved_fields'].append(field)
            elif field.startswith('official_'):
                # Exact source wording avoids unsupported model paraphrases.
                payload[field] = evidence[field]['excerpt']
            else:
                payload[field] = [value for value in payload[field] if normalize(value) in normalize(evidence[field]['excerpt'])]
        for field in ['model_or_shade', 'market', 'product_line']:
            if payload.get(field) and (field not in evidence or normalize(payload[field]) not in normalize(evidence[field]['excerpt'])):
                payload[field] = None
                payload['unresolved_fields'].append(field)
        if record.raw_model_or_shade and not payload.get('model_or_shade'):
            payload['match_status'] = 'ambiguous'
        if record.raw_market and not payload.get('market'):
            if payload['match_status'] == 'exact_match':
                payload['match_status'] = 'likely_match'
        identity = evidence.get('canonical_name')
        if not identity or normalize(payload['canonical_name']) not in normalize(identity['excerpt']):
            payload['match_status'] = 'ambiguous'
            payload['official_url'] = None
            payload['unresolved_fields'].append('canonical_name')
        for source in payload['sources']:
            page = state['pages'].get(source['url'])
            official = bool(page and _host_matches(urlparse(page['url']).hostname or '', domains))
            source['validation'] = SourceValidation(reachable=bool(page), final_url=page['url'] if page else None, official_domain_verified=official).model_dump(mode='json')
            source['supports_fields'] = [field for field, e in evidence.items() if e['url'] == source['url']]
            if source['source_type'] == 'official' and not official:
                source['source_type'] = 'other'
        payload['field_evidence'] = evidence
        state['verified_fields'] = {field: {'value': payload.get(field), **item}
                                    for field, item in evidence.items()}
        snapshot('字段依据已核对')
        payload['alternatives'] = payload['alternatives'][:3]
        payload['image_comparison'] = state.get('image_comparison') or ('缺少候选图片或剩余时限不足，未执行候选图比对，请手动确认。' if photo_paths else '未上传包装照片。')
        if state.get('image_comparison'):
            payload['match_status'] = 'ambiguous'
        runtime.result = ProductEnrichment.model_validate(payload)
        return {'submitted': True, 'match_status': runtime.result.match_status}

    runtime.run([
        FunctionTool(runtime, 'lookup_catalog', '查询品牌别名及本地产品目录', EmptyInput, catalog),
        FunctionTool(runtime, 'search_sources', '搜索官网身份，可选并行社媒摘要搜索', SearchInput, search),
        FunctionTool(runtime, 'read_pages', '读取最多两个真实来源正文', PageInput, pages, limit=2),
        FunctionTool(runtime, 'ask_clarification', '候选无法区分时询问一个关键问题', ClarificationInput, clarify),
        FunctionTool(runtime, 'compare_packaging', '有真实候选图片且剩余时间充足时比较最多两个候选包装', PageInput, compare),
        FunctionTool(runtime, 'submit_product', '提取并校验产品字段，引用原文作为 field_evidence', ProductEnrichment, submit, limit=2),
    ], '首先 lookup_catalog。目录唯一命中可直接 read_pages；否则 search_sources，然后 read_pages，再 submit_product。'
       'field_evidence 格式为字段名:{url,excerpt}，excerpt 必须是正文逐字摘录。官方字段不能由摘要支持。'
       '无法确定系列/色号时 ask_clarification；最多三个选项。目录记录不是用户确认。'
       '同一轮可以调用多个工具，严格遵守依赖顺序。社媒测评非必需，不编造。',
       {'input': record.model_dump(mode='json', exclude={'enrichment'}), 'answer': answer, 'clarification_round': round_number},
       required_sequence=('lookup_catalog',))
    if runtime.waiting:
        return {'_waiting_user': runtime.waiting, 'agent_trace': runtime.trace}
    runtime.result.agent_trace = runtime.trace
    return runtime.result.model_dump(mode='json')
