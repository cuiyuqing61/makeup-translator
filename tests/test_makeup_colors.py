from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from makeup_translator.api import create_app
from makeup_translator.knowledge import TechniqueKnowledgeBase
from makeup_translator.makeup_colors import extract_product_samples, sample_surrounding_color
from makeup_translator.models import ColorSample, Confidence, MakeupFeature, OwnedProduct, ProductMatch, Region
from makeup_translator.pipeline import match_product
from makeup_translator.product_store import ProductStore
from tests.task_worker_helpers import delayed_color_worker
from tests.test_product_workflow import sample_enrichment
from tests.test_v2 import wait_for_task


def png(color):
    buffer = BytesIO()
    Image.new('RGB', (100, 100), color).save(buffer, format='PNG')
    return buffer.getvalue()


def test_local_sampling_separates_palette_pans(tmp_path):
    path = tmp_path / 'palette.png'
    image = Image.new('RGB', (200, 100), '#ffffff')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 99, 99), fill='#a04359')
    draw.rectangle((100, 0, 199, 99), fill='#ad834e')
    image.save(path)
    samples = extract_product_samples(path, [
        {'label': '左盘', 'box': [0, 0, .49, 1]},
        {'label': '右盘', 'box': [.51, 0, 1, 1]},
    ])
    assert [s.hex for s in samples] == ['#A04359', '#AD834E']
    assert [s.label for s in samples] == ['左盘', '右盘']


def test_reference_pigment_has_local_skin_comparison(tmp_path):
    path = tmp_path / 'reference.png'
    image = Image.new('RGB',(100,100),'#D8AD9C')
    ImageDraw.Draw(image).rectangle((40,40,60,60),fill='#A06B73')
    image.save(path)
    assert sample_surrounding_color(path,[.4,.4,.61,.61]) == '#D8AD9C'


def test_owned_color_ranking_and_catalog_is_only_hint():
    card = next(c for c in TechniqueKnowledgeBase().cards if c.id == 'lip-even-lipstick')
    target = MakeupFeature(region=Region.lip, effect='豆沙色嘴唇', evidence='可见唇色', confidence=Confidence.medium,
        color_family=['豆沙'], color_samples=[ColorSample(label='唇部', hex='#9B4055', source='reference_photo', confidence=Confidence.medium, box=[.2,.2,.3,.3])])
    products = [
        OwnedProduct(id='orange', category='lip', name='橘色口红', color_samples=[ColorSample(label='01',hex='#F58C31', source='product_photo',confidence=Confidence.medium,box=[.1,.1,.4,.4])]),
        OwnedProduct(id='rose', category='lip', name='豆沙口红', color_samples=[ColorSample(label='02',hex='#A34358', source='product_photo',confidence=Confidence.medium,box=[.1,.1,.4,.4])]),
    ]
    match = match_product('lip', products, card, target)
    assert match.selected_product_id == 'rose'
    assert match.selected_color_label == '02'
    assert match.color_evidence == 'photo'
    far = match_product('lip', products[:1], card, target)
    assert far.status == 'missing'
    assert far.selected_product_id is None
    assert ProductMatch.model_validate({'required_category':'lip','match_reason':'旧记录','status':'missing'}).catalog_suggestions == []


def test_palette_must_cover_all_visible_reference_colors():
    card = next(c for c in TechniqueKnowledgeBase().cards if c.region == Region.eye and 'eyeshadow' in c.product_categories)
    target = MakeupFeature(region=Region.eye, effect='双色眼影', evidence='两处明显颜色', confidence=Confidence.medium,
        color_samples=[ColorSample(label='浅色',hex='#D8BA91',source='reference_photo',confidence=Confidence.medium,box=[.1,.1,.2,.2]),
                       ColorSample(label='深色',hex='#684338',source='reference_photo',confidence=Confidence.medium,box=[.2,.2,.3,.3])])
    one_pan = OwnedProduct(id='one',category='eyeshadow',name='单色眼影',color_samples=[ColorSample(label='浅',hex='#D8BA91',source='product_photo',confidence=Confidence.medium,box=[.1,.1,.2,.2])])
    two_pans = OwnedProduct(id='two',category='eyeshadow',name='双色眼影',color_samples=[ColorSample(label='浅',hex='#D6B88F',source='product_photo',confidence=Confidence.medium,box=[.1,.1,.2,.2]),
        ColorSample(label='深',hex='#694439',source='product_photo',confidence=Confidence.medium,box=[.3,.3,.4,.4])])
    result = match_product('eyeshadow',[one_pan,two_pans],card,target)
    assert result.selected_product_id == 'two'
    assert result.selected_color_label == '浅、深'


def test_typed_photo_upload_preserves_confirmation_and_replaces_stale_color(tmp_path, monkeypatch):
    monkeypatch.setattr('makeup_translator.makeup_colors.product_color_worker', delayed_color_worker)
    store_path = tmp_path / 'products.json'
    with TestClient(create_app(store_path=store_path, frontend_dist=None)) as client:
        product = client.post('/api/products', json={'brand':'测试', 'name':'唇膏'}).json()
        store = ProductStore(store_path)
        store.set_pending(product['id'], sample_enrichment())
        store.confirm(product['id'])
        first = client.post(f"/api/products/{product['id']}/photos/opened", files={'image':('red.png',png('#A04359'),'image/png')})
        assert first.status_code == 200
        first_task = first.json()['task']['id']
        second = client.post(f"/api/products/{product['id']}/photos/opened", files={'image':('orange.png',png('#D28A48'),'image/png')})
        assert second.status_code == 200
        assert client.get(f'/api/tasks/{first_task}').json()['status'] == 'cancelled'
        assert wait_for_task(client, second.json()['task']['id'])['status'] == 'succeeded'
        cached_record = store.get(product['id'])
        cached_record.color_samples = []
        store._replace(cached_record)
        assert client.post(f"/api/products/{product['id']}/colors/analyze").json()['task'] is None
        package = client.post(f"/api/products/{product['id']}/photos/package", files={'image':('box.png',png('#FFFFFF'),'image/png')})
        assert package.status_code == 200
        current = client.get(f"/api/products/{product['id']}").json()
        assert current['status'] == 'confirmed'
        assert current['color_samples'][0]['hex'] == '#D28A48'
        assert current['photo_urls'] and current['opened_photo_url']
        corrected = client.put(f"/api/products/{product['id']}/colors/regions", json={'regions':[{'label':'手选一号色','box':[.1,.1,.9,.9]}]})
        assert corrected.status_code == 200
        assert corrected.json()['color_samples'][0]['label'] == '手选一号色'
        assert corrected.json()['status'] == 'confirmed'
        repeated = client.post(f"/api/products/{product['id']}/photos/opened", files={'image':('orange.png',png('#D28A48'),'image/png')})
        assert repeated.json()['task'] is None
        assert client.delete(f"/api/products/{product['id']}/photos/opened").status_code == 200
        assert client.get(f"/api/products/{product['id']}").json()['color_samples'] == []


def test_deleting_product_cancels_color_and_removes_photo(tmp_path, monkeypatch):
    monkeypatch.setattr('makeup_translator.makeup_colors.product_color_worker', delayed_color_worker)
    with TestClient(create_app(store_path=tmp_path/'products.json', frontend_dist=None)) as client:
        product = client.post('/api/products', json={'brand':'测试','name':'唇膏'}).json()
        uploaded = client.post(f"/api/products/{product['id']}/photos/opened", files={'image':('pan.png',png('#A04359'),'image/png')}).json()
        task_id = uploaded['task']['id']
        media_url = uploaded['product']['opened_photo_url']
        assert client.delete(f"/api/products/{product['id']}").status_code == 200
        assert client.get(f'/api/tasks/{task_id}').json()['status'] == 'cancelled'
        assert client.get(media_url).status_code == 404
