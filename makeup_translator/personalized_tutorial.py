"""Evidence-bound, locally reviewable LangGraph tutorial workflow.

Each model role produces data for the next role. Identifiers, inventory claims,
time, visible evidence and catalog provenance are checked in local code.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver
from openai import OpenAI
from PIL import Image, ImageOps

from .cache import ArtifactCache
from .constraints import validate_tutorial
from .illustrations import attach_illustrations
from .knowledge import attach_technique_sources
from .makeup_colors import catalog_candidates, color_distance, sample_color
from .models import (
    ApplicationGuide, Confidence, DrawingGuide, LookAnalysis, MakeupModuleDecision, MakeupTarget, OwnedProduct, PipelineResult,
    ProductMatch, ProductRequirement, TechniqueCard, TutorialPlan, TutorialStep, UserProfile,
)
from .pipeline import product_satisfies_card
from .providers import _extract_json


PROMPT_VERSION = 'personalized-graph-v10-visible-lid-wash'
REGION_PLAN_VERSION = 'region-plan-v11-light-ambiguity'
TARGET_COLOR_AUDIT_VERSION = 'target-color-audit-v1'
PRODUCT_WORDING_VERSION = 'product-wording-v3'

PRODUCT_LABELS = {
    'base': '底妆产品', 'brow': '眉妆产品', 'eyeshadow': '眼影',
    'eyeliner': '眼线产品', 'mascara': '睫毛膏', 'contour': '修容产品',
    'highlighter': '高光产品', 'blush': '腮红', 'lip': '唇部产品',
}


class ModelOutputError(RuntimeError):
    pass


def _tools_used_by_step(card: TechniqueCard, instruction: str, action: str) -> list[str]:
    """A multi-purpose card may have tools for actions this step does not perform."""
    used = [tool for tool in card.tools if tool in instruction + ' ' + action]
    return used or card.tools


def _actionable_target(target: MakeupTarget) -> bool:
    """Visibility and color certainty are separate: muted color is still a step."""
    observation = target.effect + target.evidence
    if (target.region.value == 'complexion' and target.role == 'base'
            and '光线' in observation and any(term in observation for term in
            ('难以区分', '无法区分', '不能区分', '分不清'))):
        return False
    if (target.region.value == 'eye' and target.role == 'finish'
            and '睫毛' in target.effect and any(
            word in observation for word in ('自然睫毛', '自然可见', '原生睫毛',
                                             '保持原样', '保持自然状态', '未见足以确认明显睫毛膏'))):
        return False
    if not target.required and target.region.value == 'eyebrow' and target.role == 'brow' and any(
            word in observation for word in ('保留自然', '未见明显浓重填色', '没有明显浓重填色', '无需补色')):
        return False
    if not target.required and '可能受光线影响' in observation:
        return False
    if (target.role == 'highlight' and target.region.value == 'complexion'
            and ('光线' in target.evidence or '反光' in target.evidence)
            and not any(word in target.evidence for word in ('明确的高光妆效', '可见高光粉'))):
        return False
    uncertain_effect = ('不能确认这是妆效', '无法确认是否为妆效', '无法确认是否为腮红',
                        '无法确认这一效果来自底妆还是光线', '难以区分底妆与光线',
                        '看不出是否上妆', '无法与光线影响区分',
                        '可能只是光线', '仅为光线', '看不清该部位', '无法区分产品反光与光线',
                        '完全不可见', '全部不可见', '完全被遮挡', '全部被遮挡',
                        '无法看清该部位')
    if any(phrase in target.evidence for phrase in uncertain_effect):
        return False
    if target.required or target.confidence in (Confidence.medium, Confidence.high):
        return True
    return any(word in target.evidence for word in ('可见', '明显', '呈现', '有轻微'))


def _fill_missing_visible_regions(analysis: LookAnalysis, found: list[MakeupTarget]) -> list[MakeupTarget]:
    """Recover a visibly observed region when target decomposition omits it."""
    covered = {target.region for target in found}
    roles = {'complexion': 'base', 'eyebrow': 'brow', 'eye': 'base',
             'cheek': 'blush', 'lip': 'lip'}
    for feature in analysis.features:
        if feature.region in covered:
            continue
        if any(word in feature.evidence for word in ('不可见', '被遮挡', '无法看清')):
            continue
        if feature.confidence == Confidence.low and not any(
                word in feature.evidence for word in ('可见', '明显', '呈现')):
            continue
        found.append(MakeupTarget(id=f'visible-{feature.region.value}', region=feature.region,
            role=roles[feature.region.value], effect=feature.effect,
            placement=feature.placement or feature.region.value,
            color_family=feature.color_family, finish=feature.finish,
            intensity=feature.intensity, evidence=feature.evidence,
            confidence=feature.confidence, color_samples=feature.color_samples,
            image_boxes=feature.color_boxes,
            required=feature.confidence == Confidence.high))
        covered.add(feature.region)
    return found


def _module_decisions(analysis: LookAnalysis) -> list[MakeupModuleDecision]:
    """Keep a complete, evidence-bound inventory for planning and review."""
    groups = {
        'base': lambda t: t.region.value == 'complexion' and t.role == 'base',
        'brow': lambda t: t.region.value == 'eyebrow',
        'eyes': lambda t: t.region.value == 'eye',
        'blush': lambda t: t.region.value == 'cheek' and t.role == 'blush',
        'contour_highlight': lambda t: t.role in ('contour', 'highlight'),
        'lips': lambda t: t.region.value == 'lip',
        'decoration': lambda t: False,
        'overall': lambda t: False,
    }
    decisions = []
    for module, belongs in groups.items():
        targets = [t for t in analysis.targets if belongs(t)]
        active = [t for t in targets if _actionable_target(t)]
        if module == 'overall':
            status, evidence = 'preserve', '、'.join(analysis.overall_style)
        elif module == 'decoration':
            visible_notes = [text for text in [*analysis.signature_features,
                *(f.effect for f in analysis.features)] if any(
                word in text for word in ('贴钻', '亮片', '装饰', '贴纸', '珠光点缀'))]
            status = 'optional' if visible_notes else 'preserve'
            evidence = '；'.join(visible_notes) if visible_notes else '参考图没有可独立确认的装饰物'
        elif active:
            status = 'light' if all(t.intensity == 'light' or t.confidence == Confidence.low
                                    for t in active) else 'reproduce'
            evidence = '；'.join(t.evidence for t in active)
        elif module == 'eyes' and targets and all(
                t.region.value == 'eye' and '睫毛' in t.effect and not _actionable_target(t)
                for t in targets):
            status, evidence = 'preserve', '自然睫毛可见，保持原样'
        elif targets:
            status, evidence = 'uncertain', '；'.join(t.evidence for t in targets)
        elif module == 'brow' and any('眉' in note or '刘海' in note for note in analysis.uncertain_or_invisible):
            status, evidence = 'occluded', '眉部被遮挡，保留原有眉形'
        else:
            status, evidence = 'preserve', '参考图没有可独立确认的妆效'
        decisions.append(MakeupModuleDecision(module=module, status=status,
            target_ids=[t.id for t in active], evidence=evidence))
    return decisions


def _card_matches_target(card: TechniqueCard, target: MakeupTarget) -> bool:
    if card.region != target.region:
        return False
    if target.region.value == 'eye':
        if target.role == 'line':
            return card.id == 'eye-short-wing' if '眼尾拉长' in target.effect else card.id == 'eye-lashline'
        if target.role in ('base','depth','highlight'):
            if target.role == 'highlight':
                return card.id == 'eye-center-highlight'
            return ('eyeshadow' in card.product_categories
                    and card.id not in ('eye-primer', 'eye-center-highlight'))
        if target.role == 'finish' and '睫毛' in target.effect:
            return 'mascara' in card.product_categories and not any(
                word in target.effect + target.evidence for word in ('自然睫毛', '自然可见', '原生睫毛', '保持原样'))
        return False
    if target.region.value == 'cheek' and target.role == 'blush':
        return 'blush' in card.product_categories
    if target.region.value == 'complexion' and target.role == 'base':
        reference = ' '.join((target.effect, target.placement, target.evidence))
        if card.id == 'complexion-under-eye-conceal' and not any(
                word in reference for word in ('眼下', '黑眼圈', '泪沟', '暗沉')):
            return False
        if card.id == 'complexion-spot-conceal' and not any(
                word in reference for word in ('瑕疵', '斑点', '痘印', '泛红', '遮瑕')):
            return False
        return 'base' in card.product_categories
    if target.region.value == 'lip':
        if card.id == 'lip-balm-prep':
            return False
        if card.id == 'lip-center-gloss':
            return target.role == 'finish' or any(word in target.effect for word in ('光泽', '水光', '亮泽'))
        return 'lip' in card.product_categories
    if target.region.value == 'eyebrow':
        return 'brow' in card.product_categories or 'none' in card.product_categories
    if target.role == 'contour':
        return 'contour' in card.product_categories
    if target.role == 'highlight':
        return 'highlighter' in card.product_categories
    return True


def _keep_visible_blush(text: str) -> str:
    """A skin concern must not move blush away from the observed cheek zone."""
    return re.sub(r'(?:避开|绕开)[^，。；]*泛红[^，。；]*',
                  '在参考图可见的面颊范围薄拍并控制叠加量', text)


def _parse_model_json(content: str) -> dict:
    try:
        return _extract_json(content)
    except ValueError as first_error:
        start, end = content.find('{'), content.rfind('}')
        if start < 0 or end < start:
            raise ModelOutputError('模型未返回 JSON 对象') from first_error
        source = content[start:end+1]
        cleaned = []
        in_string = False
        escaped = False
        for index, char in enumerate(source):
            if in_string:
                cleaned.append(char)
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            if char == ',':
                next_index = index + 1
                while next_index < len(source) and source[next_index].isspace():
                    next_index += 1
                if next_index < len(source) and source[next_index] in '}]':
                    continue
            cleaned.append(char)
        try:
            return json.loads(''.join(cleaned))
        except ValueError as final_error:
            raise ModelOutputError('模型返回的 JSON 格式错误，已尝试修复尾逗号') from final_error


class GraphState(TypedDict, total=False):
    analysis: dict
    cards: list[dict]
    adaptations: dict
    draft: dict
    choices: dict
    issues: list[str]
    rounds: int
    result: dict


def _image_parts(path: Path, analysis: LookAnalysis) -> list[dict]:
    """The original image plus honest detail crops around visible color boxes."""
    parts = []
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((1600, 1600))
        from io import BytesIO
        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=86)
        parts.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' +
                      base64.b64encode(buffer.getvalue()).decode()}})
        for feature in analysis.features:
            for box in feature.color_boxes[:1]:
                if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
                    continue
                with Image.open(path) as source:
                    source = ImageOps.exif_transpose(source).convert('RGB')
                    w, h = source.size
                    crop = source.crop(tuple(round(v * dim) for v, dim in zip(box, (w, h, w, h))))
                    crop.thumbnail((700, 700))
                    detail = BytesIO()
                    crop.save(detail, format='JPEG', quality=90)
                    parts.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' +
                                  base64.b64encode(detail.getvalue()).decode()}})
    return parts


def _parse_targets(raw: dict) -> list[MakeupTarget]:
    if not isinstance(raw.get('targets'), list):
        raise ValueError('targets 必须是数组')
    items = []
    for item in raw['targets']:
        if not isinstance(item, dict):
            raise ValueError('每个妆效目标必须是对象')
        clean = dict(item)
        region_aliases = {'底妆': 'complexion', '面部': 'complexion', '眉部': 'eyebrow',
                          '眉毛': 'eyebrow', '眼部': 'eye', '眼妆': 'eye',
                          '面颊': 'cheek', '脸颊': 'cheek', '唇部': 'lip', '嘴唇': 'lip'}
        clean['region'] = region_aliases.get(clean.get('region'), clean.get('region'))
        clean['confidence'] = {'高': 'high', '中': 'medium', '低': 'low',
                               '较高': 'high', '较低': 'low'}.get(
                                   clean.get('confidence'), clean.get('confidence'))
        if clean.get('intensity') is not None:
            clean['intensity'] = {'轻': 'light', '浅': 'light', '中': 'medium',
                                  '强': 'strong', '浓': 'strong'}.get(
                                      clean['intensity'], clean['intensity'])
        for field in ('color_family', 'finish'):
            if isinstance(clean.get(field), str):
                import re
                clean[field] = [part.strip() for part in re.split(r'[，,、；;]', clean[field]) if part.strip()]
        target = MakeupTarget.model_validate(clean)
        if target.required and target.confidence.value == 'low':
            raise ValueError(f'{target.id}: 低置信妆效目标不能标记为必做')
        items.append(target)
    return items


def _with_product_prerequisite(instruction: str, action: str,
                               match: ProductMatch) -> tuple[str, str]:
    """Keep the written operation conditional when no usable product is verified."""
    if match.status not in ('missing', 'catalog_candidate', 'owned_approximation'):
        return instruction, action
    product_claim = re.compile(
        r'(?:现有的?|已有的?|手头的?)(?=[^，。；：]{0,8}'
        r'(?:粉底|底妆|眉粉|眉笔|眉胶|眼影|眼线|睫毛膏|腮红|修容|高光|'
        r'口红|唇膏|唇彩|唇蜜|唇部产品|化妆品|产品))')
    instruction = product_claim.sub('准备好的', instruction)
    action = product_claim.sub('准备好的', action)
    label = PRODUCT_LABELS.get(match.required_category, '对应产品')
    if match.status == 'missing':
        prefix = f'目前没有已确认可用的{label}；先准备并核对用途与实物颜色，取得合适产品后再操作：'
        short = f'取得并确认适合的{label}后，'
    elif match.status == 'catalog_candidate':
        prefix = f'目录候选{label}尚未验证实物颜色，也不代表你已拥有；先核对用途与实际颜色，取得合适产品后再操作：'
        short = f'核对{label}实际颜色并取得合适产品后，'
    else:
        prefix = f'已有同类{label}，但颜色只由标注信息支持；先核对实物颜色，确认适合目标妆效后再操作：'
        short = f'确认已有{label}实物颜色适合后，'
    return prefix + instruction, short + action


class ModelRoles:
    def __init__(self):
        key = os.getenv('DASHSCOPE_API_KEY')
        if not key:
            raise RuntimeError('DASHSCOPE_API_KEY 未配置，无法运行个性化教程 Agent')
        self.client = OpenAI(api_key=key, base_url=os.getenv(
            'QWEN_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1'),
            timeout=float(os.getenv('TUTORIAL_MODEL_TIMEOUT_SECONDS', '90')), max_retries=0)
        self.text_model = os.getenv('TUTORIAL_TEXT_MODEL') or os.getenv('QWEN_AGENT_MODEL', 'qwen-plus')
        self.vision_model = os.getenv('QWEN_VISION_MODEL', 'qwen3-vl-plus')

    def __call__(self, role: str, payload: dict, image_path: Path | None = None,
                 analysis: LookAnalysis | None = None) -> dict:
        model = self.vision_model if image_path else self.text_model
        content: str | list[dict] = json.dumps(payload, ensure_ascii=False)
        if image_path:
            content = [{'type': 'text', 'text': content}, *_image_parts(image_path, analysis)]
        args = {'model': model, 'messages': [
            {'role': 'system', 'content': (
                '你是妆教工作流中的' + role + '。仅返回一个 JSON 对象。'
                '只能引用输入中真实存在的部位、技法、面部特征、产品和证据。'
                '单张成妆图不能证明原作者的真实产品、色号或操作顺序。'
                '必须写具体的上妆位置、方向、用量、层次和完成检查。')},
            {'role': 'user', 'content': content}]}
        if not image_path:
            effort = os.getenv('QWEN_REASONING_EFFORT') or (
                'low' if model.lower().startswith('glm-5.3') else None)
            if effort:
                args['reasoning_effort'] = effort
            limit_field = ('max_completion_tokens' if model.lower().startswith(
                ('gpt-5', 'gpt-6', 'o1', 'o3', 'o4')) else 'max_tokens')
            default_limit = '2400' if '面部适配' in role else '1800'
            args[limit_field] = int(os.getenv('TUTORIAL_TEXT_MAX_TOKENS', default_limit))
        if not model.lower().startswith(('gpt-5', 'gpt-6', 'o1', 'o3', 'o4')):
            args['temperature'] = 0.1
        response = self.client.chat.completions.create(**args)
        return _parse_model_json(response.choices[0].message.content or '')


def _target_candidates(target: MakeupTarget, card: TechniqueCard,
                       products: list[OwnedProduct]) -> dict:
    """Locally vet function and color before the selection agent sees candidates."""
    categories = card.product_categories
    owned = []
    target_samples = [s for s in target.color_samples if s.confidence.value != 'low']
    for product in products:
        if not product.user_confirmed or product.category not in categories:
            continue
        if not product_satisfies_card(product, card, product.category):
            continue
        source_samples = [s for s in product.color_samples if s.confidence.value != 'low']
        distance = None
        if target_samples and source_samples:
            distance = round(sum(min(color_distance(a.hex, b.hex) for b in source_samples)
                                 for a in target_samples) / len(target_samples), 1)
        family_overlap = sum(1 for target_name in target.color_family
                             if any(target_name.casefold() in owned_name.casefold()
                                    or owned_name.casefold() in target_name.casefold()
                                    for owned_name in product.color_family if owned_name))
        # Photo distance is a ranking aid, not a calibrated likelihood.
        tier = 'photo_close' if distance is not None and distance <= 45 else (
            'metadata_close' if distance is None and family_overlap else 'uncertain')
        owned.append({'id': product.id, 'name': product.name, 'category': product.category,
                      'shade': product.shade, 'distance': distance, 'tier': tier,
                      'color_family': product.color_family, 'finish': product.finish})
    owned.sort(key=lambda p: (0 if p['tier'] == 'photo_close' else 1 if p['tier'] == 'metadata_close' else 2,
                              p['distance'] if p['distance'] is not None else 999, p['id']))
    catalog = []
    if not owned or owned[0]['tier'] == 'uncertain':
        for category in categories:
            for candidate in catalog_candidates(category, target.color_family, limit=30):
                catalog_product = OwnedProduct(id=candidate['id'], category=category,
                    name=candidate['name'], user_confirmed=False)
                if product_satisfies_card(catalog_product, card, category):
                    catalog.append(candidate)
    return {'owned': owned[:6], 'catalog': catalog[:6]}


def _product_match(target: MakeupTarget, card: TechniqueCard, candidates: dict,
                   choice: dict) -> ProductMatch:
    owned = {p['id']: p for p in candidates['owned']}
    catalog = {p['id']: p for p in candidates['catalog']}
    selected = choice.get('id')
    if selected in owned:
        p = owned[selected]
        if p['tier'] == 'uncertain':
            raise ValueError(f'{target.id}: 颜色证据不足的已有产品不能声称匹配')
        return ProductMatch(required_category=p['category'], selected_product_id=selected,
            selected_color_label=p['shade'], color_evidence='photo' if p['distance'] is not None else 'metadata',
            status='owned_match' if p['tier'] == 'photo_close' else 'owned_approximation',
            match_reason=('已有产品用途相符，照片可见颜色距离约 ' + str(p['distance']) +
                          '（仅供排序，非色号一致证明）。' if p['distance'] is not None else
                          '已有产品用途及标注的色系或质地相符，缺少可靠的实物颜色比较。'))
    if selected in catalog:
        if any(p['tier'] in ('photo_close', 'metadata_close') for p in candidates['owned']):
            raise ValueError(f'{target.id}: 已有可用候选，不能直接跳过到目录')
        p = catalog[selected]
        return ProductMatch(required_category=p['category'], status='catalog_candidate',
            match_reason='预制目录的官方列表仅提供名称或色号，未验证实物颜色；购买或使用前请自行确认。',
            catalog_product_id=p['id'], catalog_product_name=p['name'],
            catalog_source_url=p['source_url'], color_evidence='none')
    if selected not in (None, ''):
        raise ValueError(f'{target.id}: 选用了候选集之外的产品')
    if any(p['tier'] in ('photo_close', 'metadata_close') for p in candidates['owned']):
        raise ValueError(f'{target.id}: 已有符合用途和颜色证据的产品，不能标为缺货')
    return ProductMatch(required_category=card.product_categories[0], status='missing',
                        match_reason='没有用途及颜色证据足够的已有产品；目录只有色号名称，仍无法确认合适产品，请核对或补录。',
                        catalog_suggestions=[p['name'] for p in candidates['catalog'][:3]])


def run_personalized_tutorial(pipeline, image_path: Path, profile: UserProfile,
                              products: list[OwnedProduct], report: Callable,
                              model_call: Callable | None = None, *,
                              phase: str = 'full', shared: dict | None = None) -> PipelineResult | dict:
    image_path = Path(image_path)
    if phase == 'steps' and shared:
        current_analysis = LookAnalysis.model_validate(shared['analysis'])
        current_analysis.module_decisions = _module_decisions(current_analysis)
        shared = {**shared, 'analysis': current_analysis.model_dump(mode='json')}
    model_call = model_call or ModelRoles()
    cache = ArtifactCache(Path(os.getenv('MAKEUP_CACHE_PATH', 'runtime/agent-cache.sqlite3')))
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    knowledge_hash = hashlib.sha256(json.dumps(
        [c.model_dump(mode='json') for c in pipeline.knowledge.cards], sort_keys=True).encode()).hexdigest()
    text_model = os.getenv('TUTORIAL_TEXT_MODEL') or os.getenv('QWEN_AGENT_MODEL')
    key = cache.key(PROMPT_VERSION, REGION_PLAN_VERSION, TARGET_COLOR_AUDIT_VERSION,
                    PRODUCT_WORDING_VERSION, phase,
                    image_hash, knowledge_hash, text_model, os.getenv('QWEN_VISION_MODEL'),
                    profile.model_dump(mode='json') if phase != 'analysis' else None,
                    [p.model_dump(mode='json') for p in products] if phase == 'full' else None)
    trace: list[dict] = []

    def event(name, start, **extra):
        trace.append({'name': name, 'status': 'success', 'duration_ms': round((time.monotonic()-start)*1000), **extra})
        if hasattr(report, 'event'):
            report.event('trace', trace[-1])

    with cache.lock(key, timeout=30):
        cached = cache.get(key) if model_call.__class__ is ModelRoles else None
        if cached and phase == 'analysis':
            return cached
        if cached:
            result = PipelineResult.model_validate(cached)
            result.analysis.image.path = str(image_path)
            result.agent_trace = [{'name': 'tutorial_cache', 'status': 'success', 'cache_hit': True}]
            return attach_illustrations(result)

        def analyze(state: GraphState) -> GraphState:
            report(8, 'image_analysis', '正在分析参考妆面')
            start = time.monotonic()
            vision_key = cache.key('vision-v1', image_hash, os.getenv('QWEN_VISION_MODEL'))
            saved = cache.get(vision_key) if model_call.__class__ is ModelRoles else None
            analysis = LookAnalysis.model_validate(saved) if saved else pipeline.analyze_image(image_path)
            analysis.image.path = str(image_path)
            if not saved and model_call.__class__ is ModelRoles:
                cache.put(vision_key, analysis.model_dump(mode='json'))
            event('reference_vision', start, cache_hit=bool(saved))
            return {'analysis': analysis.model_dump(mode='json'), 'rounds': 0}

        def targets(state: GraphState) -> GraphState:
            report(27, 'target_decomposition', '正在拆分参考妆的颜色、位置和层次')
            start = time.monotonic()
            analysis = LookAnalysis.model_validate(state['analysis'])
            target_key = cache.key('reference-targets', PROMPT_VERSION, image_hash,
                                   os.getenv('QWEN_VISION_MODEL'))
            saved = cache.get(target_key) if model_call.__class__ is ModelRoles else None
            if saved:
                found = [MakeupTarget.model_validate(item) for item in saved]
            else:
                raw = model_call('参考妆目标识别 Agent', {
                'task': '按底妆、眉、眼影、睫毛根部或眼线、睫毛、腮红、修容高光、唇色与光泽、装饰物逐项查看原图和局部裁剪。'
                        '把每个可见部位拆为多个独立妆效目标，例如眼部浅底色、眼尾深色、眼中提亮。'
                        '返回 targets 数组，字段为 id,region,role,effect,placement,color_family,finish,'
                        'intensity,evidence,confidence,image_boxes,required。每项必须有不同 id 和可见证据。'
                        'region 只能是 complexion/eyebrow/eye/cheek/lip；role 只能是 '
                        'base/depth/highlight/line/brow/blush/contour/lip/finish；'
                        'intensity 只能是 light/medium/strong，confidence 只能是 high/medium/low。'
                        '不要把看不清的效果写成 required。具体色名必须在该目标 evidence 中有明确观察依据；'
                        '不能确定色相时 color_family 留空，但效果可见时仍须输出目标。'
                        '自然睫毛可见但没有明确睫毛妆效时只记录保持原样，required 为 false；'
                        '遮挡的眉部不要编造。颜色框仅用输入可见区域的归一化坐标。',
                'analysis': analysis.model_dump(mode='json', exclude={'image'}),
                }, image_path, analysis)
                try:
                    found = _parse_targets(raw)
                except (ValueError, TypeError) as exc:
                    corrected = model_call('目标结构修正 Agent', {
                        'task': '只修正已有可见目标的 JSON 结构，不添加新观察。返回 targets 数组。'
                                'region 只能是 complexion/eyebrow/eye/cheek/lip；'
                                'role 只能是 base/depth/highlight/line/brow/blush/contour/lip/finish；'
                                'color_family 和 finish 必须是字符串数组。字段不得遗漏。',
                        'invalid_output': raw, 'validation_error': str(exc)[:1400],
                        'visible_features': [f.model_dump(mode='json') for f in analysis.features],
                    })
                    found = _parse_targets(corrected)
                if not found or len({x.id for x in found}) != len(found):
                    raise ValueError('妆效目标识别结果为空或 ID 重复')
                visible = {f.region for f in analysis.features}
                if any(t.region not in visible or not t.evidence.strip() for t in found):
                    raise ValueError('妆效目标包含不可见部位或缺少观察依据')
                for target in found:
                    for box in target.image_boxes[:2]:
                        try:
                            target.color_samples.append(sample_color(
                                image_path, box, target.id, 'reference_photo'))
                        except (TypeError, ValueError):
                            pass
                if model_call.__class__ is ModelRoles:
                    cache.put(target_key, [t.model_dump(mode='json') for t in found])
            suspect = [t for t in found if any(
                color not in t.evidence for color in t.color_family)]
            if suspect:
                audit_key = cache.key(TARGET_COLOR_AUDIT_VERSION, image_hash,
                    os.getenv('QWEN_VISION_MODEL'),
                    [t.model_dump(mode='json') for t in suspect])
                audit = cache.get(audit_key) if model_call.__class__ is ModelRoles else None
                if not audit:
                    audit = model_call('颜色证据复核 Agent', {
                        'task': '仅复核列出的妆效目标中 color_family 的色名，逐项看原图。'
                                '返回 {"reviews":[{"id":"目标ID","supported_colors":["原色名"],'
                                '"observation":"图中直接可见的颜色证据"}]}。'
                                'supported_colors 只能从该目标原 color_family 选；色相受光线、'
                                '自然毛发或肤色干扰而无法确认时返回空数组，observation 写明不确定性。'
                                '局部像素采样可能混入肤色，不能单凭 hex 值确定眉色或眼影色。'
                                '必须为每个输入目标返回一条，不要凭妆容习惯补色。',
                        'targets': [{'id': t.id, 'region': t.region.value,
                                     'effect': t.effect, 'placement': t.placement,
                                     'evidence': t.evidence, 'color_family': t.color_family,
                                     'samples': [s.model_dump(mode='json') for s in t.color_samples]}
                                    for t in suspect],
                    }, image_path, analysis)
                reviews = audit.get('reviews')
                if (not isinstance(reviews, list) or len(reviews) != len(suspect)
                        or len({r.get('id') for r in reviews if isinstance(r, dict)}) != len(suspect)):
                    raise ValueError('颜色证据复核未覆盖全部目标')
                by_id = {r['id']: r for r in reviews}
                if set(by_id) != {t.id for t in suspect}:
                    raise ValueError('颜色证据复核返回了未知目标')
                for target in suspect:
                    item = by_id[target.id]
                    supported = item.get('supported_colors')
                    observation = item.get('observation')
                    if (not isinstance(supported, list) or
                            any(c not in target.color_family for c in supported) or
                            not isinstance(observation, str) or not observation.strip()):
                        raise ValueError(f'{target.id}: 颜色证据复核结构无效')
                    supported = [c for c in supported if c in observation]
                    removed = [c for c in target.color_family if c not in supported]
                    target.color_family = supported
                    if supported:
                        target.evidence += '；颜色复核确认：' + '、'.join(supported) + '可见。'
                    if removed:
                        target.evidence += '；其他具体色相无法确认。'
                        for sample in target.color_samples:
                            sample.confidence = Confidence.low
                        analysis.normalization_notes.append(
                            f'{target.id} 的色名 {"、".join(removed)} 缺乏可靠图像依据，未用于产品匹配。')
                if model_call.__class__ is ModelRoles and not cache.get(audit_key):
                    cache.put(audit_key, audit)
            if model_call.__class__ is ModelRoles:
                found = _fill_missing_visible_regions(analysis, found)
            analysis.targets = found
            analysis.module_decisions = _module_decisions(analysis)
            event('target_decomposition', start, count=len(found), cache_hit=bool(saved),
                  color_audits=len(suspect))
            return {'analysis': analysis.model_dump(mode='json')}

        def retrieve(state: GraphState) -> GraphState:
            report(40, 'knowledge_retrieval', '正在检索与妆效目标相符的技法')
            analysis = LookAnalysis.model_validate(state['analysis'])
            cards = pipeline.knowledge.retrieve(analysis)
            gaps = [t.id for t in analysis.targets if _actionable_target(t)
                    and not pipeline.knowledge.candidates_for_target(t, cards)]
            if gaps:
                raise ValueError('以下妆效目标缺少同部位适用技法卡：' + '、'.join(gaps))
            return {'cards': [c.model_dump(mode='json') for c in cards]}

        def adapt(state: GraphState) -> GraphState:
            report(52, 'face_adaptation', '正在将面部特征转为操作调整')
            start = time.monotonic()
            analysis = LookAnalysis.model_validate(state['analysis'])
            active_targets = [t for t in analysis.targets if _actionable_target(t)]
            review_issues = state.get('issues') or []
            adaptation_key_parts = ['face-adaptation', PROMPT_VERSION,
                [t.model_dump(mode='json') for t in active_targets],
                profile.model_dump(mode='json'), text_model]
            if review_issues:
                adaptation_key_parts.extend([state.get('adaptations'),
                                             (state.get('draft') or {}).get('steps'), review_issues])
            adaptation_key = cache.key(*adaptation_key_parts)
            saved = cache.get(adaptation_key) if model_call.__class__ is ModelRoles else None
            if saved:
                event('face_adaptation', start, cache_hit=True)
                return {'adaptations': saved}
            raw = model_call('面部适配 Agent', {
                'task': '为每个 target 返回 adaptations 字典，键是 target_id，值有 face_keys 数组、'
                        'reason、placement、direction、amount、layers、action、completion_check。'
                        'face_keys 只能从有效面部特征键中选；有效面部特征为空时 face_keys 为空，'
                        'reason 应基于妆效目标而非杜撰用户五官。用户修正的特征已在有效特征中，优先使用。'
                        '面部困扰只能调整用量、边缘或工具，不能避开参考图明确可见的目标区域。',
                'targets': [{'id': t.id, 'region': t.region.value, 'role': t.role,
                             'effect': t.effect, 'placement': t.placement,
                             'intensity': t.intensity, 'evidence': t.evidence}
                            for t in active_targets],
                'effective_face_features': profile.face_features_effective,
                'skill_level': profile.skill_level, 'preferences': profile.preferences,
                'skin_tendency': profile.skin_tendency, 'sensitivities': profile.sensitivities,
                'previous_adaptations': state.get('adaptations') if review_issues else None,
                'review_issues': review_issues,
                'selected_techniques': [
                    {'target_id': spec.get('target_id'), 'card_id': spec.get('card_id'),
                     'instruction': spec.get('instruction'),
                     'card_actions': next((card.get('actions') for card in state.get('cards', [])
                         if card.get('id') == spec.get('card_id')), [])}
                    for spec in (state.get('draft') or {}).get('steps', [])
                ] if review_issues else [],
            })
            adaptations = raw.get('adaptations', {})
            if not isinstance(adaptations, dict):
                raise ValueError('面部适配结果格式错误')
            if review_issues:
                adaptations = {**state.get('adaptations', {}), **adaptations}
            expected = {t.id for t in active_targets}
            missing = expected - set(adaptations)
            if missing:
                supplement = model_call('面部适配补全 Agent', {
                    'task': '只为 missing_target_ids 生成 adaptations 字典；每项必须有 face_keys,reason,'
                            'placement,direction,amount,layers,action,completion_check。'
                            '不得用通用占位内容，必须结合该目标及有效面部资料。',
                    'missing_target_ids': sorted(missing),
                    'targets': [{'id': t.id, 'region': t.region.value, 'effect': t.effect,
                                 'placement': t.placement, 'evidence': t.evidence}
                                for t in analysis.targets if t.id in missing],
                    'effective_face_features': profile.face_features_effective,
                    'previous_adaptations': adaptations,
                }).get('adaptations', {})
                if isinstance(supplement, dict):
                    adaptations.update(supplement)
            if not expected.issubset(adaptations):
                raise ValueError('面部适配遗漏必要妆效目标')
            for tid, item in adaptations.items():
                if tid not in {t.id for t in analysis.targets} or not isinstance(item, dict):
                    raise ValueError('面部适配引用了不存在的目标')
                if any(k not in profile.face_features_effective for k in item.get('face_keys', [])):
                    raise ValueError('面部适配引用了不存在的面部观察')
                ApplicationGuide.model_validate({k: item.get(k) for k in
                    ('placement', 'direction', 'amount', 'layers', 'action', 'completion_check')})
            if model_call.__class__ is ModelRoles:
                cache.put(adaptation_key, adaptations)
            event('face_adaptation', start)
            return {'adaptations': adaptations}

        def draft(state: GraphState) -> GraphState:
            report(65, 'tutorial_planning', '正在编排目标、技法和操作步骤')
            start = time.monotonic()
            analysis = LookAnalysis.model_validate(state['analysis'])
            cards = [TechniqueCard.model_validate(c) for c in state['cards']]
            active = [t for t in analysis.targets if _actionable_target(t)]
            regions = [r for r in ('complexion', 'eyebrow', 'eye', 'cheek', 'lip')
                       if any(t.region.value == r for t in active)]
            previous = state.get('draft') or {}
            old_plans = previous.get('region_plans') or {}
            issues = state.get('issues') or []
            affected = set(regions) if not old_plans else set()
            for issue in issues:
                for target in active:
                    if target.id in issue or target.region.value in issue:
                        affected.add(target.region.value)
                region_terms = {
                    'complexion': ('底妆', '粉底', '遮瑕', '眼下暗沉', '定妆'),
                    'eyebrow': ('眉毛', '眉粉', '眉笔', '眉胶', '眉头', '眉尾'),
                    'eye': ('眼影', '眼线', '睫毛', '眼型', '眼皮'),
                    'cheek': ('腮红', '面颊', '颧骨', '修容'),
                    'lip': ('嘴唇', '唇色', '唇妆', '唇部', '唇面', '口红', '唇膏', '唇釉'),
                }
                for candidate_region, words in region_terms.items():
                    if any(word in issue for word in words):
                        affected.add(candidate_region)
                import re
                for number in re.findall(r'步骤\s*(\d+)', issue):
                    old_steps = previous.get('steps') or []
                    if 0 < int(number) <= len(old_steps):
                        target_id = old_steps[int(number)-1].get('target_id')
                        target = next((t for t in active if t.id == target_id), None)
                        if target:
                            affected.add(target.region.value)
            if issues and not affected:
                affected = set(regions)
            plans = dict(old_plans)
            for region in regions:
                if region not in affected and region in plans:
                    continue
                region_targets = [t for t in active if t.region.value == region]
                region_cards = [c for c in cards if c.region.value == region]
                if not region_cards:
                    raise ValueError(f'{region} 没有可用技法卡')
                plan_key = cache.key('region-plan', REGION_PLAN_VERSION, region,
                    [t.model_dump(mode='json') for t in region_targets],
                    [c.model_dump(mode='json') for c in region_cards],
                    {t.id: state['adaptations'][t.id] for t in region_targets},
                    profile.model_dump(mode='json'),
                    [p.model_dump(mode='json') for p in products] if phase == 'full' else [],
                    state.get('choices'), text_model,
                    plans.get(region), issues)
                saved_plan = cache.get(plan_key) if model_call.__class__ is ModelRoles else None
                raw = saved_plan or model_call('教程规划 Agent', {
                    'task': '只规划当前部位，返回 {steps:[...],personalized_changes:[...]}。'
                            '每步输出 target_id,card_id,category,goal,instruction,success_signal,'
                            'tool,depends_on,preserves_features,application,drawing。'
                            'drawing 使用程序锚点 left_eye/right_eye/left_cheek/right_cheek/'
                            'left_brow/right_brow/lips/nose/face，字段为 anchor,side,offset_x,offset_y,'
                            'radius_x,radius_y,direction,strength,layers。偏移和半径相对脸宽，'
                            '不得编造图片绝对坐标。side 只能是 left/right/both/center；'
                            'offset_x/offset_y 靠近部位锚点：眉和眼的绝对值不超过 0.07 脸宽，'
                            '面颊不超过 0.10，唇不超过 0.08，底妆不超过 0.12；'
                            'radius_x 大于 0 且不超过 0.4，'
                            'radius_y 大于 0 且不超过 0.3；direction 只能是 '
                            'outward/inward/upward/downward/along，strength 只能是 '
                            'light/medium/strong，layers 为 1 到 4 的整数。'
                            'application 必须是 {placement,direction,amount,layers,action,completion_check}，'
                            '其中 layers 是描述层次的字符串，例如“1 层”，不能给数字；'
                            '直接对应同一步的 instruction、技法与工具，不要照搬其他步骤的动作。'
                            '每个输入目标至少一步，包括颜色不确定但妆效可见的浅眼影、睫毛根部、腮红；'
                            '颜色不确定时用部位和深浅描述，不删除整个步骤。'
                            '完整妆容模板覆盖底妆、眉、眼、腮红、修容高光、唇、装饰与整体；'
                            '只规划当前部位中有可见证据的目标，遮挡或未观察到的部位不要编造。'
                            '完整覆盖可见妆效，同一卡可在不同目标复用。'
                            '如果参考图只支持保留自然眉毛且无需补色，可用眉头梳柔技法的 none 类别，'
                            '只整理毛流；选择 brow 类别时必须实际描述少量补色或定型，不能只梳理。'
                            '位置、方向、用量、层次和完成标准要结合面部适配，不能只复制技法卡。'
                            '没有图片或产品证据时不要指定精确色号、质地或假设用户有某产品。'
                            '用户缺少卡片工具时优先选择不依赖该工具的其他同目标技法；'
                            '没有有据可依的替代技法时写明需要准备原工具，不编造替代动作。'
                            '卡片关键动作中使用的工具要逐一写明，不可把铺色刷和晕染刷笼统称为眼影刷。'
                            '用户面部特征必须实际改变落点、方向或范围，不能只列出特征名称。'
                            '每一步必须直接服务其 target；参考图与资料没有眼下暗沉时不要添加眼下遮瑕。'
                            '眼部睫毛根部加深只用贴根部的细眼线技法，不补写睫毛膏；'
                            '腮红可见时在目标面颊范围薄拍，局部泛红只控制叠加量与边缘。'
                            '用户资料仅说局部泛红可见时不能编造具体泛红位置；面颊阴影只是可见明暗，不能称为凹陷。'
                            '若本轮提供已选产品，要使工具、产品形态和动作一致。'
                    'depends_on 可以是本部位前序步骤序号或唯一的前序 target_id；target_id 只能从当前 targets 选，'
                            'card_id 只能从当前 cards 选，不输出其他模块步骤。',
                    'region': region,
                    'module_decisions': [decision.model_dump(mode='json')
                                         for decision in analysis.module_decisions],
                    'targets': [{'id': t.id, 'role': t.role, 'effect': t.effect,
                                 'placement': t.placement, 'intensity': t.intensity,
                                 'color_family': t.color_family, 'finish': t.finish,
                                 'evidence': t.evidence, 'required': t.required,
                                 'confidence': t.confidence.value}
                                for t in region_targets],
                    'cards': [{'id': c.id, 'title': c.title, 'categories': c.product_categories,
                               'tools': c.tools, 'actions': [a.model_dump() for a in c.actions],
                               'adaptation_rules': c.adaptation_rules}
                              for c in region_cards],
                    'allowed_cards_by_target': {t.id: [c.id for c in region_cards
                                                         if _card_matches_target(c,t)]
                                                for t in region_targets},
                    'adaptations': {t.id: state['adaptations'][t.id] for t in region_targets},
                    'profile': {'skill_level': profile.skill_level,
                                'must_keep': profile.must_keep, 'preferences': profile.preferences,
                                'available_tools': profile.available_tools,
                                'face_features_effective': profile.face_features_effective,
                                'owned_products': []},
                    'previous_region_plan': plans.get(region), 'review_issues': issues,
                    'selected_products': [
                        {'target_id': previous['steps'][i].get('target_id'),
                         'product_match': match}
                        for i, match in ((int(i), match) for i, match in
                                         (state.get('choices') or {}).items())
                        if i < len(previous.get('steps') or [])
                        and previous['steps'][i].get('target_id') in {t.id for t in region_targets}
                    ],
                })
                if not isinstance(raw.get('steps'), list) or not raw['steps']:
                    raise ValueError(f'{region} 规划 Agent 没有产生步骤')
                allowed_targets = {t.id for t in region_targets}
                allowed_cards = {c.id for c in region_cards}
                compatible = {t.id: {c.id for c in region_cards if _card_matches_target(c,t)}
                              for t in region_targets}
                cards_by_id = {card.id: card for card in region_cards}
                def normalize_plan(candidate: dict) -> None:
                    for spec in candidate.get('steps') or []:
                        if not isinstance(spec, dict) or not isinstance(spec.get('application'), dict):
                            continue
                        layers = spec['application'].get('layers')
                        if isinstance(layers, (int, float)) and not isinstance(layers, bool) and layers > 0:
                            spec['application']['layers'] = f'{layers:g} 层'

                def plan_issue(candidate: dict) -> str | None:
                    specs = candidate.get('steps')
                    if not isinstance(specs, list) or not specs:
                        return 'steps 必须是非空数组'
                    if not allowed_targets.issubset({spec.get('target_id') for spec in specs
                            if isinstance(spec, dict)}):
                        return '没有覆盖当前区域的全部妆效目标'
                    for spec in specs:
                        if not isinstance(spec, dict) or spec.get('target_id') not in allowed_targets:
                            return '步骤引用了其他部位或未知目标'
                        if spec.get('card_id') not in allowed_cards or spec.get('card_id') not in compatible[spec['target_id']]:
                            return '步骤引用了不适合该目标的技法卡'
                        try:
                            ApplicationGuide.model_validate(spec.get('application'))
                            if phase == 'steps' or spec.get('drawing'):
                                drawing = DrawingGuide.model_validate(spec.get('drawing'))
                                anchor_limit = {
                                    'eyebrow': .07, 'eye': .07, 'cheek': .10,
                                    'lip': .08, 'complexion': .12,
                                }[region]
                                if (abs(drawing.offset_x) > anchor_limit or
                                        abs(drawing.offset_y) > anchor_limit):
                                    return (f"{spec.get('target_id')}: drawing 偏移超出{region}锚点附近"
                                            f' ±{anchor_limit} 脸宽；须保持绘图椭圆与目标部位相交')
                        except (ValueError, TypeError) as exc:
                            return f"{spec.get('target_id')}: {str(exc)[:900]}"
                        selected_card = cards_by_id[spec['card_id']]
                        card_actions = ' '.join(action.instruction for action in selected_card.actions)
                        step_actions = ' '.join(str(spec.get(field) or '') for field in ('instruction', 'tool'))
                        step_actions += ' ' + str((spec.get('application') or {}).get('action') or '')
                        for required_tool in selected_card.tools:
                            if required_tool in card_actions and required_tool not in step_actions:
                                return f"{spec.get('target_id')}: 技法卡关键动作需要{required_tool}，步骤未明确使用或准备"
                    return None

                normalize_plan(raw)
                issue = plan_issue(raw)
                if issue:
                    raw = model_call('区域规划修正 Agent', {
                        'task': '只修正当前区域步骤。返回 {steps:[...],personalized_changes:[...]}；'
                                '移除其他区域步骤，并为当前区域每个输入目标保留至少一步。'
                                '每步仍须提供 target_id,card_id,category,goal,instruction,'
                                'success_signal,application,drawing；application 包含'
                                'placement,direction,amount,layers,action,completion_check。'
                                'drawing.anchor 只能是 left_eye/right_eye/left_cheek/right_cheek/'
                                'left_brow/right_brow/lips/nose/face；side 为 left/right/both/center；'
                                'offset_x/offset_y 的绝对值在眉眼不超过 0.07 脸宽、面颊不超过 0.10、'
                                '唇不超过 0.08、底妆不超过 0.12；'
                                'radius_x 在 (0,0.4]，radius_y 在 (0,0.3]；'
                                'direction 为 outward/inward/upward/downward/along；strength 为 '
                                'light/medium/strong；layers 为 1 到 4 的整数。'
                                '必须写明所选技法卡关键动作中用到的每一种工具，不能用笼统工具名代替。'
                                '不得新增目标或技法。',
                        'region': region,
                        'validation_error': issue,
                        'allowed_targets': [t.model_dump(mode='json') for t in region_targets],
                        'allowed_cards': [{'id': c.id, 'product_categories': c.product_categories,
                                           'actions': [a.model_dump() for a in c.actions]}
                                          for c in region_cards],
                        'allowed_cards_by_target': {key: sorted(value) for key,value in compatible.items()},
                        'invalid_plan': raw,
                    })
                    normalize_plan(raw)
                    second_issue = plan_issue(raw)
                    if second_issue:
                        raise ValueError(f'{region} 规划 Agent 两次输出无效：{second_issue}')
                if model_call.__class__ is ModelRoles and not saved_plan:
                    cache.put(plan_key, raw)
                plans[region] = raw
            steps = []
            changes = []
            for region in regions:
                segment = plans[region]
                offset = len(steps)
                for local_index, spec in enumerate(segment['steps'], start=1):
                    spec = dict(spec)
                    deps = spec.get('depends_on') or []
                    if isinstance(deps, (int, str)):
                        deps = [deps]
                    if not isinstance(deps, list):
                        raise ValueError(f'{region} 步骤 {local_index} 的 depends_on 必须是序号数组')
                    normalized_deps = []
                    for dep in deps:
                        if isinstance(dep, str) and not dep.isdigit():
                            preceding = [index for index, previous_spec in
                                enumerate(segment['steps'][:local_index-1], start=1)
                                if dep in (previous_spec.get('target_id'), previous_spec.get('step_id'))]
                            if len(preceding) != 1:
                                raise ValueError(f'{region} 步骤 {local_index} 的依赖标识不唯一或不存在')
                            local_dep = preceding[0]
                        elif (isinstance(dep, int) and not isinstance(dep, bool)) or isinstance(dep, str) and dep.isdigit():
                            local_dep = int(dep)
                        else:
                            raise ValueError(f'{region} 步骤 {local_index} 含无效依赖标识')
                        if not 0 < local_dep < local_index:
                            raise ValueError(f'{region} 步骤 {local_index} 依赖不存在或不在前面')
                        normalized_deps.append(offset + local_dep)
                    spec['depends_on'] = normalized_deps
                    if isinstance(spec.get('preserves_features'), str):
                        spec['preserves_features'] = [spec['preserves_features']]
                    steps.append(spec)
                for change in segment.get('personalized_changes') or []:
                    if isinstance(change, str):
                        changes.append(change)
                    elif isinstance(change, dict):
                        changes.append('；'.join(str(value) for value in change.values()
                                                if isinstance(value, (str, int, float))))
            if not steps:
                raise ValueError('规划 Agent 没有产生步骤')
            result = {'title': '你的专属妆教', 'look_summary': '、'.join(analysis.overall_style),
                      'preserved_features': profile.must_keep,
                      'personalized_changes': list(dict.fromkeys(changes)),
                      'steps': steps, 'region_plans': plans}
            event('tutorial_planning', start, steps=len(steps), regions=len(regions))
            return {'draft': result}

        def select_products_impl(state: GraphState) -> GraphState:
            report(76, 'product_selection', '正在核对已有产品和目录候选')
            start = time.monotonic()
            targets_by_id = {t.id: t for t in LookAnalysis.model_validate(state['analysis']).targets}
            cards_by_id = {c['id']: TechniqueCard.model_validate(c) for c in state['cards']}
            verified = {}
            for index, spec in enumerate(state['draft']['steps']):
                report(76 + min(7, int(index * 8 / max(1, len(state['draft']['steps'])))),
                       'product_selection', f'正在核对第 {index+1} / {len(state["draft"]["steps"])} 步的产品')
                target = targets_by_id.get(spec.get('target_id'))
                card = cards_by_id.get(spec.get('card_id'))
                if not target or not card or card.region != target.region:
                    raise ValueError(f'规划步骤 {index+1} 引用了无效目标或跨部位技法')
                if spec.get('category') not in card.product_categories:
                    raise ValueError(f'规划步骤 {index+1} 的产品用途与技法不符')
                if spec.get('category') == 'none':
                    verified[str(index)] = ProductMatch(
                        required_category='none', status='not_needed',
                        match_reason='此步仅整理已有毛流，不使用彩妆产品。').model_dump(mode='json')
                    continue
                candidates = _target_candidates(target, card, products)
                good_owned = [p for p in candidates['owned']
                              if p['tier'] in ('photo_close', 'metadata_close')]
                if not good_owned and not candidates['catalog']:
                    choice = {'id': None}
                elif len(good_owned) == 1:
                    choice = {'id': good_owned[0]['id']}
                elif not good_owned and len(candidates['catalog']) == 1:
                    choice = {'id': candidates['catalog'][0]['id']}
                else:
                    choice_key = cache.key('product-decision-v2', PROMPT_VERSION,
                        target.model_dump(mode='json'), card.id, candidates,
                        text_model)
                    saved_choice = cache.get(choice_key) if model_call.__class__ is ModelRoles else None
                    choice = saved_choice or model_call('产品选择 Agent', {
                        'task': '只选择当前一步，返回 {id}。优先已有产品；'
                                '仅当已有产品颜色证据不足时选目录候选。'
                                '目录只按官方色号名称提供弱候选，不能说成已拥有或色彩精确一致。',
                        'target': {'id': target.id, 'effect': target.effect,
                                   'color_family': target.color_family, 'finish': target.finish,
                                   'color_samples': [s.model_dump(mode='json') for s in target.color_samples]},
                        'card': {'id': card.id, 'category': spec['category'],
                                 'required_product_kinds': card.required_product_kinds},
                        'candidates': candidates,
                    })
                    try:
                        _product_match(target, card, candidates, choice)
                    except ValueError as exc:
                        choice = model_call('产品选择修正 Agent', {
                            'task': '只修正当前一步，返回 {id}，id 必须来自候选；'
                                    '已有产品有可靠颜色证据时不能选目录或缺货。',
                            'target_id': target.id, 'candidates': candidates,
                            'invalid_choice': choice, 'validation_error': str(exc),
                        })
                try:
                    verified[str(index)] = _product_match(
                        target, card, candidates, choice).model_dump(mode='json')
                except ValueError as exc:
                    raise RuntimeError(f'步骤 {index+1} 产品 Agent 未能在候选集中做出合规选择：{exc}') from exc
                if (model_call.__class__ is ModelRoles and
                        (len(good_owned) > 1 or (not good_owned and len(candidates['catalog']) > 1))
                        and not saved_choice):
                    cache.put(choice_key, choice)
            event('product_selection', start)
            return {'choices': verified, 'issues': []}

        def select_products(state: GraphState) -> GraphState:
            try:
                return select_products_impl(state)
            except (ValueError, KeyError, TypeError) as exc:
                return {'issues': [f'产品或步骤选择未通过校验：{exc}'],
                        'rounds': state.get('rounds', 0)+1}

        def build_plan(state: GraphState) -> TutorialPlan:
            analysis = LookAnalysis.model_validate(state['analysis'])
            targets_by_id = {t.id: t for t in analysis.targets}
            cards_by_id = {c['id']: TechniqueCard.model_validate(c) for c in state['cards']}
            specs = state['draft']['steps']
            steps = []
            identifiers: dict[str, int] = {}
            for index, spec in enumerate(specs):
                target = targets_by_id[spec['target_id']]
                card = cards_by_id[spec['card_id']]
                adaptation = state['adaptations'][target.id]
                guide = ApplicationGuide.model_validate(spec.get('application'))
                category = str(spec.get('category') or card.product_categories[0])
                identity = f'{target.id}:{card.id}:{category}'
                identifiers[identity] = identifiers.get(identity, 0) + 1
                match = (ProductMatch.model_validate(state['choices'][str(index)])
                         if phase == 'full' else ProductMatch(
                             required_category=category,
                             status='not_needed' if category == 'none' else 'pending',
                             match_reason='该步骤无需产品' if category == 'none' else '正在独立匹配产品'))
                instruction = str(spec.get('instruction') or guide.action)
                if phase == 'full':
                    instruction, guide.action = _with_product_prerequisite(instruction, guide.action, match)
                if target.region.value == 'cheek' and target.role == 'blush':
                    instruction = _keep_visible_blush(instruction)
                    for field in ('placement','direction','action'):
                        setattr(guide,field,_keep_visible_blush(getattr(guide,field)))
                keys = adaptation.get('face_keys', [])
                face_evidence = [f'{k}：{profile.face_features_effective[k]}' for k in keys]
                relevant_tools = _tools_used_by_step(card, instruction, guide.action)
                tools = relevant_tools if profile.available_tools is None else [
                    t for t in relevant_tools if t in profile.available_tools]
                chosen_tool = (spec.get('tool') if spec.get('tool') in tools else
                               (tools[0] if tools else None))
                tool_status = ('available' if chosen_tool else 'not_needed') if profile.available_tools is not None else None
                if relevant_tools and profile.available_tools is not None and not tools:
                    chosen_tool = relevant_tools[0]
                    tool_status = 'prepare'
                    instruction = f'先准备{chosen_tool}；' + instruction
                steps.append(TutorialStep(
                    order=index+1, region=target.region, target_id=target.id,
                    step_id=f'{identity}:{identifiers[identity]}',
                    target_role=target.role, target_evidence=target.evidence,
                    face_evidence=face_evidence, application=guide,
                    drawing=DrawingGuide.model_validate(spec['drawing']) if spec.get('drawing') else None,
                    goal=str(spec.get('goal') or target.effect),
                    instruction=instruction,
                    success_signal=str(spec.get('success_signal') or guide.completion_check),
                    product_match=match,
                    product_requirement=ProductRequirement(category=category,
                        purpose=target.effect, suitable_forms=card.required_product_kinds),
                    tool=chosen_tool, tool_status=tool_status,
                    adjustment_reason=str(adaptation.get('reason') or target.effect),
                    technique_card_id=card.id,
                    caution='；'.join(profile.sensitivities) if profile.sensitivities else None,
                    depends_on=spec.get('depends_on') or [],
                    preserves_features=spec.get('preserves_features') or [],
                ))
            preserved = list(dict.fromkeys([*profile.must_keep, *state['draft'].get('preserved_features', [])]))
            plan = TutorialPlan(provider='langgraph:1.2.14', title=state['draft'].get('title') or '你的专属妆教',
                look_summary=state['draft'].get('look_summary') or '、'.join(analysis.overall_style),
                preserved_features=preserved,
                personalized_changes=state['draft'].get('personalized_changes') or
                    [f'依据 {len(profile.face_features_effective)} 项面部信息调整操作'],
                steps=steps,
                missing_products=sorted({s.product_match.required_category for s in steps
                                         if s.product_match.status == 'missing'}),
                uncertainty_notice='参考照片只能支持可见妆效判断；产品色号、真实操作顺序和上脸效果需现场确认。',
                estimated_minutes=None)
            attach_technique_sources(plan, list(cards_by_id.values()))
            return plan

        def review(state: GraphState) -> GraphState:
            report(88, 'quality_review', '正在独立检查妆效、面部依据和产品匹配')
            start = time.monotonic()
            try:
                plan = build_plan(state)
            except (ValueError, KeyError, TypeError) as exc:
                return {'issues': [f'教程结构未通过校验：{exc}'],
                        'rounds': state.get('rounds', 0)+1}
            analysis = LookAnalysis.model_validate(state['analysis'])
            cards = [TechniqueCard.model_validate(c) for c in state['cards']]
            errors = validate_tutorial(plan, profile, products, cards, analysis)
            covered = {s.target_id for s in plan.steps}
            errors.extend(f'遗漏必要目标 {t.id}' for t in analysis.targets
                          if t.required and _actionable_target(t) and t.id not in covered)
            errors.extend(f'遗漏可见目标 {t.id}，颜色不确定不能删除整个部位'
                          for t in analysis.targets if _actionable_target(t) and t.id not in covered)
            preparatory_cards = {'lip-balm-prep', 'eye-primer'}
            for target in analysis.targets:
                if (target.required and target.id in covered and
                        all(step.technique_card_id in preparatory_cards
                            for step in plan.steps if step.target_id == target.id)):
                    errors.append(f'{target.id} 只有准备步骤，尚未实现参考图妆效')
            for step in plan.steps:
                if (step.product_match.status != 'not_needed' and step.region.value == 'eyebrow'
                        and any(term in step.instruction for term in ('不补色', '不加眉粉', '只梳理'))):
                    errors.append(f'步骤 {step.order}: 眉妆步骤明确不使用产品，却仍要求产品；应选无需产品类别')
            raw = model_call('独立审查 Agent', {
                'task': '返回 {"issues":[{"orders":[步骤序号],"issue":"具体问题"}]}；'
                        'orders 必须列出受影响步骤的数字，不能省略。'
                        '逐一对照原图和局部裁剪复核底妆、眉、眼影/眼线/睫毛、腮红、修容高光、'
                        '唇色/光泽、装饰物和整体效果；可见妆效不能因颜色不确定而缺步，'
                        '遮挡的眉部不能臆造细节。检查每个步骤是否忠于参考图目标、是否真正使用面部特征、'
                        '模块状态为 uncertain、且反光无法与光线区分时不要求生成产品步骤。'
                        '操作位置/方向/用量/层次/完成标准是否明确。产品仍在独立匹配时只审查类别和用途要求。'
                        '逐步对照所选技法卡的关键动作；个性化落点和方向不能与卡片核心动作冲突。'
                        '检查 drawing 的部位锚点、左右侧、方向、强度和层数是否与步骤一致。'
                        '绘图椭圆会在无手部遮挡的用户标准底图上与对应部位蒙版相交；'
                        '不要仅凭椭圆半径推断涂到参考图中被遮挡的手指或唇外皮肤，'
                        '也不要要求绘图椭圆精确复刻参考照片的遮挡轮廓。'
                        '若 product_match 为 missing，可以说明需要某类产品并给出条件性做法；'
                        '若 product_match 为 not_needed，此步经本地技法校验无需产品，不要要求购买。'
                        '不要仅因技法卡需要粉底或口红而报错，重点检查是否虚称用户已拥有或色号已验证。'
                        '只指出可由输入证实的问题，不要求猜测不可见信息。',
                'analysis': {'style': analysis.overall_style,
                             'module_decisions': [d.model_dump(mode='json') for d in analysis.module_decisions],
                             'features': [f.model_dump(mode='json') for f in analysis.features],
                             'targets': [{'id': t.id, 'region': t.region.value, 'role': t.role,
                                          'effect': t.effect, 'placement': t.placement,
                                          'color_family': t.color_family,
                                          'evidence': t.evidence, 'required': t.required}
                                         for t in analysis.targets]},
                'user_bare_face_features': profile.face_features_effective,
                'tutorial': [{'order': s.order, 'target_id': s.target_id,
                              'target_evidence': s.target_evidence,
                              'face_evidence': s.face_evidence, 'application': s.application.model_dump(),
                              'drawing': s.drawing.model_dump(mode='json') if s.drawing else None,
                              'instruction': s.instruction, 'success_signal': s.success_signal,
                              'technique_card_id': s.technique_card_id,
                              'product_match': s.product_match.model_dump(mode='json')}
                             for s in plan.steps],
                'technique_cards': [{'id': c.id, 'actions': [a.model_dump(mode='json') for a in c.actions],
                                     'tools': c.tools, 'source_url': c.source_url,
                                     'review_status': c.review_status} for c in cards],
                'hard_errors': errors,
            }, image_path, analysis)
            issues = []
            for entry in raw.get('issues', []):
                if isinstance(entry, dict):
                    orders = entry.get('orders') or []
                    label = '、'.join(f'步骤 {order}' for order in orders if isinstance(order, int))
                    issue = str(entry.get('issue') or entry.get('reason') or '').strip()
                    if issue:
                        issues.append((label + '：' if label else '') + issue)
                elif str(entry).strip():
                    issues.append(str(entry))
            issues = list(dict.fromkeys([*errors, *issues]))
            event('independent_review', start, issues=len(issues))
            if not issues:
                plan.review_warnings.extend(pipeline.review(analysis, profile, plan.steps))
                used = {s.technique_card_id for s in plan.steps}
                reviewed = all(c.review_status != 'draft' for c in cards if c.id in used)
                if not reviewed:
                    plan.review_warnings.append('部分技法卡尚待人工复核，本教程是可编辑预览。')
                result = attach_illustrations(PipelineResult(
                    analysis=analysis, retrieved_card_ids=sorted(used), tutorial=plan,
                    knowledge_version=knowledge_hash[:16],
                    publication_status='reviewed' if reviewed else 'preview'))
                return {'issues': [], 'result': result.model_dump(mode='json')}
            return {'issues': issues, 'rounds': state.get('rounds', 0)+1}

        def route(state: GraphState):
            if not state.get('issues'):
                return END
            if state.get('rounds', 0) >= 3:
                raise ValueError('独立审查两轮返工后仍未通过：' + '；'.join(state['issues'][:5]))
            if any(word in ' '.join(state['issues']) for word in
                   ('眼型', '唇形', '眉形', '面部', 'face_evidence', '五官',
                    'application', '动作不一致', '取粉')):
                return 'adapt'
            return 'draft'

        graph = StateGraph(GraphState)
        nodes = [('analyze', analyze), ('targets', targets), ('retrieve', retrieve)] if phase == 'analysis' else (
            [('adapt', adapt), ('draft', draft), ('review', review)] if phase == 'steps' else
            [('analyze', analyze), ('targets', targets), ('retrieve', retrieve),
             ('adapt', adapt), ('draft', draft), ('select_products', select_products), ('review', review)])
        for name, fn in nodes:
            graph.add_node(name, fn)
        if phase == 'analysis':
            graph.add_edge(START, 'analyze')
            graph.add_edge('analyze', 'targets')
            graph.add_edge('targets', 'retrieve')
            graph.add_edge('retrieve', END)
        else:
            graph.add_edge(START, 'adapt' if phase == 'steps' else 'analyze')
            if phase == 'full':
                graph.add_edge('analyze', 'targets')
                graph.add_edge('targets', 'retrieve')
                graph.add_edge('retrieve', 'adapt')
            graph.add_edge('adapt', 'draft')
            if phase == 'steps':
                graph.add_edge('draft', 'review')
            else:
                graph.add_edge('draft', 'select_products')
                graph.add_conditional_edges('select_products', lambda state: route(state) if state.get('issues') else 'review')
            graph.add_conditional_edges('review', route)
        checkpoint_path = Path(os.getenv('MAKEUP_GRAPH_PATH', 'runtime/tutorial-graph.sqlite3'))
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            final = graph.compile(checkpointer=saver).invoke(
                {'rounds': 0, 'analysis': (shared or {}).get('analysis', {}),
                 'cards': (shared or {}).get('cards', []), 'adaptations': {},
                 'draft': {}, 'choices': {}, 'issues': [], 'result': {}},
                config={'configurable': {'thread_id': key}}, recursion_limit=20)
        if phase == 'analysis':
            output = {'analysis': final['analysis'], 'cards': final['cards'],
                      'trace': trace, 'knowledge_version': knowledge_hash[:16]}
            if model_call.__class__ is ModelRoles:
                cache.put(key, output)
            return output
        result = PipelineResult.model_validate(final['result'])
        result.agent_trace = trace
        if model_call.__class__ is ModelRoles:
            cache.put(key, result.model_dump(mode='json'))
        return result
