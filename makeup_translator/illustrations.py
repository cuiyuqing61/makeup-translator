"""Reviewed, deterministic teaching diagrams for the local technique cards.

Coordinates use the same 320 x 210 canvas as the web SVG templates. The
geometry is copied into every tutorial result so later edits to this registry
cannot silently change historical instructions.
"""
from __future__ import annotations

from .models import PipelineResult, StepIllustration, TechniqueCard, TutorialStep


# Geometry: (template, ellipses [cx, cy, rx, ry, rotation], arrows [x1, y1, x2, y2]).
# A single enlarged eye/brow/lip represents one side; the UI says to repeat on
# the other side when appropriate. These shapes are teaching diagrams, not a
# projection onto the user's or reference model's face.
GEOMETRY = {
    'face-center': ('face', [(128, 122, 22, 25, -16), (192, 122, 22, 25, 16)], [(145, 122, 103, 126), (175, 122, 217, 126)]),
    'face-tzone': ('face', [(160, 74, 24, 11, 0), (160, 119, 9, 24, 0)], []),
    'face-tzone-chin': ('face', [(160, 74, 24, 11, 0), (160, 119, 9, 24, 0), (160, 173, 17, 8, 0)], []),
    'face-under-eye': ('face', [(128, 106, 19, 7, -12), (192, 106, 19, 7, 12)], []),
    'face-spot': ('face', [(128, 130, 9, 9, 0), (193, 137, 8, 8, 0)], []),
    'face-soften': ('face', [(122, 126, 14, 18, -12), (198, 126, 14, 18, 12)], []),
    'face-nose': ('face', [(151, 120, 6, 19, -8), (169, 120, 6, 19, 8)], [(151, 113, 148, 133), (169, 113, 172, 133)]),
    'face-edge': ('face', [(112, 166, 23, 7, 25), (208, 166, 23, 7, -25)], [(145, 162, 104, 170), (175, 162, 216, 170)]),
    'brow-all': ('brow', [(153, 66, 76, 10, -7)], [(95, 70, 214, 60)]),
    'brow-tail': ('brow', [(204, 60, 28, 9, -12)], [(190, 61, 231, 54)]),
    'brow-front': ('brow', [(106, 69, 21, 11, -7)], [(104, 77, 109, 59)]),
    'brow-gaps': ('brow', [(153, 66, 39, 7, -8)], [(130, 69, 154, 65), (161, 65, 184, 61)]),
    'eye-lid': ('eye', [(157, 103, 77, 23, 0)], [(111, 112, 204, 109)]),
    'eye-gradient': ('eye', [(118, 107, 37, 15, 0), (160, 103, 34, 19, 0), (207, 107, 25, 13, -10)], [(118, 111, 206, 107)]),
    'eye-tail': ('eye', [(214, 111, 35, 10, -13)], [(194, 118, 245, 98)]),
    'eye-center': ('eye', [(161, 99, 22, 13, 0)], []),
    'eye-line': ('eye', [(179, 117, 58, 5, -6)], [(183, 118, 243, 107)]),
    'eye-lashes': ('eye', [(160, 124, 77, 7, 0)], [(150, 129, 150, 109), (185, 130, 190, 107)]),
    'cheek-lift': ('face', [(107, 124, 27, 13, -26), (213, 124, 27, 13, 26)], [(120, 134, 86, 105), (200, 134, 234, 105)]),
    'cheek-center': ('face', [(121, 131, 20, 16, 0), (199, 131, 20, 16, 0)], [(122, 134, 105, 129), (198, 134, 215, 129)]),
    'cheek-contour': ('face', [(104, 149, 28, 10, 24), (216, 149, 28, 10, -24)], [(88, 145, 121, 159), (232, 145, 199, 159)]),
    'cheek-highlight': ('face', [(114, 112, 17, 6, -22), (206, 112, 17, 6, 22)], []),
    'cheek-outer': ('face', [(106, 130, 24, 15, -12), (214, 130, 24, 15, 12)], [(90, 130, 120, 134), (230, 130, 200, 134)]),
    'cheek-edge': ('face', [(105, 135, 32, 18, -18), (215, 135, 32, 18, 18)], [(98, 146, 119, 133), (222, 146, 201, 133)]),
    'lip-all': ('lip', [(160, 112, 62, 20, 0)], [(155, 111, 210, 113)]),
    'lip-inner': ('lip', [(160, 115, 34, 14, 0)], [(155, 115, 120, 116), (165, 115, 200, 116)]),
    'lip-center': ('lip', [(160, 118, 27, 9, 0)], []),
    'lip-edge': ('lip', [(160, 105, 75, 10, 0)], [(158, 106, 92, 111), (162, 106, 228, 111)]),
}


# Card ID -> (geometry, placement, direction, amount). Geometry is an original
# schematic drawn by this project; no third-party tutorial images are copied.
CARD_VISUALS = {
    'nose-soft-contour': ('face-nose', '鼻梁两侧的自然阴影处', '少量落色后晕开边缘', '两侧各少量'),
    'complexion-sponge-foundation': ('face-center', '面中及两颊', '由面中向外滚动轻拍', '少量液体粉底'),
    'complexion-brush-foundation': ('face-center', '面中及两颊', '短笔触向下刷匀', '先取薄层'),
    'complexion-spot-conceal': ('face-spot', '实际需要遮盖的局部；圆点仅作示例', '轻拍外缘', '点状少量'),
    'complexion-under-eye-conceal': ('face-under-eye', '眼下暗沉处', '向外轻拍', '两侧各少量'),
    'complexion-tzone-setting': ('face-tzone-chin', '额头、鼻部和下巴', '轻扫并带走余粉', '薄薄一层'),
    'eyebrow-fill-gaps': ('brow-gaps', '眉毛的可见空隙', '顺毛流画短线', '每次几笔'),
    'eyebrow-set-shape': ('brow-all', '整条眉毛', '顺毛流轻刷', '一层薄胶'),
    'eyebrow-soft-front': ('brow-front', '眉头过重处', '向眉中轻扫', '只用干净刷'),
    'eye-neutral-shadow': ('eye-lid', '可见上眼皮', '由眼中向外铺色', '浅色一层'),
    'eye-primer': ('eye-lid', '上眼皮', '轻薄推匀', '少量眼部打底'),
    'eye-crease-definition': ('eye-tail', '眼窝外侧', '向内扫软交界', '中间色少量'),
    'eye-blend-edges': ('eye-gradient', '眼影深浅交界', '小幅来回晕染', '不另取颜色'),
    'eye-center-highlight': ('eye-center', '上眼皮中央', '轻按中心并拍开边缘', '浅色少量'),
    'eye-lashline': ('eye-line', '上睫毛根部', '逐段连接', '细线一条'),
    'eye-short-wing': ('eye-tail', '上睫毛根部外侧', '沿眼尾带出短尾', '从很短开始'),
    'eye-mascara-separate': ('eye-lashes', '上睫毛根部至尖端', '由根部向上刷', '薄薄一层'),
    'eye-mgp-soft-gradient': ('eye-gradient', '眼窝与眼睑局部', '浅底色向局部深色过渡', '少量分次'),
    'cheek-soft-blush': ('cheek-center', '面颊两侧中心', '由落点向外晕开', '两侧各薄层'),
    'cheek-lifted-blush': ('cheek-lift', '颧骨外侧', '向发际斜上晕开', '先薄扫一层'),
    'cheek-cream-blush': ('cheek-center', '面颊两侧相应位置', '由中心轻拍向外', '两侧等量少量'),
    'cheek-outer-contour': ('cheek-contour', '颧骨下方外侧', '由外侧向面中渐淡', '先少量'),
    'cheek-cheekbone-highlight': ('cheek-highlight', '颧骨高点', '轻扫并检查反光', '薄薄一层'),
    'cheek-mgp-diffused-blush': ('cheek-outer', '面颊外侧至面中', '斜向面中渐淡', '外侧少量'),
    'lip-balm-prep': ('lip-all', '上下唇', '薄涂并等待', '一层润唇产品'),
    'lip-natural-line': ('lip-edge', '原有唇缘', '从唇峰向两侧画短线', '细线少量'),
    'lip-even-lipstick': ('lip-all', '上下唇', '沿轮廓填色', '先涂薄层'),
    'lip-center-gloss': ('lip-center', '上下唇中央', '向外轻带余量', '各点少量'),
    'lip-mgp-inner-gradient': ('lip-inner', '上下唇内侧', '由内向外拍柔交界', '底色薄涂，内侧叠一层'),
}

FALLBACK_TEMPLATES = {'complexion': 'face', 'cheek': 'face', 'eyebrow': 'brow', 'eye': 'eye', 'lip': 'lip'}


def illustrate_step(step: TutorialStep, *, mode: str = 'standard') -> StepIllustration:
    visual = CARD_VISUALS.get(step.technique_card_id)
    if visual:
        geometry_name, placement, direction, amount = visual
        template, areas, arrows = GEOMETRY[geometry_name]
    else:
        template = FALLBACK_TEMPLATES[step.region.value]
        placement, direction, amount = '此部位通用示意', '具体位置和动作请以文字步骤为准', '按文字步骤少量操作'
        areas, arrows = [], []
    return StepIllustration(
        step_order=step.order,
        source_card_id=step.technique_card_id,
        template=template,
        placement=step.application.placement if step.application else placement,
        direction=step.application.direction if step.application else direction,
        amount=('少量操作，按简化文字步骤执行' if mode == 'simplified' else
                step.application.amount if step.application else amount),
        areas=[{'cx': x, 'cy': y, 'rx': rx, 'ry': ry, 'rotation': angle}
               for x, y, rx, ry, angle in areas],
        arrows=[{'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2}
                for x1, y1, x2, y2 in arrows],
        mode=mode,
    )


def attach_illustrations(result: PipelineResult, *, replace: bool = False) -> PipelineResult:
    """Fill old results without changing already snapshotted illustrations."""
    existing = {item.step_order: item for item in result.step_illustrations}
    result.step_illustrations = [
        illustrate_step(step) if replace or step.order not in existing
        or existing[step.order].source_card_id != step.technique_card_id
        else existing[step.order]
        for step in result.tutorial.steps
    ]
    return result
