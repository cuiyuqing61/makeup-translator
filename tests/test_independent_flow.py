from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from makeup_translator.api import create_app
from makeup_translator.facial_images import effect_mask, render_effects
from makeup_translator.models import Confidence, MakeupTarget
from makeup_translator.personalized_tutorial import _actionable_target
from makeup_translator.pipeline import MakeupTutorialPipeline
from makeup_translator.models import UserProfile
from makeup_translator.product_agent import partial_product_card
from makeup_translator.product_models import ProductRecord
from makeup_translator.product_store import ProductStore
from makeup_translator.tasks import TaskManager
from tests.task_worker_helpers import (checkpoint_then_wait_worker, delayed_enrichment_worker,
    delayed_tutorial_products_worker, delayed_tutorial_steps_worker, failing_tutorial_worker)
from tests.test_v2 import wait_for_task
from tests.test_product_workflow import sample_enrichment


@pytest.mark.parametrize('failing_stage', ['steps', 'products'])
def test_tutorial_branches_publish_and_retry_independently(tmp_path, monkeypatch, failing_stage):
    import makeup_translator.api as api_module

    original = getattr(api_module, f'tutorial_{failing_stage}_worker')
    monkeypatch.setattr(api_module, f'tutorial_{failing_stage}_worker', failing_tutorial_worker)
    picture = BytesIO()
    Image.new('RGB', (64, 64), '#c99a91').save(picture, 'PNG')
    with TestClient(create_app(store_path=tmp_path / 'products.json', frontend_dist=None)) as client:
        created = client.post('/api/tutorials', files={'image': ('look.png', picture.getvalue(), 'image/png')},
                              data={'provider': 'fixture'}).json()
        assert wait_for_task(client, created['id'])['status'] == 'succeeded'
        detail = client.get(f"/api/tutorials/{created['id']}").json()
        assert wait_for_task(client, detail[f'{failing_stage}_task_id'])['status'] == 'failed'
        other = 'products' if failing_stage == 'steps' else 'steps'
        assert wait_for_task(client, detail[f'{other}_task_id'])['status'] == 'succeeded'
        detail = client.get(f"/api/tutorials/{created['id']}").json()
        assert detail[f'{other}_result']
        assert detail[f'{failing_stage}_result'] is None
        monkeypatch.setattr(api_module, f'tutorial_{failing_stage}_worker', original)
        retried = client.post(f"/api/tutorials/{created['id']}/retry").json()
        assert retried['kind'] == f'tutorial_{failing_stage}'
        assert wait_for_task(client, retried['id'])['status'] == 'succeeded'
        detail = client.get(f"/api/tutorials/{created['id']}").json()
        assert detail['steps_result'] and detail['products_result']


@pytest.mark.parametrize('slow_stage', ['steps', 'products'])
def test_first_completed_branch_is_visible_while_other_runs(tmp_path, monkeypatch, slow_stage):
    delayed = delayed_tutorial_steps_worker if slow_stage == 'steps' else delayed_tutorial_products_worker
    monkeypatch.setattr('makeup_translator.api.tutorial_' + slow_stage + '_worker', delayed)
    picture = BytesIO()
    Image.new('RGB', (64, 64), '#c99a91').save(picture, 'PNG')
    with TestClient(create_app(store_path=tmp_path / 'products.json', frontend_dist=None)) as client:
        created = client.post('/api/tutorials', files={'image': ('look.png', picture.getvalue(), 'image/png')},
                              data={'provider': 'fixture'}).json()
        assert wait_for_task(client, created['id'])['status'] == 'succeeded'
        other = 'products' if slow_stage == 'steps' else 'steps'
        detail = client.get(f"/api/tutorials/{created['id']}").json()
        assert wait_for_task(client, detail[f'{other}_task_id'])['status'] == 'succeeded'
        detail = client.get(f"/api/tutorials/{created['id']}").json()
        assert detail[f'{other}_result']
        assert detail['stages'][slow_stage]['status'] in {'queued', 'running'}
        assert wait_for_task(client, detail[f'{slow_stage}_task_id'])['status'] == 'succeeded'


def test_timeout_keeps_evidence_limited_partial_card(tmp_path):
    manager = TaskManager()
    record = ProductRecord(id='p1', raw_brand='测试', raw_name='测试口红')
    try:
        task = manager.submit(kind='product_enrichment', worker=checkpoint_then_wait_worker,
            args=(), target_id=record.id, timeout_seconds=1.5,
            on_timeout=lambda snapshot: partial_product_card(record, snapshot))
        import time
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and manager.get(task.id).status in {'queued', 'running'}:
            time.sleep(.03)
        finished = manager.get(task.id)
        assert finished.status == 'timed_out'
        assert finished.result['status'] == 'partial'
        assert finished.result['verified_fields']['canonical_name']['value'] == '测试口红'
        assert 'category' in finished.result['pending_fields']
        assert finished.checkpoint['stage'] == '来源页面已读取'
    finally:
        manager.shutdown()


def test_partial_card_hides_unverified_search_sources():
    record = ProductRecord(id='p1', raw_brand='测试', raw_name='测试口红')
    partial = partial_product_card(record, {'stage': '来源搜索已完成',
        'sources': [{'url': 'https://example.org/unverified', 'title': '搜索候选'}]})
    assert partial['sources'] == []
    assert partial['verified_fields'] == {}
    assert partial['search_progress'] == '来源搜索已完成'


def test_confirmed_product_keeps_old_enrichment_until_refresh_confirmed(tmp_path, monkeypatch):
    monkeypatch.setattr('makeup_translator.tasks.product_refresh_worker', delayed_enrichment_worker)
    store_path = tmp_path / 'products.json'
    store = ProductStore(store_path)
    draft = store.add_draft('示例品牌', '示例唇膏', '123', '中国大陆')
    store.set_pending(draft.id, sample_enrichment())
    original = store.confirm(draft.id)
    with TestClient(create_app(store_path=store_path, frontend_dist=None)) as client:
        started = client.post(f'/api/products/{draft.id}/enrich?force_refresh=true').json()
        assert client.get(f'/api/products/{draft.id}').json()['status'] == 'confirmed'
        finished = wait_for_task(client, started['id'])
        assert finished['status'] == 'succeeded'
        assert finished['result']['refresh_candidate']['canonical_name'] == '示例唇膏'
        before = client.get(f'/api/products/{draft.id}').json()
        assert before['updated_at'] == original.model_dump(mode='json')['updated_at']
        confirmed = client.post(f'/api/products/{draft.id}/confirm',
                                json={'acknowledge_unverified': False}).json()
        assert confirmed['status'] == 'confirmed'
        assert confirmed['updated_at'] != before['updated_at']


def test_edit_cancels_old_product_verification_before_commit(tmp_path, monkeypatch):
    monkeypatch.setattr('makeup_translator.api.product_enrichment_worker', delayed_enrichment_worker)
    with TestClient(create_app(store_path=tmp_path / 'products.json', frontend_dist=None)) as client:
        product = client.post('/api/products', json={'brand': '示例品牌', 'name': '旧名称'}).json()
        started = client.post(f"/api/products/{product['id']}/enrich").json()
        edited = client.put(f"/api/products/{product['id']}", json={
            'brand': '示例品牌', 'name': '新名称'}).json()
        assert edited['raw_name'] == '新名称'
        assert client.get(f"/api/tasks/{started['id']}").json()['status'] == 'cancelled'
        assert client.get(f"/api/products/{product['id']}").json()['status'] == 'draft'


def test_natural_lashes_are_preserved_without_mascara_target():
    target = MakeupTarget(id='lashes', region='eye', role='finish', effect='自然睫毛可见',
        placement='上眼睑', evidence='自然睫毛可见，未见明确睫毛妆效',
        confidence=Confidence.medium)
    assert not _actionable_target(target)


def test_reference_retrieval_is_identical_across_user_profiles():
    pipeline = MakeupTutorialPipeline('fixture')
    analysis = pipeline.analyze_image(Path('examples/fixture.png'))
    profiles = [UserProfile(skill_level='beginner', available_minutes=5,
                available_tools=[], face_features_effective={'eyelid': 'monolid'}),
                UserProfile(skill_level='advanced', available_minutes=120,
                available_tools=['眼影刷'], face_features_effective={'eyelid': 'outer_double'})]
    ids = [[card.id for card in pipeline.knowledge.retrieve(analysis, profile)] for profile in profiles]
    assert ids[0] == ids[1]


def test_drawing_anchor_clips_effect_and_missing_color_keeps_base():
    import math
    from makeup_translator.facial_images import FACE_OUTLINE
    # A compact synthetic landmark map is enough to verify mask clipping and color handling.
    points = [(50.0, 50.0)] * 478
    points[234], points[454] = (10.0, 50.0), (90.0, 50.0)
    points[33], points[133], points[159], points[145] = (25.0, 48.0), (40.0, 48.0), (32.0, 40.0), (32.0, 52.0)
    points[362], points[263], points[386], points[374] = (60.0, 48.0), (75.0, 48.0), (68.0, 40.0), (68.0, 52.0)
    for order, index in enumerate(FACE_OUTLINE):
        angle = 2 * math.pi * order / len(FACE_OUTLINE)
        points[index] = (50 + 42 * math.sin(angle), 51 - 44 * math.cos(angle))
    points[234], points[454] = (10.0, 50.0), (90.0, 50.0)
    effect = {'region': 'eye', 'role': 'base', 'card_id': 'eye-neutral-shadow',
              'drawing': {'anchor': 'left_eye', 'side': 'left', 'offset_x': 0,
                          'offset_y': -.08, 'radius_x': .12, 'radius_y': .08}}
    mask = effect_mask(points, (100, 100), effect)
    assert mask.getbbox()
    assert mask.getpixel((68, 38)) == 0
    base = Image.new('RGB', (100, 100), '#ddd2c9')
    rendered, latest = render_effects(base, points, [effect])
    assert rendered.tobytes() == base.tobytes()
    assert latest.getbbox()
