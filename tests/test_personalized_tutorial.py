from pathlib import Path

import pytest
from PIL import Image

from makeup_translator.models import OwnedProduct, UserProfile
from makeup_translator.pipeline import MakeupTutorialPipeline
from makeup_translator.personalized_tutorial import (
    _parse_model_json, _product_match, _target_candidates, _with_product_prerequisite,
    _actionable_target, _card_matches_target, _keep_visible_blush,
    _tools_used_by_step,
    _module_decisions, run_personalized_tutorial,
)
from makeup_translator.models import MakeupFeature, MakeupTarget, Confidence, ProductMatch


def _run(tmp_path, monkeypatch, face=None, products=None, first_review=False,
         unsupported_color=False, split=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('MAKEUP_CACHE_PATH', str(tmp_path / 'cache.sqlite3'))
    monkeypatch.setenv('MAKEUP_GRAPH_PATH', str(tmp_path / 'graph.sqlite3'))
    image = tmp_path / 'reference.jpg'
    Image.new('RGB', (100, 100), '#b48070').save(image)
    calls = []

    def model(role, payload, image_path=None, analysis=None):
        calls.append(role)
        if '目标识别' in role:
            return {'targets': [
                {'id': 'lip-color', 'region': 'lip', 'role': 'lip', 'effect': '柔雾豆沙唇',
                 'placement': '上下唇', 'color_family': ['豆沙'], 'finish': ['velvet'],
                 'intensity': 'medium', 'evidence': ('参考图唇部着色，但色相受光线影响'
                                                  if unsupported_color else '参考图唇部呈豆沙色且边缘柔和'),
                 'confidence': 'medium', 'image_boxes': [], 'required': True},
            ]}
        if '颜色证据复核' in role:
            assert image_path == image
            assert [t['id'] for t in payload['targets']] == ['lip-color']
            return {'reviews': [{'id': 'lip-color', 'supported_colors': [],
                                 'observation': '唇色可见，但具体色相受光线影响，无法确认'}]}
        if '面部适配' in role:
            return {'adaptations': {'lip-color': {
                'face_keys': ['lip_shape'] if face else [],
                'reason': '沿自然唇缘轻拍' if face else '保留参考图柔和边缘',
                'placement': '唇内侧', 'direction': '由内向外', 'amount': '薄薄一层',
                'layers': '先打底再内侧叠色', 'action': '用指腹轻拍',
                'completion_check': '唇缘柔和且没有明显硬线'}}}
        if '教程规划' in role:
            application = {'placement': '唇内侧', 'direction': '由内向外',
                           'amount': '薄薄一层', 'layers': '先打底再叠色',
                           'action': '用口红或唇刷轻涂，棉签清理越界', 'completion_check': '唇缘柔和'}
            return {'title': '豆沙唇教程', 'look_summary': '柔雾豆沙唇',
                    'preserved_features': [], 'personalized_changes': ['依据唇形调整'],
                    'steps': [{'target_id': 'lip-color', 'card_id': 'lip-even-lipstick',
                               'category': 'lip', 'goal': '柔雾豆沙唇',
                               'instruction': '用口红或唇刷薄涂唇色，棉签清理越界',
                               'success_signal': '唇缘柔和', 'duration_minutes': 3,
                               'depends_on': [], 'preserves_features': [],
                               'drawing': {'anchor': 'lips', 'side': 'center',
                                           'offset_x': 0, 'offset_y': 0,
                                           'radius_x': .13, 'radius_y': .06,
                                           'direction': 'outward', 'strength': 'medium',
                                           'layers': 1},
                               'application': application}]}
        if '产品选择' in role:
            candidate = payload['candidates']
            return {'id': candidate['owned'][0]['id'] if candidate['owned'] else
                    candidate['catalog'][0]['id'] if candidate['catalog'] else None}
        if '独立审查' in role:
            if first_review and calls.count(role) == 1:
                return {'issues': ['请核对完成标准']}
            return {'issues': []}
        raise AssertionError(role)

    profile = UserProfile(available_minutes=10, face_features_effective=face or {})
    pipeline = MakeupTutorialPipeline('fixture')
    if split:
        shared = run_personalized_tutorial(pipeline, image, profile, products or [],
                                           lambda *_: None, model, phase='analysis')
        result = run_personalized_tutorial(pipeline, image, profile, products or [],
                                           lambda *_: None, model, phase='steps', shared=shared)
    else:
        result = run_personalized_tutorial(pipeline, image, profile,
                                           products or [], lambda *_: None, model)
    return result, calls


def test_split_planning_uses_shared_analysis_without_waiting_for_products(tmp_path, monkeypatch):
    result, calls = _run(tmp_path, monkeypatch, products=[OwnedProduct(
        id='mine', category='lip', name='豆沙口红', color_family=['豆沙'])], split=True)
    step = result.tutorial.steps[0]
    assert step.product_match.status == 'pending'
    assert step.product_requirement.category == 'lip'
    assert step.drawing.anchor == 'lips'
    assert step.step_id.startswith('lip-color:lip-even-lipstick:lip:')
    assert not any('产品选择' in role for role in calls)


def test_graph_binds_face_target_product_and_repairs(tmp_path, monkeypatch):
    product = OwnedProduct(id='mine', category='lip', name='豆沙口红',
                           color_family=['豆沙'])
    result, calls = _run(tmp_path, monkeypatch, {'lip_shape': '薄唇'}, [product], True)
    step = result.tutorial.steps[0]
    assert step.target_id == 'lip-color'
    assert step.face_evidence == ['lip_shape：薄唇']
    assert step.product_match.selected_product_id == 'mine'
    assert step.application.placement == '唇内侧'
    assert calls.count('独立审查 Agent') == 2
    assert calls.count('教程规划 Agent') == 2


def test_graph_changes_when_profile_or_inventory_changes(tmp_path, monkeypatch):
    owned = OwnedProduct(id='mine', category='lip', name='豆沙口红',
                         color_family=['豆沙'])
    with_face, _ = _run(tmp_path / 'a', monkeypatch, {'lip_shape': '薄唇'}, [owned])
    without, _ = _run(tmp_path / 'b', monkeypatch, {}, [])
    assert with_face.tutorial.steps[0].face_evidence
    assert not without.tutorial.steps[0].face_evidence
    assert with_face.tutorial.steps[0].product_match.status == 'owned_approximation'
    assert without.tutorial.steps[0].product_match.status == 'missing'


def test_ambiguous_owned_products_use_selection_agent(tmp_path, monkeypatch):
    products = [OwnedProduct(id=name, category='lip', name=f'{name}口红',
                             color_family=['豆沙']) for name in ('a', 'b')]
    result, calls = _run(tmp_path, monkeypatch, {}, products)
    assert calls.count('产品选择 Agent') == 1
    assert result.tutorial.steps[0].product_match.selected_product_id == 'a'


def test_unsupported_target_color_is_removed_before_product_matching(tmp_path, monkeypatch):
    owned = OwnedProduct(id='mine', category='lip', name='豆沙口红', color_family=['豆沙'],
                         finish=['velvet'])
    result, calls = _run(tmp_path, monkeypatch, products=[owned], unsupported_color=True)
    target = result.analysis.targets[0]
    assert calls.count('颜色证据复核 Agent') == 1
    assert target.color_family == []
    assert '无法确认' in target.evidence
    assert result.tutorial.steps[0].product_match.status == 'missing'


@pytest.mark.parametrize('status', ['missing', 'catalog_candidate', 'owned_approximation'])
def test_unverified_product_steps_state_prerequisite(status):
    match = ProductMatch(required_category='lip', status=status, match_reason='颜色未验证')
    instruction, action = _with_product_prerequisite(
        '取现有唇部产品薄涂', '用已有唇部产品轻拍', match)
    assert '实物颜色' in instruction or '实际颜色' in instruction
    assert '后再操作' in instruction
    assert '现有唇部产品' not in instruction
    if status != 'owned_approximation':
        assert '已有唇部产品' not in action
    evidence, _ = _with_product_prerequisite('现有证据只支持淡粉色观感', '轻拍', match)
    assert '现有证据' in evidence


def test_unverified_product_choice_is_rejected():
    target = MakeupTarget(id='t', region='lip', role='lip', effect='红唇', placement='唇部',
                          evidence='可见红色', confidence=Confidence.medium,
                          color_family=['红'])
    card = next(c for c in MakeupTutorialPipeline('fixture').knowledge.cards if c.id == 'lip-even-lipstick')
    candidates = _target_candidates(target, card, [])
    with pytest.raises(ValueError, match='候选集之外'):
        _product_match(target, card, candidates, {'id': 'invented'})
    assert candidates['catalog']
    unselected = _product_match(target, card, candidates, {'id': None})
    assert unselected.status == 'missing'
    assert unselected.catalog_suggestions
    selected = _product_match(target, card, candidates, {'id': candidates['catalog'][0]['id']})
    assert selected.status == 'catalog_candidate'
    assert selected.selected_product_id is None
    assert selected.catalog_source_url


def test_model_json_trailing_comma_repair_does_not_change_strings():
    parsed = _parse_model_json('{"choices":{"0":{"id":null,"reason":"a, } literal",},},}')
    assert parsed['choices']['0']['reason'] == 'a, } literal'


def test_visible_low_color_targets_remain_in_complete_look_template(tmp_path):
    pipeline = MakeupTutorialPipeline('fixture')
    image = tmp_path / 'look.jpg'
    Image.new('RGB',(100,100),'#bc8879').save(image)
    analysis = pipeline.analyze_image(image)
    analysis.targets = [
        MakeupTarget(id='eye-wash',region='eye',role='depth',effect='浅眼影',
                     placement='眼睑',evidence='眼睑可见轻微晕染，具体颜色无法确认',
                     confidence=Confidence.low,required=False,intensity='light'),
        MakeupTarget(id='cheek-blush',region='cheek',role='blush',effect='淡腮红',
                     placement='双颊',evidence='双颊可见淡淡腮红',
                     confidence=Confidence.low,required=False,intensity='light'),
        MakeupTarget(id='nose-glare',region='complexion',role='highlight',effect='鼻梁反光',
                     placement='鼻梁',evidence='可见反光，但不能确认这是妆效还是光线造成',
                     confidence=Confidence.low,required=False,intensity='light'),
    ]
    assert [_actionable_target(t) for t in analysis.targets] == [True,True,False]
    decisions = {d.module:d for d in _module_decisions(analysis)}
    assert decisions['eyes'].target_ids == ['eye-wash']
    assert decisions['blush'].status == 'light'
    assert decisions['contour_highlight'].status == 'uncertain'
    analysis.targets[-1].confidence = Confidence.medium
    analysis.targets[-1].required = True
    assert not _actionable_target(analysis.targets[-1])


def test_uncertain_base_blush_and_natural_brow_do_not_force_operations():
    targets = [
        MakeupTarget(id='base', region='complexion', role='base', effect='肤色均匀',
            placement='全脸', evidence='难以区分底妆与光线影响',
            confidence=Confidence.low, required=False),
        MakeupTarget(id='brow', region='eyebrow', role='brow', effect='自然眉形',
            placement='双眉', evidence='未见明显浓重填色',
            confidence=Confidence.medium, required=False),
        MakeupTarget(id='blush', region='cheek', role='blush', effect='面颊微红',
            placement='双颊', evidence='无法确认是否为腮红',
            confidence=Confidence.low, required=False),
        MakeupTarget(id='sheen', region='lip', role='finish', effect='唇面轻微光泽',
            placement='唇面', evidence='细微光泽可能受光线影响',
            confidence=Confidence.low, required=False),
    ]
    assert not any(_actionable_target(target) for target in targets)


def test_partially_occluded_visible_lip_color_remains_actionable():
    visible_lip = MakeupTarget(id='lip-color', region='lip', role='lip',
        effect='可见唇面呈柔和粉色', placement='露出的唇面',
        evidence='露出的唇部可见低饱和粉色，部分唇部被遮挡',
        confidence=Confidence.medium, required=True)
    hidden_lip = MakeupTarget(id='lip-hidden', region='lip', role='lip',
        effect='唇色不明', placement='唇面', evidence='唇部完全被遮挡',
        confidence=Confidence.low, required=False)
    assert _actionable_target(visible_lip)
    assert not _actionable_target(hidden_lip)


def test_natural_lashes_and_uncertain_lighting_do_not_require_product_steps():
    targets = [
        MakeupTarget(id='lashes', region='eye', role='finish',
            effect='睫毛可见，保持自然状态', placement='双眼睫毛',
            evidence='未见足以确认明显睫毛膏效果的证据',
            confidence=Confidence.medium, required=False),
        MakeupTarget(id='base', region='complexion', role='base',
            effect='面部观感均匀', placement='面部',
            evidence='底妆效果无法与光线影响区分',
            confidence=Confidence.low, required=False),
        MakeupTarget(id='lid', region='eye', role='base',
            effect='眼睑明暗变化', placement='上眼睑',
            evidence='上眼睑可见淡淡变化，但是否由眼影造成无法可靠确定',
            confidence=Confidence.low, required=False),
    ]
    assert [_actionable_target(target) for target in targets] == [False, False, True]


def test_equivocal_complexion_does_not_require_foundation():
    target = MakeupTarget(id='base', region='complexion', role='base',
        effect='面部观感较均匀，妆感轻薄', placement='面部',
        evidence='没有明显底妆边界；底妆效果与光线难以区分',
        confidence=Confidence.low, required=False)
    assert not _actionable_target(target)


def test_product_free_brow_step_selects_only_the_brush_it_uses():
    card = next(card for card in MakeupTutorialPipeline('fixture').knowledge.cards
                if card.id == 'eyebrow-soft-front')
    assert _tools_used_by_step(card, '用干净螺旋眉刷顺毛流轻梳，不加眉粉',
                              '沿原有毛流整理') == ['干净螺旋眉刷']


def test_lash_root_cannot_use_mascara_and_blush_stays_on_visible_cheek():
    cards = {card.id:card for card in MakeupTutorialPipeline('fixture').knowledge.cards}
    target = MakeupTarget(id='lash-root',region='eye',role='line',effect='睫毛根部轻微加深',
                          placement='上睫毛根部',evidence='可见细微加深',
                          confidence=Confidence.low,required=False)
    assert _card_matches_target(cards['eye-lashline'],target)
    assert not _card_matches_target(cards['eye-mascara-separate'],target)
    assert '避开泛红' not in _keep_visible_blush('双颊中部薄拍，避开泛红最明显处')


def test_reviewer_repairs_only_affected_region(tmp_path, monkeypatch):
    monkeypatch.setenv('MAKEUP_CACHE_PATH', str(tmp_path / 'cache.sqlite3'))
    monkeypatch.setenv('MAKEUP_GRAPH_PATH', str(tmp_path / 'graph.sqlite3'))
    image = tmp_path / 'reference.jpg'
    Image.new('RGB', (100, 100), '#a08070').save(image)
    planned = []
    reviews = 0

    def model(role, payload, image_path=None, analysis=None):
        nonlocal reviews
        if '目标识别' in role:
            return {'targets': [
                {'id': 'eye-base', 'region': 'eye', 'role': 'base', 'effect': '浅眼影',
                 'placement': '上眼皮', 'evidence': '眼皮浅色', 'confidence': 'medium'},
                {'id': 'lip-color', 'region': 'lip', 'role': 'lip', 'effect': '豆沙唇',
                 'placement': '唇部', 'evidence': '唇部豆沙色', 'confidence': 'medium'}]}
        if '面部适配' in role:
            guide = {'face_keys': [], 'reason': '匹配可见妆效', 'placement': '可见区域',
                     'direction': '向外晕染', 'amount': '少量', 'layers': '一层',
                     'action': '轻拍', 'completion_check': '边缘自然'}
            return {'adaptations': {'eye-base': guide, 'lip-color': guide}}
        if '教程规划' in role:
            region = payload['region']
            planned.append(region)
            tid, card, category = (
                ('eye-base', 'eye-neutral-shadow', 'eyeshadow') if region == 'eye'
                else ('lip-color', 'lip-even-lipstick', 'lip'))
            action = ('用干净铺色刷薄铺浅眼影，再用晕染刷柔化边缘' if region == 'eye'
                      else '用口红或唇刷薄涂，棉签清理越界')
            return {'personalized_changes': [{'reason': '按可见妆效', 'change': region}],
                    'steps': [{'target_id': tid, 'card_id': card, 'category': category,
                               'goal': tid, 'instruction': action,
                               'success_signal': '边缘自然', 'duration_minutes': 3,
                               'application': {'placement': '可见区域', 'direction': '向外晕染',
                                               'amount': '少量', 'layers': '一层',
                                               'action': action, 'completion_check': '边缘自然'}}]}
        if '产品选择' in role:
            return {'choices': {str(i): {'id': None} for i in range(len(payload['steps']))}}
        if '独立审查' in role:
            reviews += 1
            return {'issues': ['步骤 1 需调整眼影层次'] if reviews == 1 else []}
        raise AssertionError(role)

    result = run_personalized_tutorial(MakeupTutorialPipeline('fixture'), image,
                                        UserProfile(available_minutes=15), [],
                                        lambda *_: None, model)
    assert planned == ['eye', 'lip', 'eye']
    assert [step.target_id for step in result.tutorial.steps] == ['eye-base', 'lip-color']
    assert all(isinstance(x, str) for x in result.tutorial.personalized_changes)


def test_natural_brow_grooming_does_not_require_product(tmp_path, monkeypatch):
    monkeypatch.setenv('MAKEUP_CACHE_PATH', str(tmp_path / 'cache.sqlite3'))
    monkeypatch.setenv('MAKEUP_GRAPH_PATH', str(tmp_path / 'graph.sqlite3'))
    image = tmp_path / 'reference.jpg'
    Image.new('RGB', (100, 100), '#a08070').save(image)
    pipeline = MakeupTutorialPipeline('fixture')
    fixture_analysis = pipeline.analyze_image(image)
    fixture_analysis.features.append(MakeupFeature(
        region='eyebrow', effect='自然柔和眉毛', evidence='眉毛本身柔和',
        confidence=Confidence.medium))
    pipeline.analyze_image = lambda _: fixture_analysis

    def model(role, payload, image_path=None, analysis=None):
        if '目标识别' in role:
            return {'targets': [{'id': 'brow-natural', 'region': 'eyebrow', 'role': 'brow',
                                 'effect': '保留自然眉毛', 'placement': '沿毛流',
                                 'evidence': '眉毛本身柔和', 'confidence': 'medium'}]}
        if '面部适配' in role:
            return {'adaptations': {'brow-natural': {
                'face_keys': [], 'reason': '保留原有眉色', 'placement': '双眉',
                'direction': '顺毛流', 'amount': '不加产品', 'layers': '梳理一遍',
                'action': '用干净眉刷轻梳', 'completion_check': '毛流整齐'}}}
        if '教程规划' in role:
            return {'steps': [{'target_id': 'brow-natural', 'card_id': 'eyebrow-soft-front',
                               'category': 'none', 'goal': '保留自然眉毛',
                               'instruction': '用干净眉刷顺毛流轻梳，不补色',
                               'success_signal': '毛流整齐', 'duration_minutes': 1,
                               'application': {'placement': '双眉', 'direction': '顺毛流',
                                               'amount': '不加产品', 'layers': '梳理一遍',
                                               'action': '用干净眉刷轻梳',
                                               'completion_check': '毛流整齐'}}]}
        if '独立审查' in role:
            return {'issues': []}
        raise AssertionError(role)

    result = run_personalized_tutorial(pipeline, image, UserProfile(), [],
                                       lambda *_: None, model)
    match = result.tutorial.steps[0].product_match
    assert match.status == 'not_needed'
    assert match.required_category == 'none'
    assert result.tutorial.missing_products == []
