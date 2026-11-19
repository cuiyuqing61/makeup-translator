import time
from pathlib import Path
from fastapi.testclient import TestClient
from makeup_translator.api import create_app
from makeup_translator.tasks import TaskManager
from makeup_translator.storage import Storage
from makeup_translator.product_store import ProductStore
from makeup_translator.product_enrichment import brand_domains, reconcile_search_sources
from tests.task_worker_helpers import slow_worker


def idle_profile_worker(*args):
    time.sleep(2)
    return {}


def wait_for_task(client, task_id, seconds=5):
    deadline = time.monotonic() + seconds
    task = client.get(f'/api/tasks/{task_id}').json()
    while task['status'] in {'queued', 'running'} and time.monotonic() < deadline:
        time.sleep(.03)
        task = client.get(f'/api/tasks/{task_id}').json()
    return task


def test_queue_limits_and_cancel_never_commits():
    manager = TaskManager()
    saved = []
    try:
        tasks = [manager.submit(kind='product_enrichment', worker=slow_worker, args=(.4,), target_id=str(i), timeout_seconds=5, on_success=lambda x: saved.append(x) or x) for i in range(5)]
        assert [t.status for t in tasks] == ['running'] * 3 + ['queued'] * 2
        assert manager.get(tasks[-1].id).elapsed_seconds == 0
        assert manager.get(tasks[-1].id).queue_position == 2
        manager.cancel(tasks[-1].id)
        manager.cancel(tasks[0].id)
        deadline = time.monotonic() + 5
        while any(t.cancellable for t in manager.list()) and time.monotonic() < deadline:
            time.sleep(.03)
        assert len(saved) == 3
        assert manager.get(tasks[0].id).status == 'cancelled'
    finally:
        manager.shutdown()


def test_profile_product_migration_and_delete(tmp_path):
    path = tmp_path / 'products.json'
    path.write_text('{"schema_version":"1.0","products":[]}')
    with TestClient(create_app(store_path=path, frontend_dist=None)) as client:
        assert client.put('/api/profile',json={'eye_notes':'用户纠正的内双'}).status_code == 200
        record = client.post('/api/products',json={'brand':'品牌','name':'名称'}).json()
        assert client.put('/api/products/'+record['id'],json={'brand':'新品牌','name':'新名称'}).json()['raw_name'] == '新名称'
    with TestClient(create_app(store_path=path, frontend_dist=None)) as client:
        assert client.get('/api/profile').json()['eye_notes'] == '用户纠正的内双'
        assert len(client.get('/api/products').json()) == 1
        assert client.delete('/api/products/'+record['id']).status_code == 200
    assert ProductStore(path).load().products == []
    assert path.read_text() == '{"schema_version":"1.0","products":[]}'


def test_restart_marks_incomplete_without_resubmission(tmp_path):
    db = Storage(tmp_path / 'test.sqlite3')
    manager = TaskManager(db)
    task = manager.submit(kind='tutorial', worker=slow_worker,args=(5,),timeout_seconds=10)
    # Persisted active record loaded by a new manager is interrupted, never relaunched.
    restored = TaskManager(db)
    assert restored.get(task.id).stage == 'interrupted'
    assert not restored.get(task.id).cancellable
    manager.shutdown()
    restored.shutdown()


def test_brand_aliases_and_review_cannot_write_official_fields(tmp_path):
    registry = {'LANCOME':['lancome.com'], "L'OREAL PARIS":['lorealparisusa.com']}
    assert brand_domains('Lancôme', registry) == ['lancome.com']
    assert brand_domains('欧莱雅', registry) == ['lorealparisusa.com']
    path = tmp_path/'domains.json'
    path.write_text('{}')
    result = reconcile_search_sources({'canonical_brand':'品牌','canonical_name':'唇膏','official_description':'越权描述','sources':[{'url':'https://xiaohongshu.com/a','source_type':'official','supports_fields':['official_description','review_observations']}]}, [{'url':'https://xiaohongshu.com/a','title':'品牌唇膏','_search_scope':'review'}],path)
    assert result['official_description'] is None
    assert result['sources'][0]['source_type'] == 'review'
    assert result['sources'][0]['supports_fields'] == ['review_observations']


def test_cancel_after_result_before_commit_does_not_call_callback():
    import threading
    manager = TaskManager()
    entered, release = threading.Event(), threading.Event()
    saved = []
    task = manager.submit(kind='tutorial', worker=slow_worker, args=(.2,), timeout_seconds=5, on_success=lambda result: saved.append(result) or result)
    process = manager._tasks[task.id].process
    join = process.join
    def paused_join(timeout=None):
        join(timeout)
        entered.set()
        release.wait(2)
    process.join = paused_join
    assert entered.wait(3)
    manager.cancel(task.id)
    release.set()
    time.sleep(.1)
    assert saved == []
    assert manager.get(task.id).status == 'cancelled'
    manager.shutdown()


def test_photo_cache_user_override_and_deletion(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    calls = []
    def observe(images, purpose):
        calls.append(purpose)
        return {'eye_notes':'照片观察', 'notes':'仅可见观察'}
    monkeypatch.setattr('makeup_translator.photo.observe_photos', observe)
    photo = BytesIO()
    Image.new('RGB',(512,512)).save(photo,format='PNG')
    with TestClient(create_app(store_path=tmp_path/'products.json', frontend_dist=None)) as client:
        client.put('/api/profile',json={'eye_notes':'我的纠正'})
        first = None
        for _ in range(2):
            r = client.post('/api/profile/photo',files={'image':('face.png',photo.getvalue(),'image/png')})
            assert r.status_code == 200
            if first is None:
                first = r.json()
        assert r.json()['version'] == first['version']
        assert r.json()['analysis_task_id'] == first['analysis_task_id']
        assert r.json()['base_task_id'] == first['base_task_id']
        # Upload is asynchronous; identical content keeps its version and task ids.
        assert r.json()['version']
        assert r.json()['analysis_task_id']
        assert r.json()['base_task_id']
        assert calls == []
        assert client.get('/api/profile').json()['eye_notes'] == '我的纠正'
        url = r.json()['image_url']
        assert client.get(url).status_code == 200
        assert client.delete('/api/profile/photo').status_code == 200
        assert client.get(url).status_code == 404
        assert client.post('/api/photos/recognize',files=[('images',('a.png',b'invalid','image/png'))]).status_code == 415


def test_high_resolution_jpg_upload_is_normalized_with_exif_orientation(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image

    monkeypatch.setattr('makeup_translator.facial_images.analysis_worker', idle_profile_worker)
    monkeypatch.setattr('makeup_translator.facial_images.base_worker', idle_profile_worker)
    photo = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new('RGB', (7000, 6000), color=(220, 180, 170)).save(
        photo, format='JPEG', exif=exif,
    )
    with TestClient(create_app(store_path=tmp_path / 'products.json', frontend_dist=None)) as client:
        response = client.post(
            '/api/profile/photo',
            files={'image': ('portrait.JPG', photo.getvalue(), 'image/jpeg')},
        )
        assert response.status_code == 200, response.text
        record = response.json()
        assert record['mime'] == 'image/jpeg'
        saved = client.get(record['image_url'])
        assert saved.status_code == 200
        with Image.open(BytesIO(saved.content)) as image:
            assert image.format == 'JPEG'
            assert image.size == (1755, 2048)
            assert image.getexif().get(274) is None


def test_iphone_style_multiframe_jpg_upload_uses_primary_photo(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image

    monkeypatch.setattr('makeup_translator.facial_images.analysis_worker', idle_profile_worker)
    monkeypatch.setattr('makeup_translator.facial_images.base_worker', idle_profile_worker)
    photo = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    primary = Image.new('RGB', (1024, 768), color=(220, 180, 170))
    secondary = Image.new('RGB', (256, 256), color=(100, 90, 80))
    primary.save(photo, format='MPO', save_all=True, append_images=[secondary], exif=exif)
    with Image.open(BytesIO(photo.getvalue())) as source:
        assert source.format == 'MPO'
    with TestClient(create_app(store_path=tmp_path / 'products.json', frontend_dist=None)) as client:
        response = client.post(
            '/api/profile/photo',
            files={'image': ('portrait.JPG', photo.getvalue(), 'image/jpeg')},
        )
        assert response.status_code == 200, response.text
        record = response.json()
        saved = client.get(record['image_url'])
        assert saved.status_code == 200
        with Image.open(BytesIO(saved.content)) as image:
            assert image.format == 'JPEG'
            assert image.size == (768, 1024)
            assert image.getexif().get(274) is None


def test_delete_running_product_never_reappears(tmp_path, monkeypatch):
    from tests.task_worker_helpers import delayed_enrichment_worker
    monkeypatch.setattr('makeup_translator.api.product_enrichment_worker',delayed_enrichment_worker)
    with TestClient(create_app(store_path=tmp_path/'products.json',frontend_dist=None)) as client:
        product = client.post('/api/products',json={'brand':'测试','name':'测试唇膏'}).json()
        task = client.post('/api/products/'+product['id']+'/enrich').json()
        assert client.delete('/api/products/'+product['id']).status_code == 200
        time.sleep(.3)
        assert client.get('/api/products').json() == []
        assert client.get('/api/tasks/'+task['id']).json()['status'] == 'cancelled'


def test_tutorial_history_survives_restart_and_delete_removes_media(tmp_path):
    from io import BytesIO
    from PIL import Image

    store_path = tmp_path / 'products.json'
    image = BytesIO()
    Image.new('RGB', (32, 32), color=(220, 180, 180)).save(image, format='PNG')
    with TestClient(create_app(store_path=store_path, frontend_dist=None)) as client:
        created = client.post(
            '/api/tutorials',
            files={'image': ('reference.png', image.getvalue(), 'image/png')},
            data={'provider': 'fixture'},
        ).json()
        completed = wait_for_task(client, created['id'])
        assert completed['status'] == 'succeeded'
        detail = client.get(f"/api/tutorials/{created['id']}").json()
        assert wait_for_task(client, detail['steps_task_id'])['status'] == 'succeeded'
        image_url = client.get('/api/tutorials').json()[0]['image_url']
        assert client.get(image_url).status_code == 200

    with TestClient(create_app(store_path=store_path, frontend_dist=None)) as client:
        history = client.get('/api/tutorials').json()
        assert history[0]['task']['status'] == 'succeeded'
        assert history[0]['steps_result']['tutorial']['steps']
        assert client.delete(f"/api/tutorials/{created['id']}").status_code == 200
        assert client.get(image_url).status_code == 404
        assert client.get('/api/tutorials').json() == []


def test_new_product_photo_resets_review_and_cancels_search(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from tests.task_worker_helpers import delayed_enrichment_worker

    monkeypatch.setattr('makeup_translator.api.product_enrichment_worker', delayed_enrichment_worker)
    photo = BytesIO()
    Image.new('RGB', (24, 24), color=(200, 150, 150)).save(photo, format='PNG')
    with TestClient(create_app(store_path=tmp_path / 'products.json', frontend_dist=None)) as client:
        product = client.post('/api/products', json={'brand': '测试', 'name': '测试唇膏'}).json()
        task = client.post(f"/api/products/{product['id']}/enrich").json()
        response = client.post(
            f"/api/products/{product['id']}/photos",
            files={'images': ('label.png', photo.getvalue(), 'image/png')},
        )
        assert response.status_code == 200
        assert response.json()['status'] == 'draft'
        assert len(response.json()['photo_urls']) == 1
        assert client.get(f"/api/tasks/{task['id']}").json()['status'] == 'cancelled'
