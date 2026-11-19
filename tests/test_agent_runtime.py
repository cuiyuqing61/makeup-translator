import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from qwen_agent.llm import BaseChatModel
from qwen_agent.llm.schema import Message

from makeup_translator.agent_runtime import AgentRuntime, BudgetExceeded, EmptyInput, FunctionTool, _next_required_tool, _reasoning_effort, _use_native_tool_calls
from makeup_translator.constraints import validate_tutorial
from makeup_translator.models import OwnedProduct, Region, UserProfile
from makeup_translator.pipeline import MakeupTutorialPipeline, match_product
from makeup_translator.product_agent import canonical_brand, lookup_catalog, normalize, run_product_agent
from makeup_translator.product_models import ProductRecord
from makeup_translator.revisions import initial_session, preview_revision, RevisionRequest
from makeup_translator.tutorial_agent import ground_tutorial_payload, run_tutorial_agent


class ScriptedModel(BaseChatModel):
    """Network-free model double; the real Qwen-Agent dispatch loop still runs."""
    def __init__(self, turns):
        super().__init__({'model': 'scripted', 'model_type': 'oai'})
        self.turns = iter(turns)

    def chat(self, *args, **kwargs):
        turn = next(self.turns)
        yield [Message(role='assistant', content='', extra={}, function_call={'name': name, 'arguments': json.dumps(payload)}) for name, payload in turn]

    def _chat_with_functions(self, *args, **kwargs):
        raise AssertionError('unused')

    def _chat_stream(self, *args, **kwargs):
        raise AssertionError('unused')

    def _chat_no_stream(self, *args, **kwargs):
        raise AssertionError('unused')


def test_real_framework_dispatches_and_stops_after_submission():
    runtime = AgentRuntime(llm=ScriptedModel([[('finish', {})]]))
    def finish(_):
        runtime.result = {'ok': True}
        return runtime.result
    assert runtime.run([FunctionTool(runtime, 'finish', '提交', EmptyInput, finish)], '', {}) == {'ok': True}
    assert runtime.counts['orchestrator'] == 1
    assert any(t['name'] == 'finish' for t in runtime.trace)


def test_four_call_budget_is_code_enforced():
    runtime = AgentRuntime(llm=ScriptedModel([[('noop', {})]] * 5))
    with pytest.raises(BudgetExceeded):
        runtime.run([FunctionTool(runtime, 'noop', '空操作', EmptyInput, lambda _: {}, limit=10)], '', {})
    assert runtime.counts['orchestrator'] == 4


def test_required_sequence_forces_a_failed_submission_retry():
    class ChoiceRecordingModel(ScriptedModel):
        def __init__(self, turns):
            super().__init__(turns)
            self.choices = []

        def chat(self, *args, **kwargs):
            self.choices.append(kwargs.get('function_choice'))
            yield from super().chat(*args, **kwargs)

    model = ChoiceRecordingModel([
        [('submit', {})],
        [('submit', {'value': 'valid'})],
    ])
    runtime = AgentRuntime(llm=model)

    class Submission(BaseModel):
        value: str

    def submit(payload):
        if payload.value != 'valid':
            raise ValueError('请修正')
        runtime.result = {'value': payload.value}
        return runtime.result

    result = runtime.run(
        [FunctionTool(runtime, 'submit', '校验并提交', Submission, submit, limit=2)],
        '', {}, required_sequence=('submit', 'submit'))
    assert result == {'value': 'valid'}
    assert runtime.counts['submit'] == 2
    assert _next_required_tool(('submit', 'submit'), {'submit': 0}) == 'submit'
    assert _next_required_tool(('submit', 'submit'), {'submit': 1}) == 'submit'
    assert _next_required_tool(('submit', 'submit'), {'submit': 2}) is None


def test_non_dashscope_endpoint_uses_native_tool_calls(monkeypatch):
    monkeypatch.delenv('QWEN_AGENT_USE_RAW_API', raising=False)
    assert _use_native_tool_calls('https://aihubmix.com/v1') is True
    assert _use_native_tool_calls('https://dashscope.aliyuncs.com/compatible-mode/v1') is False
    monkeypatch.setenv('QWEN_AGENT_USE_RAW_API', 'false')
    assert _use_native_tool_calls('https://aihubmix.com/v1') is False


def test_glm_53_defaults_to_low_reasoning(monkeypatch):
    monkeypatch.delenv('QWEN_REASONING_EFFORT', raising=False)
    assert _reasoning_effort('glm-5.3-flash') == 'low'
    assert _reasoning_effort('qwen-plus') is None


def test_product_clarification_is_structured_and_resumable():
    result = run_product_agent(ProductRecord(id='a', raw_brand='花知晓', raw_name='腮红'), [], lambda *a: None,
        llm=ScriptedModel([[('lookup_catalog', {})], [('ask_clarification', {'question':'包装上写的系列名是什么？','choices':[]})]]))
    assert result['_waiting_user']['round'] == 1
    assert 'checkpoint' in result['_waiting_user']
    assert any(t['name'] == 'lookup_catalog' for t in result['agent_trace'])


@pytest.mark.parametrize('alias', ['3ce', '３ＣＥ', '三熹玉', '3CE STYLENANDA'])
def test_brand_aliases(alias):
    assert canonical_brand(alias) == '3CE'
    assert normalize('RD-01') == normalize('rd 01')


def test_catalog_matches_consumer_shade_code_alias_and_other_market(monkeypatch, tmp_path):
    from makeup_translator import product_agent
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps([
        {'brand':'YSL Beauty','name':'ALL HOURS FOUNDATION','aliases':['恒久粉底液'],
         'shade':'LN4 - Light Neutral 4','market':'美国'},
        {'brand':'YSL Beauty','name':'ALL HOURS FOUNDATION','aliases':['恒久粉底液'],
         'shade':'LN7 - Light Neutral 7','market':'美国'},
    ]))
    monkeypatch.setattr(product_agent, 'CATALOG_PATH', catalog)
    record = ProductRecord(id='ysl', raw_brand='ysl', raw_name='圣罗兰恒久粉底液',
                           raw_model_or_shade='LN4', raw_market='中国大陆')
    hits = lookup_catalog(record)
    assert len(hits) == 1
    assert hits[0]['shade'] == 'LN4 - Light Neutral 4'


def test_catalog_shade_prefixes_do_not_cross_match(monkeypatch, tmp_path):
    from makeup_translator import product_agent
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps([
        {'brand':'Test','name':'Foundation','aliases':[],'shade':'N4','market':'国际'},
        {'brand':'Test','name':'Foundation','aliases':[],'shade':'LN4','market':'国际'},
        {'brand':'Test','name':'Foundation','aliases':[],'shade':'N-012 瓷白','market':'国际'},
    ]))
    monkeypatch.setattr(product_agent, 'CATALOG_PATH', catalog)
    assert [x['shade'] for x in lookup_catalog(ProductRecord(
        id='n4', raw_brand='Test', raw_name='Foundation', raw_model_or_shade='N4'))] == ['N4']
    assert [x['shade'] for x in lookup_catalog(ProductRecord(
        id='n12', raw_brand='Test', raw_name='Foundation', raw_model_or_shade='N12'))] == ['N-012 瓷白']


def test_catalog_missing_shade_stays_ambiguous(monkeypatch, tmp_path):
    from makeup_translator import product_agent
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps([
        {'brand':'Test','name':'Foundation','aliases':[],'shade':shade,'market':'国际'}
        for shade in ('N1','N2','N3','N4')
    ]))
    monkeypatch.setattr(product_agent, 'CATALOG_PATH', catalog)
    hits = lookup_catalog(ProductRecord(id='many', raw_brand='Test', raw_name='Foundation'))
    assert len(hits) == 3
    assert len({x['shade'] for x in hits}) == 3


def test_catalog_matches_chinese_consumer_shorthand(monkeypatch, tmp_path):
    from makeup_translator import product_agent
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps([{
        'brand':'BOBBI BROWN', 'name':'芭比波朗轻盈持妆粉底液',
        'aliases':['【官方正品】芭比波朗黑磁瓶持妆粉底液防晒保湿控油底妆'],
        'shade':'#W-016 暖瓷白/Warm Porcelain', 'market':'中国大陆',
    }]))
    monkeypatch.setattr(product_agent, 'CATALOG_PATH', catalog)
    hits = lookup_catalog(ProductRecord(id='bb', raw_brand='Bobbi Brown',
        raw_name='黑磁瓶粉底液', raw_model_or_shade='W16'))
    assert len(hits) == 1
    assert hits[0]['shade'].startswith('#W-016')


def baseline():
    pipeline = MakeupTutorialPipeline('fixture')
    products = [OwnedProduct(id='eye',category='eyeshadow',name='眼影'), OwnedProduct(id='cheek',category='blush',name='腮红'), OwnedProduct(id='lip',category='lip',name='唇膏')]
    profile = UserProfile()
    result = pipeline.run(Path('examples/fixture.png'), profile, products)
    return pipeline, products, profile, result


def test_knowledge_retrieval_reserves_every_visible_region():
    pipeline, _, profile, result = baseline()
    analysis = result.analysis.model_copy(deep=True)
    analysis.features.extend([
        analysis.features[0].model_copy(update={'region': Region.complexion}),
        analysis.features[0].model_copy(update={'region': Region.eyebrow}),
    ])
    cards = pipeline.knowledge.retrieve(analysis, profile, limit=8)
    assert {card.region for card in cards} == {feature.region for feature in analysis.features}
    assert len(cards) >= 9  # Per-target coverage is no longer cut by a global limit.


def test_tutorial_tools_dispatch_and_validate(tmp_path, monkeypatch):
    import shutil
    pipeline, products, profile, result = baseline()
    monkeypatch.setenv('MAKEUP_CACHE_PATH', str(tmp_path/'cache.sqlite3'))
    image = tmp_path/'job'/'ref.png'
    image.parent.mkdir()
    shutil.copy('examples/fixture.png', image)
    scripted = ScriptedModel([[('analyze_reference', {})], [('retrieve_techniques', {})], [('validate_tutorial', result.tutorial.model_dump(mode='json'))]])
    actual = run_tutorial_agent(pipeline, image, profile, products, lambda *a: None, llm=scripted)
    assert actual.tutorial.provider == 'qwen-agent:0.0.31'
    assert actual.publication_status == 'preview'
    assert len([t for t in actual.agent_trace if t['name']=='orchestrator']) == 3


def test_tutorial_submission_ids_are_grounded_in_retrieved_state():
    pipeline, products, profile, result = baseline()
    retrieved = [card for card in pipeline.knowledge.cards
                 if card.id in result.retrieved_card_ids and card.region.value != 'eyebrow']
    payload = result.tutorial.model_dump(mode='json')
    payload['estimated_minutes'] = profile.available_minutes
    for step in payload['steps']:
        step['duration_minutes'] = profile.available_minutes
        step.pop('technique_card_id')
        step['product_match'] = {
            'required_category': 'invented',
            'selected_product_id': 'invented-id',
            'match_reason': 'model guess',
            'status': 'owned_match',
        }
    payload['steps'].append({**payload['steps'][-1], 'order': 99, 'region': 'eyebrow'})
    grounded = ground_tutorial_payload(payload, retrieved, products, profile)
    from makeup_translator.models import TutorialPlan
    plan = TutorialPlan.model_validate(grounded)
    assert not validate_tutorial(plan, profile, products, pipeline.knowledge.cards, result.analysis)
    assert all(step.technique_card_id in {card.id for card in pipeline.knowledge.cards}
               for step in plan.steps)
    assert len({step.technique_card_id for step in plan.steps}) == len(plan.steps)
    assert all(step.goal in {card.title for card in retrieved} for step in plan.steps)
    assert all(step.product_match.selected_product_id != 'invented-id' for step in plan.steps)
    assert [step.order for step in plan.steps] == list(range(1, len(plan.steps) + 1))
    assert sum(step.duration_minutes for step in plan.steps) <= profile.available_minutes
    assert plan.estimated_minutes is None


@pytest.mark.parametrize('wrong_category', ['mascara', 'eyeliner', 'lip', 'blush'])
def test_wrong_product_function_never_matches(wrong_category):
    pipeline, _, _, result = baseline()
    card = next(c for c in pipeline.knowledge.cards if c.product_categories == ['eyeshadow'])
    product = OwnedProduct(id='wrong', category=wrong_category, name='错误用途', finish=['matte'])
    assert match_product('eyeshadow', [product], card).status == 'missing'


def test_constraint_rejects_unknown_product_time_and_tool():
    pipeline, products, profile, result = baseline()
    result.tutorial.steps[0].product_match.selected_product_id = 'fake'
    result.tutorial.steps[0].duration_minutes = 30
    profile.available_tools = []
    errors = validate_tutorial(result.tutorial, profile, products, pipeline.knowledge.cards, result.analysis)
    assert any('清单' in e for e in errors)
    assert not any('时长' in e for e in errors)
    assert any('工具' in e for e in errors)


def test_revision_locks_and_unrelated_steps():
    pipeline, products, profile, result = baseline()
    session = initial_session(result.model_dump(mode='json'))
    metadata = {'profile': profile.model_dump(), 'products': [p.model_dump() for p in products]}
    session['states']['1'] = 'completed'
    with pytest.raises(ValueError, match='锁定'):
        preview_revision(session, metadata, RevisionRequest(action='simplify', step=1, base_version=session['active_version']))
    revision = preview_revision(session, metadata, RevisionRequest(action='simplify', step=2, base_version=session['active_version']))
    assert revision['result']['tutorial']['steps'][0] == result.tutorial.model_dump(mode='json')['steps'][0]
    assert revision['changed_steps'] == [2]
    assert len(session['versions']) == 1


def test_impossible_remaining_time_and_stale_revision_rejected():
    _, products, profile, result = baseline()
    session = initial_session(result.model_dump(mode='json'))
    metadata = {'profile':profile.model_dump(), 'products':[p.model_dump() for p in products]}
    with pytest.raises(ValueError, match='时间不足'):
        preview_revision(session, metadata, RevisionRequest(action='shorten', minutes=1, base_version=session['active_version']))
    with pytest.raises(ValueError, match='版本已变化'):
        preview_revision(session, metadata, RevisionRequest(action='simplify', base_version='stale'))


def test_unknown_site_and_fabricated_excerpt_cannot_be_official(monkeypatch, tmp_path):
    from makeup_translator import product_agent
    from makeup_translator.product_models import SearchAttempt, ProductEnrichment, ProductSource
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-only')
    monkeypatch.setenv('MAKEUP_CACHE_PATH', str(tmp_path/'cache.sqlite3'))
    url = 'https://unknown.example/product'
    sources = [{'title':'Unknown Test Blush','url':url,'_search_scope':'identity'}]
    monkeypatch.setattr(product_agent.QwenProductEnricher, '_run_search',
        lambda *args: (sources, SearchAttempt(stage='identity_search',status='success',duration_ms=0,result_count=1)))
    monkeypatch.setattr(product_agent, 'read_public_page', lambda *args: {'url':url,'text':'Unknown Test Blush product listing','checked_at':'2026-09-18T00:00:00+00:00'})
    candidate = ProductEnrichment(match_status='exact_match', canonical_brand='Unknown', canonical_name='Test Blush',
        category='blush', official_url=url, official_description='fabricated',
        field_evidence={'official_description':{'url':url,'excerpt':'fabricated'}},
        sources=[ProductSource(title='Unknown Test Blush',url=url,source_type='official',supports_fields=['official_description'])])
    model = ScriptedModel([[('lookup_catalog',{})], [('search_sources',{})], [('read_pages',{'urls':[url]})], [('submit_product',candidate.model_dump(mode='json'))]])
    result = run_product_agent(ProductRecord(id='test',raw_brand='Unknown',raw_name='Test Blush'), [], lambda *args: None, llm=model)
    assert result['official_url'] is None
    assert result['official_description'] is None
    assert not result['field_evidence']
    assert all(source['source_type']!='official' for source in result['sources'])
    assert result['match_status']=='ambiguous'


def test_recent_verified_flagship_catalog_entry_is_usable(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from makeup_translator import product_agent
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps([{
        'id':'tmall-lip-1','brand':'Test Brand','name':'Test Lipstick','shade':None,
        'market':'中国大陆','version':None,'category':'lip','aliases':[],
        'source_url':'https://detail.tmall.com/item.htm?id=1',
        'verified_at':datetime.now(timezone.utc).isoformat(),
        'verification_status':'official_listing_observed',
        'source_kind':'official_flagship_tmall','store_identity':'Test Brand官方旗舰店',
        'store_identity_verified':True,'evidence':{'name':'Test Lipstick'},
    }]))
    monkeypatch.setattr(product_agent, 'CATALOG_PATH', catalog)
    monkeypatch.setenv('MAKEUP_CACHE_PATH', str(tmp_path/'cache.sqlite3'))
    result = product_agent.run_product_agent(
        ProductRecord(id='flagship', raw_brand='Test Brand', raw_name='Test Lipstick'),
        [], lambda *_args: None,
        llm=ScriptedModel([[('lookup_catalog', {})]]),
    )
    assert result['match_status'] == 'likely_match'
    assert result['official_url'] is None
    assert result['sources'][0]['source_type'] == 'official_store'
    assert result['sources'][0]['validation']['reachable'] is True


def test_replacement_propagates_only_to_referencing_dependencies():
    _, products, profile, result = baseline()
    eye = result.tutorial.steps[0]
    eye.instruction = '使用眼影轻扫眼尾'
    dependent = eye.model_copy(deep=True)
    dependent.order = 2
    dependent.depends_on = [1]
    result.tutorial.steps[1] = dependent
    replacement = OwnedProduct(id='eye2',category='eyeshadow',name='另一盘眼影')
    products.append(replacement)
    session = initial_session(result.model_dump(mode='json'))
    metadata = {'profile':profile.model_dump(), 'products':[p.model_dump() for p in products]}
    payload = RevisionRequest(action='swap_product',step=1,value='eye2',base_version=session['active_version'])
    preview = preview_revision(session,metadata,payload)
    assert preview['changed_steps']==[1,2]
    assert preview['result']['tutorial']['steps'][1]['product_match']['selected_product_id']=='eye2'
    assert preview['result']['tutorial']['steps'][2]==result.tutorial.steps[2].model_dump(mode='json')
    session['states']['2']='completed'
    with pytest.raises(ValueError, match='已锁定的依赖步骤'):
        preview_revision(session,metadata,payload)
