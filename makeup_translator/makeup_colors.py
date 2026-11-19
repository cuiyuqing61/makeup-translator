"""Pixel evidence for makeup colours. Numbers are camera observations, not calibrated shade IDs."""
from __future__ import annotations

import hashlib
import io
import json
import math
import statistics
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

from .models import ColorSample, Confidence, MakeupFeature

ALGORITHM_VERSION = 'pixel-colour-v2'


def _box(value) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError('颜色区域需要四个归一化坐标')
    x1, y1, x2, y2 = (float(v) for v in value)
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ValueError('颜色区域坐标必须在 0 到 1 之间')
    if (x2-x1)*(y2-y1) < .0001:
        raise ValueError('颜色区域太小')
    return x1, y1, x2, y2


def sample_color(path: Path, box, label: str, source: str) -> ColorSample:
    x1, y1, x2, y2 = _box(box)
    with Image.open(path) as source_image:
        oriented = ImageOps.exif_transpose(source_image)
        profile = oriented.info.get('icc_profile')
        if profile:
            try:
                image = ImageCms.profileToProfile(oriented.convert('RGB'),
                    ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                    ImageCms.createProfile('sRGB'), outputMode='RGB')
            except (OSError, ValueError):
                image = oriented.convert('RGB')
        else:
            image = oriented.convert('RGB')
        width, height = image.size
        # Exclude borders, printed pan labels and separators when the box is approximate.
        margin_x, margin_y = (x2-x1)*.13, (y2-y1)*.13
        area = image.crop((int((x1+margin_x)*width), int((y1+margin_y)*height),
                           max(int((x1+margin_x)*width)+1, int((x2-margin_x)*width)),
                           max(int((y1+margin_y)*height)+1, int((y2-margin_y)*height))))
        area.thumbnail((100, 100))
        pixels = list(area.get_flattened_data())
    if len(pixels) < 20:
        raise ValueError('颜色区域分辨率不足')
    # Reject clipped glare and deep shadows, while keeping pale/low-saturation pigment.
    valid = [p for p in pixels if not (min(p) > 247 or max(p) < 17)]
    if len(valid) < max(20, len(pixels)//4):
        raise ValueError('颜色区域反光或阴影过多')
    channels = [round(statistics.median(p[i] for p in valid)) for i in range(3)]
    confidence = Confidence.medium if len(valid) >= 150 and len(valid)/len(pixels) >= .55 else Confidence.low
    return ColorSample(label=label[:60] or '可见颜色', hex='#' + ''.join(f'{n:02X}' for n in channels),
                       source=source, confidence=confidence, box=[x1,y1,x2,y2])


def sample_surrounding_color(path: Path, box) -> str:
    """Estimate neighboring skin so visible pigment can transfer as a color difference."""
    x1,y1,x2,y2 = _box(box)
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        width,height = image.size
        dx,dy = (x2-x1)*.55,(y2-y1)*.55
        left,top = max(0,x1-dx),max(0,y1-dy)
        right,bottom = min(1,x2+dx),min(1,y2+dy)
        surrounding = image.crop((int(left*width),int(top*height),
                                  max(int(left*width)+1,int(right*width)),
                                  max(int(top*height)+1,int(bottom*height))))
        surrounding.thumbnail((160,160))
        pixels = list(surrounding.get_flattened_data())
        sx,sy = surrounding.size
        chosen = [p for index,p in enumerate(pixels)
                  if not (x1 <= left+(index%sx+.5)/sx*(right-left) <= x2 and
                          y1 <= top+(index//sx+.5)/sy*(bottom-top) <= y2)
                  and not (min(p)>247 or max(p)<17)]
    if len(chosen)<20:
        raise ValueError('颜色区域周围缺少可靠皮肤参照')
    values = [round(statistics.median(pixel[i] for pixel in chosen)) for i in range(3)]
    return '#'+''.join(f'{value:02X}' for value in values)


def extract_product_samples(path: Path, boxes: list[dict]) -> list[ColorSample]:
    result = []
    for index, item in enumerate(boxes[:16]):
        try:
            result.append(sample_color(path, item['box'], str(item.get('label') or f'色块 {index+1}'), 'product_photo'))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def product_color_worker(photo_path: str, report) -> dict:
    """Vision locates exposed pigment; local pixels supply RGB. No packaging colour guessing."""
    from .photo import observe_photos
    path = Path(photo_path)
    report(15, 'locating', '正在定位产品本体与各个色块')
    mime = 'image/png' if path.suffix.lower() == '.png' else 'image/webp' if path.suffix.lower() == '.webp' else 'image/jpeg'
    payload = observe_photos([(path.read_bytes(), mime)], 'product_color')
    boxes = payload.get('regions', [])
    if not isinstance(boxes, list):
        boxes = []
    report(65, 'sampling', '正在从原始照片提取可见颜色')
    samples = extract_product_samples(path, boxes)
    if not samples:
        raise ValueError('未能可靠定位产品色块；请在均匀白光下拍摄打开后的产品，避开反光和外包装')
    return {'samples': [sample.model_dump(mode='json') for sample in samples],
            'algorithm': ALGORITHM_VERSION,
            'photo_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def add_reference_samples(path: Path, features: list[MakeupFeature]) -> None:
    for feature in features:
        for index, box in enumerate(feature.color_boxes[:3]):
            try:
                feature.color_samples.append(sample_color(path, box,
                    f'{feature.region.value} {index+1}', 'reference_photo'))
            except (TypeError, ValueError):
                pass


def _lab(hex_color: str) -> tuple[float, float, float]:
    rgb = [int(hex_color[i:i+2], 16)/255 for i in (1,3,5)]
    linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in rgb]
    x = (.4124564*linear[0]+.3575761*linear[1]+.1804375*linear[2])/.95047
    y = .2126729*linear[0]+.7151522*linear[1]+.0721750*linear[2]
    z = (.0193339*linear[0]+.1191920*linear[1]+.9503041*linear[2])/1.08883
    f = lambda t: t**(1/3) if t > .008856 else 7.787*t+16/116
    fx,fy,fz = f(x),f(y),f(z)
    return 116*fy-16, 500*(fx-fy), 200*(fy-fz)


def color_distance(a: str, b: str) -> float:
    la, lb = _lab(a), _lab(b)
    return math.sqrt(sum((x-y)**2 for x,y in zip(la,lb)))


def catalog_suggestions(category: str, families: list[str], limit: int = 3) -> list[str]:
    """Shade-name hints only. Catalog contains no calibrated swatches."""
    if not families or category not in {'lip', 'blush', 'eyeshadow', 'eyeliner'}:
        return []
    path = Path(__file__).resolve().parent.parent / 'data' / 'product_catalog.json'
    try:
        records = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    terms = {'粉': ('pink','rose','peach','coral'), '红': ('red','rose','berry','coral'),
             '棕': ('brown','chocolate','coffee','tea'), '橘': ('orange','peach','coral'),
             '紫': ('mauve','purple','plum'), '裸': ('nude','beige'), '金': ('gold','champagne')}
    wanted = {word for family in families for char, words in terms.items() if char in family for word in words}
    wanted.update(word for family in families for word in family.lower().split() if len(word) > 3)
    found = []
    for record in records:
        if record.get('category') != category:
            continue
        shade = str(record.get('shade') or '')
        if not any(word in shade.lower() for word in wanted):
            continue
        display = ' '.join(filter(None, [record.get('brand'), record.get('name'), shade]))
        if display not in found:
            found.append(display)
        if len(found) >= limit:
            break
    return found


def catalog_candidates(category: str, families: list[str], limit: int = 5) -> list[dict]:
    """Official-listing shade-name candidates, never claimed as measured color matches."""
    if not families:
        return []
    path = Path(__file__).resolve().parent.parent / 'data' / 'product_catalog.json'
    records = json.loads(path.read_text(encoding='utf-8'))
    terms = {'粉': ('pink', 'rose', 'peach', 'coral'), '红': ('red', 'rose', 'berry'),
             '棕': ('brown', 'chocolate', 'coffee', 'tea'), '橘': ('orange', 'peach', 'coral'),
             '紫': ('mauve', 'purple', 'plum'), '裸': ('nude', 'beige'), '金': ('gold', 'champagne')}
    wanted = {word for family in families for char, words in terms.items() if char in family for word in words}
    wanted.update(word for family in families for word in family.lower().split() if len(word) > 3)
    ranked = []
    for record in records:
        if (record.get('category') != category or not record.get('source_url')
                or record.get('verification_status') != 'official_listing_observed'):
            continue
        shade = str(record.get('shade') or '').lower()
        score = sum(word in shade for word in wanted)
        if score:
            ranked.append((score, record))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]['id']))
    return [{'id': record['id'], 'category': record['category'],
             'name': ' '.join(filter(None, [record.get('brand'), record.get('name'), record.get('shade')])),
             'source_url': record['source_url'], 'evidence': 'official_listing_shade_name_only'}
            for _, record in ranked[:limit]]
