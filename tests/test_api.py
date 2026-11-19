from io import BytesIO
from pathlib import Path
import time

from fastapi.testclient import TestClient
from PIL import Image

from makeup_translator.api import _key_edit_orders, create_app
from makeup_translator.product_store import ProductStore


def test_default_step_edits_skip_productless_grooming():
    steps = [
        {'order': 1, 'region': 'complexion', 'product_match': {'status': 'missing'}},
        {'order': 2, 'region': 'eyebrow', 'product_match': {'status': 'not_needed'}},
        {'order': 3, 'region': 'eye', 'product_match': {'status': 'missing'}},
        {'order': 4, 'region': 'eye', 'product_match': {'status': 'missing'}},
        {'order': 5, 'region': 'cheek', 'product_match': {'status': 'catalog_candidate'}},
        {'order': 6, 'region': 'lip', 'product_match': {'status': 'catalog_candidate'}},
    ]
    assert _key_edit_orders(steps) == {1, 3, 5, 6}


def test_product_api_uses_draft_workflow(tmp_path: Path):
    app = create_app(store_path=tmp_path / "products.json", frontend_dist=None)
    with TestClient(app) as client:
        created = client.post(
            "/api/products",
            json={
                "brand": "示例品牌",
                "name": "示例唇膏",
                "model_or_shade": "101",
                "market": "中国大陆",
            },
        )
        assert created.status_code == 201
        assert created.json()["status"] == "draft"

        products = client.get("/api/products")
        assert products.status_code == 200
        assert [item["id"] for item in products.json()] == [created.json()["id"]]


def test_tutorial_api_reuses_fixture_pipeline(tmp_path: Path):
    app = create_app(store_path=tmp_path / "products.json", frontend_dist=None)
    image = BytesIO()
    Image.new("RGB", (64, 64), color=(201, 154, 145)).save(image, format="PNG")
    image.seek(0)
    with TestClient(app) as client:
        response = client.post(
            "/api/tutorials",
            files={"image": ("reference.png", image, "image/png")},
            data={
                "provider": "fixture",
                "profile_json": '{"skill_level":"beginner","available_minutes":15}',
            },
        )
        assert response.status_code == 202
        task = response.json()
        assert task["status"] in {"queued", "running"}

        deadline = time.monotonic() + 5
        while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.05)
            task = client.get(f"/api/tasks/{task['id']}").json()

        assert task["status"] == "succeeded"
        payload = task["result"]
        assert payload["analysis"]["provider"].startswith("fixture")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            detail = client.get(f"/api/tutorials/{task['id']}").json()
            if detail['steps_result'] and detail['products_result']:
                break
            time.sleep(.05)
        assert detail['stages']['steps']['status'] == 'succeeded'
        assert detail['stages']['products']['status'] == 'succeeded'
        assert [step["order"] for step in detail['steps_result']["tutorial"]["steps"]] == [1, 2, 3]
        assert detail['products_result']['items']
        image_rows = client.get(f"/api/tutorials/{task['id']}/images").json()
        assert len(image_rows) == 3
        assert all(row['source'] == 'svg' and row['status'] == 'unavailable' for row in image_rows)


def test_legacy_ai_step_image_is_not_reused(tmp_path: Path):
    store_path = tmp_path / 'products.sqlite3'
    app = create_app(store_path=store_path, frontend_dist=None)
    image = BytesIO()
    Image.new('RGB',(64,64),'#c99a91').save(image,format='PNG')
    with TestClient(app) as client:
        response = client.post('/api/tutorials',
            files={'image':('reference.png',image.getvalue(),'image/png')},
            data={'provider':'fixture','profile_json':'{}'})
        task_id = response.json()['id']
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            task = client.get(f'/api/tasks/{task_id}').json()
            if task['status']=='succeeded': break
            time.sleep(.05)
        assert task['status']=='succeeded'
        ProductStore(store_path).db.put('step_image',f'{task_id}-1',{
            'id':f'{task_id}-1','tutorial_id':task_id,'step_order':1,
            'source':'ai_effect','status':'succeeded','image_url':'/api/media/old/edited.png'})
        rows = client.get(f'/api/tutorials/{task_id}/images').json()
        assert rows[0]['source']=='svg'
        assert rows[0]['status']=='unavailable'
        assert not rows[0].get('image_url')
