from __future__ import annotations

import time
from collections.abc import Callable


def slow_worker(
    seconds: float,
    report: Callable[[int, str, str], None],
) -> dict[str, bool]:
    report(12, "slow_test", "test worker is running")
    time.sleep(seconds)
    return {"done": True}


def delayed_enrichment_worker(record, photos, report):
    from tests.test_product_workflow import sample_enrichment
    report(20, 'test_search', '正在核验测试来源')
    time.sleep(.7)
    return sample_enrichment().model_dump(mode='json')


def clarification_worker(report):
    return {'_waiting_user': {'question': '哪个系列？', 'choices': [], 'round': 1, 'checkpoint': {}}}


def delayed_color_worker(photo_path, report):
    from PIL import Image
    from makeup_translator.makeup_colors import sample_color
    report(15, 'test_color', '正在读取颜色')
    time.sleep(.35)
    with Image.open(photo_path) as image:
        width, height = image.size
    return {'samples': [sample_color(__import__('pathlib').Path(photo_path), [.1,.1,.9,.9], '测试色块', 'product_photo').model_dump(mode='json')], 'algorithm': 'test'}


def failing_tutorial_worker(*args):
    report = args[-1]
    report(25, 'test_failure', '模拟分支失败')
    raise ValueError('模拟分支失败')


def checkpoint_then_wait_worker(report):
    report.event('checkpoint', {'stage': '来源页面已读取',
        'sources': [{'url': 'https://example.com/item', 'title': '候选来源'}],
        'verified_fields': {'canonical_name': {'value': '测试口红',
            'url': 'https://example.com/item'}}})
    time.sleep(4)
    return {'unexpected': True}


def delayed_tutorial_steps_worker(image_path, profile, provider, shared, report):
    from makeup_translator.tasks import tutorial_steps_worker
    time.sleep(1.5)
    return tutorial_steps_worker(image_path, profile, provider, shared, report)


def delayed_tutorial_products_worker(shared, products, report):
    from makeup_translator.tasks import tutorial_products_worker
    time.sleep(1.5)
    return tutorial_products_worker(shared, products, report)
