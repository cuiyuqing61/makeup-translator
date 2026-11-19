"""Versioned portrait editing and landmark based local makeup illustrations."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

PROMPT_VERSION = "portrait-2.5-v1"
STEP_RENDER_VERSION = "local-makeup-v9-bilateral-arrows"
MODEL_DEFAULT = "gpt-image-2.5-sunburst"
PART_POINTS = {
    "left_eye": (33, 133, 159, 145), "right_eye": (362, 263, 386, 374),
    "left_brow": (70, 107, 105, 66), "right_brow": (336, 296, 334, 300),
    "left_cheek": (50, 101, 117, 123), "right_cheek": (280, 330, 346, 352),
    "lip": (61, 291, 0, 17, 13, 14), "face": (10, 152, 234, 454),
    "nose": (1, 4, 5, 6, 98, 327),
    "eyes": (33, 133, 362, 263, 159, 386, 145, 374),
    "brows": (70, 107, 105, 66, 336, 296, 334, 300),
    "cheeks": (50, 101, 117, 123, 280, 330, 346, 352),
}
REGION_PARTS = {
    "eye": ("eyes",), "eyebrow": ("brows",),
    "cheek": ("cheeks",), "lip": ("lip",),
    "complexion": ("face",),
}


def image_model() -> str:
    return os.getenv("IMAGE_MODEL", MODEL_DEFAULT)


def digest_payload(*items: object) -> str:
    return hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def image_client():
    from openai import OpenAI
    key = os.getenv("IMAGE_API_KEY")
    if not key:
        raise ValueError("未配置 IMAGE_API_KEY；图片将使用 SVG 示意。")
    return OpenAI(api_key=key, base_url=os.getenv("IMAGE_BASE_URL") or "https://api.openai.com/v1", timeout=180, max_retries=0)


def edit_image(source: Path, prompt: str, *, mask: Path | None = None, size: str = "1024x1024") -> bytes:
    with source.open("rb") as image:
        if mask:
            with mask.open("rb") as mask_file:
                result = image_client().images.edit(model=image_model(), image=image, mask=mask_file,
                    prompt=prompt, size=size, quality="medium", output_format="png")
        else:
            result = image_client().images.edit(model=image_model(), image=image,
                prompt=prompt, size=size, quality="medium", output_format="png")
    if not result.data or not result.data[0].b64_json:
        raise ValueError("图片服务没有返回可用图像")
    return base64.b64decode(result.data[0].b64_json)


def landmarks(image: Image.Image) -> list[tuple[float, float]]:
    """Use MediaPipe Face Landmarker; unavailable or ambiguous faces fail closed."""
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "makeup-mpl"))
    try:
        import mediapipe as mp
        import numpy as np
    except (ImportError, AttributeError) as exc:
        raise ValueError("未安装 MediaPipe，无法定位面部关键点") from exc
    model_path = Path(os.getenv("FACE_LANDMARKER_MODEL") or
        Path(__file__).resolve().parent.parent / "third_party" / "magic-makeup" / "mediapipe" / "face_landmarker.task")
    if not model_path.is_file():
        raise ValueError("缺少 Face Landmarker 模型文件")
    options = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path),
                                          delegate=mp.tasks.BaseOptions.Delegate.CPU),
        running_mode=mp.tasks.vision.RunningMode.IMAGE, num_faces=2,
        min_face_detection_confidence=0.6)
    with mp.tasks.vision.FaceLandmarker.create_from_options(options) as detector:
        media = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=np.ascontiguousarray(image.convert("RGB")))
        found = detector.detect(media)
    faces = found.face_landmarks or []
    if len(faces) != 1:
        raise ValueError("请上传只有一张清晰正面人脸的照片")
    return [(p.x * image.width, p.y * image.height) for p in faces[0]]


def check_portrait(image: Image.Image, points: list[tuple[float, float]]) -> None:
    if min(image.size) < 500 or ImageStat.Stat(image.convert("L").filter(ImageFilter.FIND_EDGES)).var[0] < 15:
        raise ValueError("照片分辨率或清晰度不足，请重新上传")
    bounds = [points[i] for i in PART_POINTS["face"]]
    xs, ys = [p[0] for p in bounds], [p[1] for p in bounds]
    if min(xs) < image.width * .04 or max(xs) > image.width * .96 or min(ys) < image.height * .03 or max(ys) > image.height * .97:
        raise ValueError("面部不完整或姿态不合适，请上传无遮挡正面照片")
    left, right = points[33], points[263]
    if abs(left[1] - right[1]) > abs(left[0] - right[0]) * .25:
        raise ValueError("面部倾斜过大，请上传正面照片")


def face_proportions(points: list[tuple[float, float]]) -> tuple[float, float]:
    width = max(1, abs(points[454][0] - points[234][0]))
    return (abs(points[263][0] - points[33][0]) / width,
            abs(points[291][0] - points[61][0]) / width)


def prepare_portrait(path: Path) -> dict:
    with Image.open(path) as original:
        image = original.convert("RGB")
    if min(image.size) < 500 or ImageStat.Stat(image.convert("L")).var[0] < 20:
        raise ValueError("照片分辨率或清晰度不足，请上传清晰正面照")
    points = landmarks(image)
    check_portrait(image, points)
    return {"width": image.width, "height": image.height}


def base_worker(source_path: str, staged_path: str, report) -> dict:
    source = Path(source_path)
    prepare_portrait(source)
    with Image.open(source) as original:
        original_points = landmarks(original.convert("RGB"))
    report(15, "editing", "正在生成标准底图")
    prompt = ("Edit this exact person's bare-face portrait into a neutral front-facing beauty teaching base. "
              "Square centered head-and-shoulders portrait, face relatively large, soft even diffuse light, pale neutral background. "
              "Keep the person's identity, facial proportions, natural skin tone, real skin texture, brows, eyes and cheeks. "
              "Remove any visible makeup; do not beautify, reshape, blur skin, or invent features. No text.")
    raw = edit_image(source, prompt, size="1536x1536")
    with Image.open(io.BytesIO(raw)) as result:
        result = result.convert("RGB")
        if result.size != (1536, 1536):
            raise ValueError("图片服务未返回 1536×1536 底图；请检查尺寸兼容性")
        points = landmarks(result)
        check_portrait(result, points)
        original_ratios, result_ratios = face_proportions(original_points), face_proportions(points)
        if any(abs(a-b) > max(.18, a*.4) for a, b in zip(original_ratios, result_ratios)):
            raise ValueError("标准底图五官比例变化过大，已拒绝用于教学")
        boxes = {part: crop_box(points, part, result.size) for part in PART_POINTS}
        result.save(staged_path, format="PNG")
    report(95, "validating", "正在检查底图")
    return {"staged_path": staged_path, "model": image_model(), "prompt_version": PROMPT_VERSION,
            "crop_boxes": boxes}


def analysis_worker(source_path: str, mime: str, report) -> dict:
    from .photo import observe_photos
    from .face_profile import normalize_analysis
    report(10, "observing", "正在分析可见面部特点")
    result = normalize_analysis(observe_photos([(Path(source_path).read_bytes(), mime)], "profile"))
    if result.get("unsuitable_reason"):
        raise ValueError(result["unsuitable_reason"])
    return result


def crop_box(points: list[tuple[float, float]], part: str, size: tuple[int, int]) -> tuple[int, int, int, int]:
    selected = [points[i] for i in PART_POINTS[part]]
    xs, ys = [p[0] for p in selected], [p[1] for p in selected]
    x, y = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
    span = max(max(xs)-min(xs), max(ys)-min(ys))
    scale = 3.0 if part == "face" else (1.65 if part in ("eyes", "cheeks", "brows") else
                                       3.6 if "cheek" in part else 3.1)
    side = max(160, int(span * scale))
    side = min(side, size[0], size[1])
    left = max(0, min(size[0]-side, int(x-side/2)))
    top = max(0, min(size[1]-side, int(y-side/2)))
    return left, top, left+side, top+side


def local_mask(points: list[tuple[float, float]], part: str, box: tuple[int, int, int, int]) -> Image.Image:
    """White pixels may change; outside remains byte-identical after compositing."""
    selected = [points[i] for i in PART_POINTS[part]]
    xs, ys = [p[0] for p in selected], [p[1] for p in selected]
    scale = 1024 / (box[2] - box[0])
    center = ((min(xs)+max(xs))/2-box[0], (min(ys)+max(ys))/2-box[1])
    x, y = center[0]*scale, center[1]*scale
    width = max(38, (max(xs)-min(xs))*scale*1.45)
    height = max(38, (max(ys)-min(ys))*scale*2.0)
    if "eye" in part:
        width *= 1.18
        height *= 1.28
        y -= height * .12
    if "cheek" in part:
        width *= 1.5; height *= 1.7
    if part == "face":
        width *= .9; height *= .9
    mask = Image.new("L", (1024, 1024), 0)
    top = y-height/2
    if "cheek" in part:
        lower_lid = points[145 if part == 'left_cheek' else 374][1]
        top = max(top, (lower_lid-box[1])*scale + 24)
    ImageDraw.Draw(mask).ellipse((x-width/2, top, x+width/2, y+height/2), fill=255)
    return mask


def mark_operation(image: Image.Image, mask: Image.Image,
                   marker: tuple[int,int] | None = None,
                   direction: str | None = None, side: str | None = None) -> Image.Image:
    output = image.copy().convert("RGB")
    bounds = mask.getbbox()
    if bounds:
        draw = ImageDraw.Draw(output)
        if marker:
            x,y = marker
            draw.ellipse((x-25,y-25,x+25,y+25),outline=(155,82,113),width=4)
        else:
            solid = mask.point(lambda value: 255 if value > 64 else 0)
            edge = ImageChops.subtract(solid.filter(ImageFilter.MaxFilter(9)), solid)
            edge = edge.point(lambda value: min(170, value))
            output = Image.composite(Image.new('RGB', image.size, (155, 82, 113)), output, edge)
            draw = ImageDraw.Draw(output)
            x, y = bounds[0], bounds[1]
        regions = [(bounds, side)]
        if side == 'both' and not marker:
            middle = (bounds[0] + bounds[2]) // 2
            regions = []
            for half, (left, right) in zip(('left', 'right'),
                                           ((bounds[0], middle), (middle, bounds[2]))):
                local = mask.crop((left, bounds[1], right, bounds[3])).getbbox()
                if local:
                    regions.append(((left + local[0], bounds[1] + local[1],
                                     left + local[2], bounds[1] + local[3]), half))
        for region_bounds, region_side in regions:
            if direction:
                cx = (region_bounds[0] + region_bounds[2]) // 2
                cy = (region_bounds[1] + region_bounds[3]) // 2
                dx, dy = {'outward': (1, 0), 'inward': (-1, 0),
                          'upward': (0, -1), 'downward': (0, 1),
                          'along': (1, 0)}.get(direction, (1, 0))
                if region_side == 'left' and direction in {'outward', 'inward'}:
                    dx = -dx
                start = (cx-dx*32, cy-dy*32)
                end = (cx+dx*32, cy+dy*32)
            else:
                start = (max(6, x-58), max(6, y-58))
                end = (x-20,y-20) if marker else (x,y)
                dx,dy = 1,0
            draw.line((*start,*end), fill=(155, 82, 113), width=5)
            base = (end[0]-dx*16, end[1]-dy*16)
            draw.polygon([end, (base[0]-dy*7, base[1]+dx*7),
                          (base[0]+dy*7, base[1]-dx*7)], fill=(155, 82, 113))
    return output


def composite_allowed(edited: Image.Image, original: Image.Image, allowed: Image.Image) -> Image.Image:
    if edited.size != original.size or allowed.size != original.size:
        raise ValueError("局部图和蒙版尺寸不一致")
    return Image.composite(edited.convert("RGB"), original.convert("RGB"), allowed.convert("L"))


FACE_OUTLINE = (10,338,297,332,284,251,389,356,454,323,361,288,397,365,379,378,
                400,377,152,148,176,149,150,136,172,58,132,93,234,127,162,21,54,103,67,109)
LIP_OUTLINE = (61,40,37,0,267,270,291,321,405,314,17,84,181,91)
EYE_OUTLINES = ((33,160,158,133,153,144), (362,385,387,263,373,380))


def effect_mask(points: list[tuple[float, float]], size: tuple[int, int], effect: dict) -> Image.Image:
    """Restrict pigment to landmark-relative cosmetic zones; protect features."""
    role = str(effect.get('role') or '')
    region = str(effect.get('region') or '')
    card = str(effect.get('card_id') or '')
    mask = Image.new('L', size, 0)
    draw = ImageDraw.Draw(mask)
    xy = lambda indices: [tuple(map(int, points[i])) for i in indices]
    face_width = abs(points[454][0] - points[234][0])
    stroke = max(2, round(face_width * .007))
    if region == 'lip':
        if 'gloss' in card or role == 'finish':
            width = abs(points[291][0]-points[61][0])
            center = points[17][0]
            upper = points[14][1]
            lower = points[17][1]
            draw.ellipse((center-width*.25,upper+(lower-upper)*.25,
                          center+width*.25,lower-(lower-upper)*.05),fill=255)
        else:
            draw.polygon(xy(LIP_OUTLINE), fill=255)
            # The mouth opening is never repainted.
            draw.polygon(xy((78,95,88,178,87,14,317,402,318,324,308,191,80,81,82,13)), fill=0)
    elif region == 'eye':
        for outer, inner, top, bottom, lid in ((33,133,159,145,(33,160,158,133)),
                                               (362,263,386,374,(362,385,387,263))):
            x0, x1 = sorted((points[outer][0], points[inner][0]))
            width = x1-x0
            if role == 'line' or 'liner' in card or 'lash-root' in card:
                draw.line(xy(lid), fill=255, width=stroke)
            elif 'mascara' in card or 'lash' in card or '睫毛' in str(effect.get('goal')):
                draw.line(xy(lid), fill=255, width=stroke + 1)
                for i in range(3):
                    x = x0 + width * (.62 + i*.12)
                    y = points[top][1] + width*.015
                    draw.line((x,y,x+width*.025,y-width*.06),fill=255,width=max(1,stroke//2))
            else:
                y = points[top][1]
                height = width * (.25 if role == 'depth' else .32)
                draw.ellipse((x0-width*.03, y-height, x1+width*.03,
                              y+width*.035), fill=255)
    elif region == 'cheek' and role in ('blush', 'contour', 'highlight'):
        for eye, cheek, mouth in ((33,117,61), (263,346,291)):
            cx = (points[eye][0] + points[cheek][0]) / 2
            cy = (points[eye][1] + points[mouth][1]) / 2
            width = face_width * (.18 if role == 'blush' else .13)
            height = face_width * (.10 if role == 'blush' else .07)
            if role == 'contour': cy += height*.9
            if role == 'highlight': cy -= height*.7
            draw.ellipse((cx-width, cy-height, cx+width, cy+height), fill=255)
    elif role == 'highlight' and ('nose' in card or region == 'complexion'):
        x = points[6][0]; y0 = points[6][1]; y1 = points[1][1]
        draw.ellipse((x-face_width*.035,y0,x+face_width*.035,y1),fill=255)
    elif role == 'contour' and ('nose' in card or region == 'complexion'):
        for x in (points[98][0], points[327][0]):
            draw.ellipse((x-face_width*.025,points[6][1],x+face_width*.025,points[4][1]),fill=255)
    elif region == 'complexion':
        draw.polygon(xy(FACE_OUTLINE), fill=255)
    elif region == 'eyebrow':
        for indices in ((70,63,105,66,107,55), (300,293,334,296,336,285)):
            draw.polygon(xy(indices),fill=255)
    if region != 'lip':
        draw.polygon(xy(LIP_OUTLINE),fill=0)
    if region != 'eye' or not (role in ('line', 'finish') or
                               'liner' in card or 'lash' in card):
        for outline in EYE_OUTLINES:
            draw.polygon(xy(outline),fill=0)
    if region in ('complexion','cheek','eye','eyebrow'):
        face_area = Image.new('L',size,0)
        ImageDraw.Draw(face_area).polygon(xy(FACE_OUTLINE),fill=255)
        mask = ImageChops.multiply(mask,face_area)
    drawing = effect.get('drawing')
    if isinstance(drawing, dict) and drawing.get('anchor'):
        anchor = drawing['anchor']
        pair = {'left_eye': 'right_eye', 'right_eye': 'left_eye',
                'left_cheek': 'right_cheek', 'right_cheek': 'left_cheek',
                'left_brow': 'right_brow', 'right_brow': 'left_brow'}
        anchors = [anchor]
        if drawing.get('side') == 'both' and anchor in pair:
            anchors.append(pair[anchor])
        elif drawing.get('side') == 'left' and anchor.startswith('right_'):
            anchors = [pair[anchor]]
        elif drawing.get('side') == 'right' and anchor.startswith('left_'):
            anchors = [pair[anchor]]
        anchor_mask = Image.new('L', size, 0)
        anchor_draw = ImageDraw.Draw(anchor_mask)
        for name in anchors:
            indices = PART_POINTS.get('lip' if name == 'lips' else name)
            if not indices:
                continue
            ax = sum(points[i][0] for i in indices) / len(indices)
            ay = sum(points[i][1] for i in indices) / len(indices)
            if name.endswith('_eye') and role not in ('line', 'finish') and not any(
                    word in card for word in ('liner', 'lash', 'mascara')):
                # Eye landmarks center on the eye opening; shadow belongs above the lid.
                ay -= face_width * .04
            # Mirror horizontal offsets for the other half of a bilateral action.
            sign = -1 if name.startswith('left_') else 1
            cx = ax + float(drawing.get('offset_x', 0)) * face_width * sign
            cy = ay + float(drawing.get('offset_y', 0)) * face_width
            rx = min(face_width*.4, max(2, float(drawing.get('radius_x', .12))*face_width))
            ry = min(face_width*.3, max(2, float(drawing.get('radius_y', .07))*face_width))
            anchor_draw.ellipse((cx-rx, cy-ry, cx+rx, cy+ry), fill=255)
        clipped = ImageChops.multiply(mask, anchor_mask)
        if not clipped.getbbox():
            raise ValueError('绘图参数无法落在该部位的允许区域')
        mask = clipped
    # Feather within the allowed shape; never leak pigment into protected pixels.
    soft = mask.filter(ImageFilter.GaussianBlur(max(1, round(face_width*.012))))
    return ImageChops.multiply(soft, mask)


def render_effects(base: Image.Image, points: list[tuple[float, float]],
                   effects: list[dict], *, skip_invalid: bool = False) -> tuple[Image.Image, Image.Image]:
    result = base.convert('RGB').copy()
    latest = Image.new('L', base.size, 0)
    for effect in effects:
        try:
            mask = effect_mask(points, base.size, effect)
        except ValueError:
            if skip_invalid:
                continue
            raise
        if not mask.getbbox():
            raise ValueError(f"无法定位步骤妆效区域：{effect.get('region')}")
        color = effect.get('color_hex')
        if not isinstance(color,str) or len(color) != 7 or not color.startswith('#'):
            latest = mask
            continue
        rgb = tuple(int(color[i:i+2],16) for i in (1,3,5))
        region = effect.get('region')
        role = effect.get('role')
        strength = .28 if region == 'lip' else .20 if region == 'eye' else .12 if region == 'cheek' else .04
        if role in ('line','depth','contour'): strength = min(.46,strength*1.7)
        if role == 'highlight':
            rgb = tuple(min(255,int(channel*.4+255*.6)) for channel in rgb)
            strength = .14
        if 'gloss' in str(effect.get('card_id') or ''):
            strength = .24
        if effect.get('intensity') == 'light': strength *= .65
        drawing = effect.get('drawing') or {}
        strength *= {'light': .65, 'medium': 1, 'strong': 1.25}.get(drawing.get('strength'), 1)
        strength *= min(1.3, 1 + .1 * (int(drawing.get('layers', 1)) - 1))
        surrounding = effect.get('reference_skin_hex')
        if (region in ('eye','cheek') and isinstance(surrounding,str)
                and len(surrounding)==7 and surrounding.startswith('#')):
            skin = tuple(int(surrounding[i:i+2],16) for i in (1,3,5))
            relative_strength = min(1,strength*(8 if region == 'cheek' else 3.2))
            delta = [round((rgb[i]-skin[i])*relative_strength) for i in range(3)]
            shifted = Image.merge('RGB',tuple(channel.point(
                lambda value, d=d: max(0,min(255,value+d)))
                for channel,d in zip(result.split(),delta)))
            result = Image.composite(shifted,result,mask)
        else:
            alpha = mask.point(lambda value: round(value*strength))
            result = Image.composite(Image.new('RGB',base.size,rgb),result,alpha)
        latest = mask
    return result, latest


def _crop_mask(mask: Image.Image, box: tuple[int,int,int,int]) -> Image.Image:
    return mask.crop(box).resize((1024,1024),Image.Resampling.LANCZOS)


def _annotation_marker(points: list[tuple[float,float]],
                       box: tuple[int,int,int,int], part: str) -> tuple[int,int] | None:
    if part != 'face':
        return None
    x,y = points[117]  # Mid-cheek landmark, inside the base-makeup zone.
    scale = 1024/(box[2]-box[0])
    return int((x-box[0])*scale),int((y-box[1])*scale)


def baseline_step(base_path: Path, part: str, output_path: Path,
                  previous_effects: list[dict] | None = None,
                  current_effect: dict | None = None) -> dict:
    with Image.open(base_path) as source:
        face = source.convert("RGB")
    points = landmarks(face)
    box = crop_box(points, part, face.size)
    previous = previous_effects or []
    rendered = render_effects(face, points, previous, skip_invalid=True)[0] if previous else face
    local = rendered.crop(box).resize((1024, 1024), Image.Resampling.LANCZOS)
    if current_effect:
        mark_operation(local, _crop_mask(effect_mask(points, face.size, current_effect), box),
                       _annotation_marker(points,box,part),
                       (current_effect.get('drawing') or {}).get('direction'),
                       (current_effect.get('drawing') or {}).get('side')).save(output_path)
    else:
        local.save(output_path)
    return {"box": box, "part": part}


def step_worker(base_path: str, part: str, effects: list[dict], staged_path: str, report) -> dict:
    report(5, "locating", "正在定位面部区域")
    with Image.open(base_path) as src:
        face = src.convert("RGB")
    points = landmarks(face)
    box = crop_box(points, part, face.size)
    report(25, "editing", "正在叠加局部妆效")
    previous = render_effects(face, points, effects[:-1], skip_invalid=True)[0]
    combined, latest = render_effects(previous, points, effects[-1:])
    local = combined.crop(box).resize((1024,1024),Image.Resampling.LANCZOS)
    # The teaching mark is drawn after pigment validation and follows the actual mask.
    mark_operation(local, _crop_mask(latest, box),
                   _annotation_marker(points,box,part),
                   ((effects[-1].get('drawing') or {}).get('direction') if effects else None),
                   ((effects[-1].get('drawing') or {}).get('side') if effects else None)).save(staged_path)
    return {"staged_path": staged_path, "box": box, "part": part,
            "model": "local-pigment-compositor", "prompt_version": STEP_RENDER_VERSION}
