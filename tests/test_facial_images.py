from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import math

from PIL import Image, ImageDraw

from makeup_translator import facial_images as subject


def test_edit_uses_sunburst_and_explicit_size_and_mask(tmp_path: Path, monkeypatch):
    calls = []
    source = tmp_path / "face.png"
    mask = tmp_path / "mask.png"
    Image.new("RGB", (32, 32)).save(source)
    Image.new("RGBA", (32, 32)).save(mask)
    output = BytesIO()
    Image.new("RGB", (32, 32)).save(output, format="PNG")

    def edit(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(b64_json=__import__("base64").b64encode(output.getvalue()).decode())])

    monkeypatch.setattr(subject, "image_client", lambda: SimpleNamespace(images=SimpleNamespace(edit=edit)))
    monkeypatch.delenv("IMAGE_MODEL", raising=False)
    assert subject.edit_image(source, "change", mask=mask) == output.getvalue()
    assert calls[0]["model"] == "gpt-image-2.5-sunburst"
    assert calls[0]["size"] == "1024x1024"
    assert calls[0]["quality"] == "medium"
    assert calls[0]["mask"].name.endswith("mask.png")


def test_composite_preserves_every_pixel_outside_allowed_area():
    original = Image.new("RGB", (64, 64), (20, 30, 40))
    edited = Image.new("RGB", (64, 64), (200, 110, 80))
    allowed = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(allowed).ellipse((15, 20, 45, 50), fill=255)
    combined = subject.composite_allowed(edited, original, allowed)
    for y in range(64):
        for x in range(64):
            assert combined.getpixel((x, y)) == (edited if allowed.getpixel((x, y)) else original).getpixel((x, y))


def test_local_lip_effect_preserves_unmasked_pixels_and_mouth_opening():
    points = [(32.0, 32.0)] * 468
    outer = {61:(20,44),40:(23,41),37:(28,40),0:(32,39),267:(36,40),
             270:(41,41),291:(44,44),321:(41,47),405:(36,49),314:(32,50),
             17:(29,50),84:(25,49),181:(22,47),91:(20,45)}
    inner = {78:(27,45),95:(29,44),88:(31,44),178:(32,44),87:(33,44),
             14:(34,45),317:(35,45),402:(36,45),318:(35,46),324:(34,46),
             308:(33,46),191:(32,46),80:(31,46),81:(30,46),82:(29,46),13:(28,45)}
    for index, point in {**outer, **inner}.items():
        points[index] = point
    base = Image.new('RGB', (64,64), (180,145,130))
    effect = {'region':'lip','role':'lip','color_hex':'#A85065','intensity':'medium'}
    mask = subject.effect_mask(points,base.size,effect)
    rendered,_ = subject.render_effects(base,points,[effect])
    assert rendered.getpixel((32,41)) != base.getpixel((32,41))
    assert rendered.getpixel((32,45)) == base.getpixel((32,45))
    assert all(rendered.getpixel((x,y)) == base.getpixel((x,y))
               for y in range(64) for x in range(64) if mask.getpixel((x,y)) == 0)


def test_blush_mask_never_reaches_hair_outside_face_outline():
    points = [(32.0,32.0)]*468
    for order,index in enumerate(subject.FACE_OUTLINE):
        angle=2*math.pi*(order-8)/len(subject.FACE_OUTLINE)
        points[index]=(32+20*math.cos(angle),32+24*math.sin(angle))
    for index in subject.PART_POINTS['left_cheek']:
        points[index]=(14.0,36.0)
    for index in subject.PART_POINTS['right_cheek']:
        points[index]=(50.0,36.0)
    mask=subject.effect_mask(points,(64,64),{'region':'cheek','role':'blush'})
    assert mask.getbbox()
    face=Image.new('L',(64,64),0)
    ImageDraw.Draw(face).polygon([points[i] for i in subject.FACE_OUTLINE],fill=255)
    assert all(mask.getpixel((x,y))==0 for y in range(64) for x in range(64)
               if face.getpixel((x,y))==0)


def test_lashline_mask_survives_eye_opening_protection():
    points = [(50.0, 50.0)] * 468
    for order, index in enumerate(subject.FACE_OUTLINE):
        angle = 2 * math.pi * order / len(subject.FACE_OUTLINE)
        points[index] = (50 + 42 * math.sin(angle), 50 - 45 * math.cos(angle))
    points[234], points[454] = (8.0, 50.0), (92.0, 50.0)
    for index, point in {33:(20,40),160:(25,37),158:(34,37),133:(40,40),
                         153:(34,44),144:(25,44),159:(30,37),145:(30,44)}.items():
        points[index] = point
    effect = {'region':'eye','role':'line','card_id':'eye-lashline',
              'drawing':{'anchor':'left_eye','side':'left','offset_x':0,
                         'offset_y':0,'radius_x':.14,'radius_y':.06}}
    assert subject.effect_mask(points,(100,100),effect).getbbox()


def test_invalid_previous_effect_does_not_block_current_step(monkeypatch):
    base = Image.new('RGB',(20,20),'#dddddd')
    def mask(_points, size, effect):
        if effect['id'] == 'invalid':
            raise ValueError('绘图参数无法落在该部位的允许区域')
        result = Image.new('L',size,0)
        ImageDraw.Draw(result).rectangle((3,3,8,8),fill=255)
        return result
    monkeypatch.setattr(subject,'effect_mask',mask)
    invalid = {'id':'invalid','region':'lip','color_hex':'#aa0000'}
    valid = {'id':'valid','region':'lip','color_hex':'#aa0000'}
    try:
        subject.render_effects(base, [], [invalid,valid])
    except ValueError:
        pass
    else:
        raise AssertionError('严格渲染应报告无效步骤')
    rendered, current_mask = subject.render_effects(base, [], [invalid,valid],skip_invalid=True)
    assert current_mask.getbbox()
    assert rendered.getpixel((5,5)) != base.getpixel((5,5))


def test_bilateral_operation_arrows_stay_on_each_eye():
    base = Image.new('RGB',(300,120),'#eeeeee')
    mask = Image.new('L',base.size,0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((30,35,105,75),fill=255)
    draw.ellipse((195,35,270,75),fill=255)
    marked = subject.mark_operation(base,mask,direction='upward',side='both')
    assert marked.getpixel((67,55)) != base.getpixel((67,55))
    assert marked.getpixel((232,55)) != base.getpixel((232,55))
    assert marked.getpixel((150,55)) == base.getpixel((150,55))
