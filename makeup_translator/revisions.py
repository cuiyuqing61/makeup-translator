"""Deterministic revisions: reuse analysis, preserve history and lock finished steps."""
from copy import deepcopy
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .constraints import validate_tutorial
from .models import StrictModel, PipelineResult, UserProfile, OwnedProduct, TechniqueCard
from .knowledge import TechniqueKnowledgeBase
from .illustrations import attach_illustrations, illustrate_step


class RevisionRequest(StrictModel):
    action: Literal['swap_product', 'swap_tool', 'simplify', 'shorten']
    step: int = Field(default=1, ge=1)
    value: str = ''
    minutes: int = Field(default=10, ge=1, le=120)
    base_version: str


class StepStateRequest(StrictModel):
    state: Literal['not_started', 'completed', 'skipped']


class FeedbackRequest(StrictModel):
    difficulty: Literal['easy', 'appropriate', 'hard']
    actual_minutes: int = Field(ge=1, le=300)
    liked_result: bool
    product_experience: str = Field(default='', max_length=1000)
    preference: str = Field(default='', max_length=300)
    save_preference: bool = False


def initial_session(result):
    version = {'id': uuid4().hex, 'result': result, 'reason': 'original'}
    return {'active_version': version['id'], 'versions': [version], 'states': {}, 'preview': None}


def active_result(session):
    return next(v['result'] for v in session['versions'] if v['id'] == session['active_version'])


def preview_revision(session, metadata, request: RevisionRequest):
    if request.base_version != session['active_version']:
        raise ValueError('教程版本已变化，请刷新后再修改')
    before = attach_illustrations(PipelineResult.model_validate(active_result(session)))
    result = before.model_copy(deep=True)
    plan = result.tutorial
    profile = UserProfile.model_validate(metadata['profile'])
    products = [OwnedProduct.model_validate(p) for p in metadata['products']]
    cards = ([TechniqueCard.model_validate(c) for c in metadata['knowledge_cards']]
             if metadata.get('knowledge_cards') else TechniqueKnowledgeBase().cards)
    by_id = {c.id: c for c in cards}
    unlocked = [s for s in plan.steps if session['states'].get(str(s.order), 'not_started') == 'not_started']
    step = next((s for s in unlocked if s.order == request.step), None)
    if request.action != 'shorten' and not step:
        raise ValueError('已完成或跳过的步骤已锁定；请先重新打开步骤')
    changed = []
    original_step = step.model_copy(deep=True) if step else None
    if request.action == 'swap_product':
        product = next((p for p in products if p.id == request.value and p.user_confirmed), None)
        if not product or product.category != step.product_match.required_category:
            raise ValueError('请选择快照中用途相同的已确认产品')
        old = next((p for p in products if p.id == step.product_match.selected_product_id), None)
        if old:
            step.instruction = step.instruction.replace(old.name, product.name)
        step.product_match.selected_product_id = product.id
        step.product_match.status = 'owned_match'
        step.product_match.match_reason = f'用户指定同用途产品“{product.name}”；色号和显色程度请先少量试用。'
        changed.append(step.order)
    elif request.action == 'swap_tool':
        card = by_id.get(step.technique_card_id)
        if not card or request.value not in card.tools:
            raise ValueError('该技法没有经过定义的工具替代，请选择简化或跳过')
        if step.tool:
            step.instruction = step.instruction.replace(step.tool, request.value)
        step.tool = request.value
        changed.append(step.order)
    else:
        targets = [step] if request.action == 'simplify' else unlocked
        if not targets:
            raise ValueError('没有可修改的未完成步骤')
        if request.action == 'shorten' and request.minutes < len(targets):
            raise ValueError('剩余时间不足以保留所有步骤；请先明确跳过哪些步骤')
        for target in targets:
            card = by_id.get(target.technique_card_id)
            if not card or not card.beginner_shortcut:
                raise ValueError(f'步骤 {target.order} 没有定义过的简化方法')
            target.instruction = card.beginner_shortcut
            target.success_signal = card.actions[-1].success_signal
            target.duration_minutes = min(target.duration_minutes, 2 if request.action == 'simplify' else max(1, request.minutes // len(targets)))
            changed.append(target.order)
            result.step_illustrations = [
                illustrate_step(target, mode='simplified') if image.step_order == target.order else image
                for image in result.step_illustrations
            ]
    if request.action in {'swap_product', 'swap_tool'}:
        affected = {step.order}
        for dependent in plan.steps:
            if not (set(dependent.depends_on) & affected):
                continue
            affected.add(dependent.order)
            references_change = (
                dependent.product_match.selected_product_id == original_step.product_match.selected_product_id
                if request.action == 'swap_product' else dependent.tool == original_step.tool)
            if not references_change:
                continue
            if session['states'].get(str(dependent.order), 'not_started') != 'not_started':
                raise ValueError(f'修改影响已锁定的依赖步骤 {dependent.order}，请先重新打开该步骤')
            if request.action == 'swap_product':
                dependent.product_match = step.product_match.model_copy(deep=True)
                if old:
                    dependent.instruction = dependent.instruction.replace(old.name, product.name)
            else:
                if original_step.tool:
                    dependent.instruction = dependent.instruction.replace(original_step.tool, request.value)
                dependent.tool = request.value
            changed.append(dependent.order)
    plan.estimated_minutes = max(5, sum(s.duration_minutes for s in plan.steps))
    plan.missing_products = sorted({s.product_match.required_category for s in plan.steps if s.product_match.status == 'missing'})
    attach_illustrations(result)
    errors = validate_tutorial(plan, profile, products, cards, result.analysis)
    if errors:
        raise ValueError('；'.join(errors))
    # No structural deletion: dependencies and unrelated steps stay intact.
    for old, new in zip(before.tutorial.steps, plan.steps):
        if old.order not in changed and old != new:
            raise ValueError('修改影响了不相关步骤')
    return {'id': uuid4().hex, 'base_version': session['active_version'], 'result': result.model_dump(mode='json'),
            'reason': request.action, 'changed_steps': changed, 'states': deepcopy(session['states'])}
