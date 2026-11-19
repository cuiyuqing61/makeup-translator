"""Hard constraints shared by generation and local revisions."""
import re
from .models import TutorialPlan, UserProfile, OwnedProduct, TechniqueCard, LookAnalysis


def validate_tutorial(plan: TutorialPlan, profile: UserProfile, products: list[OwnedProduct],
                      cards: list[TechniqueCard], analysis: LookAnalysis) -> list[str]:
    errors = []
    inventory = {p.id: p for p in products if p.user_confirmed}
    knowledge = {c.id: c for c in cards}
    visible = {f.region for f in analysis.features}
    targets = {t.id: t for t in analysis.targets}
    catalog = None
    for step in plan.steps:
        prefix = f'步骤 {step.order}: '
        card = knowledge.get(step.technique_card_id)
        if not card or card.region != step.region:
            errors.append(prefix + '技法不存在或使用部位不符')
        if step.region not in visible:
            errors.append(prefix + '图片未提供该部位的可见依据')
        match = step.product_match
        if step.product_requirement and step.product_requirement.category != match.required_category:
            errors.append(prefix + '所需产品类别与匹配结果不一致')
        if step.product_requirement and card and set(step.product_requirement.suitable_forms) != set(card.required_product_kinds):
            errors.append(prefix + '产品形态要求与技法卡不一致')
        if card and match.required_category not in card.product_categories:
            errors.append(prefix + '产品用途与技法不符')
        if match.status == 'not_needed':
            if (not card or match.required_category != 'none' or
                    'none' not in card.product_categories or match.selected_product_id):
                errors.append(prefix + '无需产品状态与技法不符')
            wording = ' '.join([step.instruction, step.application.action if step.application else ''])
            clauses = re.split(r'[，。；;]', wording)
            product_term = r'(?:产品|眉粉|眉笔|眉胶|眼影|眼线|睫毛膏|腮红|修容|高光|口红|唇膏|唇釉|粉底)'
            if any(not re.search(r'无需|不用|不必|不要|不使用|不购买|不准备|不加|避免', clause)
                   and (re.search(r'(?:准备|购买|取用|使用|涂抹|刷上|蘸取).{0,8}' + product_term, clause)
                        or re.search(product_term + r'.{0,5}(?:上妆|涂抹|轻拍|刷上)', clause))
                   for clause in clauses):
                errors.append(prefix + '无需产品与准备或使用产品的动作矛盾')
        elif match.selected_product_id:
            product = inventory.get(match.selected_product_id)
            if not product or product.category != match.required_category:
                errors.append(prefix + '产品不在已确认清单中，或功能不符（眼影、眼线和睫毛膏不可互代）')
            elif card:
                from .pipeline import product_satisfies_card
                if not product_satisfies_card(product, card, match.required_category):
                    errors.append(prefix + '已有产品无法确认满足这一步的具体用途')
            if match.status == 'missing':
                errors.append(prefix + '缺货状态与产品 ID 冲突')
        elif match.status == 'catalog_candidate':
            if catalog is None:
                import json
                from pathlib import Path
                catalog = {item['id']: item for item in json.loads(
                    (Path(__file__).resolve().parent.parent / 'data' / 'product_catalog.json').read_text(encoding='utf-8'))}
            candidate = catalog.get(match.catalog_product_id)
            if (not candidate or not match.catalog_source_url
                    or candidate.get('source_url') != match.catalog_source_url
                    or candidate.get('category') != match.required_category):
                errors.append(prefix + '目录候选缺少有效来源或类别不符')
            elif card:
                from .pipeline import product_satisfies_card
                candidate_product = OwnedProduct(id=candidate['id'],
                    category=candidate['category'],
                    name=' '.join(filter(None, [candidate.get('brand'), candidate.get('name'),
                                                candidate.get('shade')])),
                    user_confirmed=False)
                if not product_satisfies_card(candidate_product, card, match.required_category):
                    errors.append(prefix + '目录候选不具备这一步要求的具体用途')
        elif match.status not in ('missing', 'pending'):
            errors.append(prefix + '没有产品 ID 的匹配必须标记 missing')
        if analysis.targets:
            target = targets.get(step.target_id)
            if not target or target.region != step.region:
                errors.append(prefix + '目标不存在或部位不符')
            elif step.target_evidence != target.evidence:
                errors.append(prefix + '妆效观察依据与参考图不符')
            if not step.application or any(not getattr(step.application, field).strip() for field in
                    ('placement', 'direction', 'amount', 'layers', 'action', 'completion_check')):
                errors.append(prefix + '缺少具体的位置、方向、用量、层次、动作或完成检查')
            valid_evidence = {f'{k}：{v}' for k, v in profile.face_features_effective.items()}
            if any(value not in valid_evidence for value in step.face_evidence):
                errors.append(prefix + '面部依据不是用户资料中的有效观察')
        if step.drawing:
            allowed_anchors = {
                'complexion': {'face', 'nose', 'left_cheek', 'right_cheek'},
                'eyebrow': {'left_brow', 'right_brow'},
                'eye': {'left_eye', 'right_eye'},
                'cheek': {'left_cheek', 'right_cheek'},
                'lip': {'lips'},
            }
            if step.drawing.anchor not in allowed_anchors.get(step.region.value, set()):
                errors.append(prefix + '绘图锚点与操作部位不符')
        if step.region.value == 'cheek':
            wording = ' '.join([step.instruction, step.adjustment_reason,
                step.application.placement if step.application else ''])
            if profile.face_features_effective.get('cheek_shadow') == 'visible' and '凹陷' in wording:
                errors.append(prefix + '面颊阴影不能直接解释为面部凹陷')
            if profile.face_features_effective.get('local_redness') == 'visible' and any(
                    place + '泛红' in wording or '泛红' + place in wording
                    for place in ('鼻翼', '颧骨', '面颊内侧', '面颊外侧', '苹果肌')):
                errors.append(prefix + '用户资料没有泛红位置依据，不能指定具体区域')
        if (profile.available_tools is not None and step.tool
                and step.tool not in profile.available_tools and step.tool_status != 'prepare'):
            errors.append(prefix + '用户没有该工具')
        if step.tool_status == 'prepare' and (not step.tool or '准备' not in step.instruction):
            errors.append(prefix + '缺少工具时必须明确准备要求')
        if card and step.tool and step.tool not in card.tools:
            errors.append(prefix + '工具不适用于该技法')
        if any(dep >= step.order or dep < 1 for dep in step.depends_on):
            errors.append(prefix + '依赖步骤必须存在且位于当前步骤之前')
        if not step.instruction.strip() or not step.success_signal.strip():
            errors.append(prefix + '缺少动作或完成标准')
    for feature in profile.must_keep:
        if feature not in plan.preserved_features or not any(feature in s.preserves_features for s in plan.steps):
            errors.append(f'未保留用户指定特征：{feature}')
    return errors
