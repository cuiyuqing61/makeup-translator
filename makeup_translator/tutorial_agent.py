from __future__ import annotations

import hashlib
import json
import os
import time
from copy import deepcopy
from pathlib import Path

from .agent_runtime import AgentRuntime, EmptyInput, FunctionTool
from .constraints import validate_tutorial
from .knowledge import DEFAULT_KNOWLEDGE_PATH
from .illustrations import attach_illustrations
from .models import PipelineResult, TutorialPlan
from .cache import ArtifactCache
from .prompts import ANALYSIS_SYSTEM_PROMPT, ANALYSIS_USER_PROMPT
from .face_profile import instruction_note


def ground_tutorial_payload(payload, cards, products, profile, analysis=None):
    """Bind model-written steps to trusted card and inventory identifiers."""
    from .pipeline import match_product

    grounded = deepcopy(payload)
    cards_by_id = {card.id: card for card in cards}
    cards_by_region = {}
    for card in cards:
        cards_by_region.setdefault(card.region.value, []).append(card)

    supported_steps = []
    used_card_ids = set()
    for step in grounded.get('steps', []):
        region = step.get('region')
        card = cards_by_id.get(step.get('technique_card_id'))
        card_is_trusted = bool(card and card.region.value == region and card.id not in used_card_ids)
        if not card_is_trusted:
            candidates = [candidate for candidate in cards_by_region.get(region, [])
                          if candidate.id not in used_card_ids]
            if not candidates:
                continue
            text = ' '.join(str(step.get(field, '')) for field in
                            ('goal', 'instruction', 'success_signal')).casefold()
            requested = (step.get('product_match') or {}).get('required_category')

            def relevance(candidate):
                terms = [candidate.title, *candidate.effects, *candidate.product_categories]
                return ((20 if requested in candidate.product_categories else 0)
                        + sum(min(len(term), 8) for term in terms
                              if term and term.casefold() in text))

            card = max(candidates, key=relevance)
            step['technique_card_id'] = card.id
        # Placement, direction and completion cues must come from the same
        # authored card as the illustration. The model still supplies the
        # personalized reason and overall plan, but cannot move a diagram's
        # application area by paraphrasing the action.
        step['goal'] = card.title
        step['instruction'] = (card.beginner_shortcut if profile.skill_level == 'beginner'
                               and card.beginner_shortcut else
                               ' '.join(action.instruction for action in card.actions))
        note = instruction_note(card.id, profile.face_features_effective)
        if note:
            step['instruction'] += ' ' + note
            step['adjustment_reason'] = '根据你的面部特点调整：' + note
        step['success_signal'] = card.actions[-1].success_signal
        step['duration_minutes'] = card.duration_minutes
        used_card_ids.add(card.id)

        requested = (step.get('product_match') or {}).get('required_category')
        category = requested if requested in card.product_categories else card.product_categories[0]
        feature = next((f for f in analysis.features if f.region == card.region), None) if analysis else None
        step['product_match'] = match_product(category, products, card, feature).model_dump(mode='json')

        allowed_tools = card.tools
        if profile.available_tools is not None:
            allowed_tools = [tool for tool in allowed_tools if tool in profile.available_tools]
        if step.get('tool') not in allowed_tools:
            step['tool'] = allowed_tools[0] if allowed_tools else None
        supported_steps.append(step)
    for order, step in enumerate(supported_steps, start=1):
        step['order'] = order
        step['depends_on'] = [dependency for dependency in step.get('depends_on', [])
                              if 1 <= dependency < order]
        step.setdefault('duration_minutes', 3)
    grounded['steps'] = supported_steps
    grounded['estimated_minutes'] = None
    return grounded


def run_tutorial_agent(pipeline, image_path, profile, products, report, llm=None):
    runtime = AgentRuntime(seconds=120, report=report, llm=llm)
    state = {}
    knowledge_digest = hashlib.sha256(json.dumps([c.model_dump(mode='json') for c in pipeline.knowledge.cards], sort_keys=True).encode()).hexdigest()
    cache = ArtifactCache(Path(os.getenv('MAKEUP_CACHE_PATH', 'runtime/agent-cache.sqlite3')))
    final_key = cache.key('tutorial-complete-v3-no-time-budget', hashlib.sha256(Path(image_path).read_bytes()).hexdigest(),
        os.getenv('QWEN_AGENT_MODEL', 'qwen-plus'), os.getenv('QWEN_VISION_MODEL', 'qwen3-vl-plus'),
        ANALYSIS_SYSTEM_PROMPT, ANALYSIS_USER_PROMPT, Path(__file__).read_text(), knowledge_digest,
        profile.model_dump(mode='json'), [p.model_dump(mode='json') for p in products])
    cached_result = None if llm else cache.get(final_key)
    if cached_result:
        result = attach_illustrations(PipelineResult.model_validate(cached_result))
        if not validate_tutorial(result.tutorial, profile, products, pipeline.knowledge.cards, result.analysis):
            result.analysis.image.path = str(image_path)
            runtime.event('tutorial_cache', 'success', time.monotonic(), cache_hit=True, usage=None)
            result.agent_trace = runtime.trace
            return result

    def analyze(_):
        from .models import LookAnalysis
        key = cache.key('vision-v1', hashlib.sha256(Path(image_path).read_bytes()).hexdigest(),
                        os.getenv('QWEN_VISION_MODEL', 'qwen3-vl-plus'), ANALYSIS_SYSTEM_PROMPT, ANALYSIS_USER_PROMPT)
        cached = cache.get(key)
        started = time.monotonic()
        state['analysis'] = LookAnalysis.model_validate(cached) if cached else pipeline.analyze_image(image_path)
        state['analysis'].image.path = str(image_path)
        if not cached:
            cache.put(key, state['analysis'].model_dump(mode='json'))
        runtime.event('vision', 'success', started, cache_hit=bool(cached), internal_model_call=not bool(cached),
                      model=getattr(pipeline.analyzer, 'model', 'fixture'), usage=None if cached else getattr(pipeline.analyzer, 'last_usage', None))
        return state['analysis'].model_dump(mode='json', exclude={'image'})

    def retrieve(_):
        if 'analysis' not in state:
            raise ValueError('请先调用 analyze_reference')
        from .models import TechniqueCard
        key = cache.key('retrieval-v3-reference-only', state['analysis'].model_dump(mode='json', exclude={'image'}),
                        knowledge_digest)
        cached = cache.get(key)
        state['cards'] = [TechniqueCard.model_validate(c) for c in cached] if cached else pipeline.knowledge.retrieve(state['analysis'], profile)
        if not cached:
            cache.put(key, [c.model_dump(mode='json') for c in state['cards']])
        if not state['cards']:
            raise ValueError('参考妆效缺少可用技法卡')
        from .pipeline import match_product
        return {'cards': [c.model_dump(mode='json') for c in state['cards']],
                'matches': {c.id: [match_product(cat, products, c,
                    next((f for f in state['analysis'].features if f.region == c.region), None)).model_dump(mode='json')
                                    for cat in c.product_categories] for c in state['cards']}}

    def submit(plan):
        if 'cards' not in state:
            raise ValueError('请先读取图片并检索技法')
        errors = validate_tutorial(plan, profile, products, state['cards'], state['analysis'])
        if errors:
            raise ValueError('；'.join(errors))
        from .knowledge import attach_technique_sources
        attach_technique_sources(plan, state['cards'])
        plan.provider = 'qwen-agent:0.0.31'
        plan.review_warnings.extend(pipeline.review(state['analysis'], profile, plan.steps))
        selected = {s.technique_card_id for s in plan.steps}
        reviewed = all(c.review_status != 'draft' for c in state['cards'] if c.id in selected)
        if not reviewed:
            plan.review_warnings.append('技法卡尚待人工复核，本教程是可编辑预览，不是经专家验证的教程。')
        plan.missing_products = sorted({s.product_match.required_category for s in plan.steps if s.product_match.status == 'missing'})
        runtime.result = attach_illustrations(PipelineResult(analysis=state['analysis'], retrieved_card_ids=list(selected), tutorial=plan,
            knowledge_version=knowledge_digest[:16],
            publication_status='reviewed' if reviewed else 'preview'))
        return {'validated': True}

    runtime.run([
        FunctionTool(runtime, 'analyze_reference', '分析参考妆面，仅输出可见观察', EmptyInput, analyze),
        FunctionTool(runtime, 'retrieve_techniques', '按参考妆效检索技法并匹配已有产品用途', EmptyInput, retrieve),
        FunctionTool(runtime, 'validate_tutorial', '校验并提交教程；失败时仅允许一次修正',
                     TutorialPlan, submit, limit=2,
                     normalizer=lambda payload: ground_tutorial_payload(
                         payload, state.get('cards', []), products, profile, state.get('analysis'))),
    ], '先 analyze_reference，再 retrieve_techniques，最后 validate_tutorial。'
       '每步写清用量、位置、方向、动作和完成信号。不得把缺货产品写成用户已有。'
       '用户 must_keep 必须出现在 preserved_features 并映射到相应步骤的 preserves_features。'
       '遮挡区域不可猜测。'
       'beginner 优先简化。只能选择工具返回的技法 ID 和产品 ID。',
       {'profile': profile.model_dump(mode='json'), 'products': [p.model_dump(mode='json') for p in products]},
       required_sequence=('analyze_reference', 'retrieve_techniques',
                          'validate_tutorial', 'validate_tutorial'))
    runtime.result.agent_trace = runtime.trace
    if not llm:
        cache.put(final_key, runtime.result.model_dump(mode='json'))
    return runtime.result
