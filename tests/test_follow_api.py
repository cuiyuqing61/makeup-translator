from io import BytesIO
import time
from PIL import Image
from fastapi.testclient import TestClient
from makeup_translator.api import create_app
from tests.test_v2 import wait_for_task


def test_unfinished_follow_and_feedback_routes_are_not_exposed(tmp_path):
    image = BytesIO()
    Image.new('RGB',(64,64)).save(image,format='PNG')
    with TestClient(create_app(store_path=tmp_path/'products.json',frontend_dist=None)) as client:
        task = client.post('/api/tutorials',files={'image':('ref.png',image.getvalue(),'image/png')},data={'provider':'fixture'}).json()
        assert wait_for_task(client,task['id'])['status']=='succeeded'
        base = '/api/tutorials/'+task['id']
        assert client.get(base+'/follow').status_code == 404
        assert client.put(base+'/steps/1',json={'state':'completed'}).status_code == 404
        assert client.post(base+'/revisions',json={'action':'simplify','step':1}).status_code == 404
        assert client.get(base+'/feedback').status_code == 404
        assert client.put(base+'/feedback',json={'difficulty':'hard'}).status_code == 404
        assert client.delete(base+'/feedback').status_code == 404
        assert client.get(base).status_code == 200


def test_reply_cancel_after_restart_does_not_resurrect_product(tmp_path):
    from tests.task_worker_helpers import clarification_worker
    app = create_app(store_path=tmp_path/'products.json',frontend_dist=None)
    with TestClient(app) as client:
        product = client.post('/api/products',json={'brand':'品牌','name':'腮红'}).json()
        task = app.state.task_manager.submit(kind='product_enrichment',worker=clarification_worker,args=(),target_id=product['id'],timeout_seconds=5)
        assert wait_for_task(client,task.id)['status']=='waiting_user'
        client.delete('/api/products/'+product['id'])
        assert client.get('/api/tasks/'+task.id).json()['status']=='cancelled'
        assert client.post('/api/tasks/'+task.id+'/reply',json={'answer':'系列A'}).status_code==409
        assert client.get('/api/products').json()==[]


def test_legacy_tutorial_history_restores_diagrams_after_restart(tmp_path):
    image = BytesIO()
    Image.new('RGB', (64, 64)).save(image, format='PNG')
    store_path = tmp_path / 'products.json'
    app = create_app(store_path=store_path, frontend_dist=None)
    with TestClient(app) as client:
        task = client.post('/api/tutorials', files={'image': ('ref.png', image.getvalue(), 'image/png')},
                           data={'provider': 'fixture'}).json()
        assert wait_for_task(client, task['id'])['status'] == 'succeeded'
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            item = client.get(f"/api/tutorials/{task['id']}").json()
            if item['steps_result']:
                break
            time.sleep(.05)
        db = app.state.task_manager.storage
        step_task_id = item['steps_task_id']
        task_row = db.get('task', step_task_id)
        task_row['result'].pop('step_illustrations')
        db.put('task', step_task_id, task_row)

    with TestClient(create_app(store_path=store_path, frontend_dist=None)) as client:
        history = client.get('/api/tutorials').json()
        item = next(row for row in history if row['id'] == task['id'])
        result = item['steps_result']
        assert len(result['step_illustrations']) == len(result['tutorial']['steps'])
