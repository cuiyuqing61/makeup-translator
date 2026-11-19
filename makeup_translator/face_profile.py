"""Small, user-correctable face observations used by makeup techniques."""
from __future__ import annotations

from typing import Any


ANALYSIS_PROMPT_VERSION = "face-features-v1"

# Values describe visible appearance, not diagnoses or permanent skin properties.
FEATURES: dict[str, dict[str, Any]] = {
    "eye_shape": {"label": "眼睛轮廓", "group": "eyes", "options": {
        "almond": "杏眼", "round": "圆眼", "elongated": "细长眼",
        "peach_blossom": "桃花眼", "phoenix": "丹凤眼", "unknown": "无法判断"}},
    "eyelid": {"label": "眼皮状态", "group": "eyes", "options": {
        "monolid": "单眼皮", "inner_double": "内双", "double": "双眼皮", "unknown": "无法判断"}},
    "eye_tilt": {"label": "眼尾方向", "group": "eyes", "options": {
        "level": "平缓", "upturned": "略上扬", "downturned": "略下垂", "unknown": "无法判断"}},
    "brow_shape": {"label": "眉形", "group": "brows", "options": {
        "straight": "平直", "arched": "弯眉", "angled": "有眉峰", "unknown": "无法判断"}},
    "brow_density": {"label": "眉毛疏密", "group": "brows", "options": {
        "sparse": "有空隙", "medium": "适中", "dense": "较浓密", "unknown": "无法判断"}},
    "nose_wings": {"label": "鼻翼宽度", "group": "nose_lips", "options": {
        "narrow": "偏窄", "medium": "适中", "broad": "偏宽", "unknown": "无法判断"}},
    "lip_shape": {"label": "唇部轮廓", "group": "nose_lips", "options": {
        "thin": "偏薄", "balanced": "适中", "full": "偏丰满", "unknown": "无法判断"}},
    "under_eye_shadow": {"label": "眼下暗沉", "group": "local", "options": {
        "visible": "可见", "not_visible": "未见明显暗沉", "unknown": "无法判断"}},
    "local_redness": {"label": "局部泛红", "group": "local", "options": {
        "visible": "可见", "not_visible": "未见明显泛红", "unknown": "无法判断"}},
    "cheek_shadow": {"label": "面颊阴影", "group": "local", "options": {
        "visible": "可见阴影", "not_visible": "未见明显阴影", "unknown": "无法判断"}},
}

GROUPS = {
    "eyes": {"label": "眼部", "technique": "按睁眼可见范围确定眼影和眼线位置"},
    "brows": {"label": "眉部", "technique": "按原有眉形和疏密决定补色范围"},
    "nose_lips": {"label": "鼻唇", "technique": "保留原有轮廓，只在目标妆容需要时修饰"},
    "local": {"label": "局部肤色", "technique": "只在可见位置少量修饰，避免整脸加厚"},
}


def schema() -> dict[str, Any]:
    return {"groups": GROUPS, "features": FEATURES}


def analysis_prompt() -> str:
    choices = "; ".join(f"{key}: {', '.join(item['options'])}" for key, item in FEATURES.items())
    return (
        "只分析照片中可见、会影响化妆步骤的特点，不推断肤质、敏感程度、健康、年龄或黑眼圈成因。"
        "返回 JSON 对象，包含 features；每个字段都返回 {value, certainty, evidence}。"
        "value 只能取以下英文选项之一：" + choices + "。"
        "certainty 只能是 clear 或 uncertain；受光线、角度、遮挡影响时用 uncertain，"
        "无法判断时 value 用 unknown。evidence 用简短中文说明照片中的依据，不写大段建议。"
        "面颊阴影只是可见明暗，不判断是否真实凹陷。"
        "若多人、脸部明显遮挡或严重侧脸，另返回 unsuitable_reason 说明重新上传原因。"
    )


def normalize_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("unsuitable_reason"):
        return {"unsuitable_reason": str(payload["unsuitable_reason"])[:200]}
    raw = payload.get("features") or {}
    normalized = {}
    for key, definition in FEATURES.items():
        item = raw.get(key) if isinstance(raw, dict) else None
        item = item if isinstance(item, dict) else {}
        value = str(item.get("value") or "unknown")
        if value not in definition["options"]:
            value = "unknown"
        certainty = "clear" if item.get("certainty") == "clear" and value != "unknown" else "uncertain"
        normalized[key] = {"value": value, "certainty": certainty,
                           "evidence": str(item.get("evidence") or "")[:160]}
    return {"features": normalized, "prompt_version": ANALYSIS_PROMPT_VERSION}


def effective_features(observations: dict[str, Any], choices: dict[str, str],
                       choice_version: str | None, photo_version: str | None) -> dict[str, str]:
    result = {}
    observed = observations.get("features") or {}
    for key, definition in FEATURES.items():
        item = observed.get(key) or {}
        value = item.get("value")
        if item.get("certainty") == "clear" and value in definition["options"] and value != "unknown":
            result[key] = value
    if photo_version and choice_version == photo_version:
        for key, value in choices.items():
            if key in FEATURES and value in FEATURES[key]["options"]:
                if value == "unknown":
                    result.pop(key, None)
                else:
                    result[key] = value
    return result


def feature_terms(features: dict[str, str]) -> list[str]:
    terms = []
    for key, value in features.items():
        if key in FEATURES and value in FEATURES[key]["options"] and value != "unknown":
            terms.append(FEATURES[key]["options"][value])
    return terms


CARD_FEATURE_MATCHES: dict[str, dict[str, set[str]]] = {
    "eye-neutral-shadow": {"eyelid": {"monolid", "inner_double"}},
    "eye-mgp-soft-gradient": {"eyelid": {"monolid", "inner_double"}},
    "eye-short-wing": {"eye_tilt": {"downturned", "upturned"}},
    "eyebrow-fill-gaps": {"brow_density": {"sparse"}},
    "eyebrow-set-shape": {"brow_density": {"dense"}},
    "complexion-under-eye-conceal": {"under_eye_shadow": {"visible"}},
    "complexion-spot-conceal": {"local_redness": {"visible"}},
    "lip-natural-line": {"lip_shape": {"thin"}},
    "nose-soft-contour": {"nose_wings": {"broad"}},
}


def card_feature_score(card_id: str, features: dict[str, str]) -> int:
    return 5 * sum(features.get(key) in values
                   for key, values in CARD_FEATURE_MATCHES.get(card_id, {}).items())


def instruction_note(card_id: str, features: dict[str, str]) -> str | None:
    notes = []
    if card_id in {"eye-neutral-shadow", "eye-crease-definition", "eye-mgp-soft-gradient"}:
        if features.get("eyelid") in {"monolid", "inner_double"}:
            notes.append("睁眼确认能看见的范围，再决定铺色高度。")
        if features.get("eye_shape") in {"almond", "round", "elongated", "peach_blossom", "phoenix"}:
            notes.append("顺着你原有的眼睛轮廓晕开，不照搬参考图的眼形。")
    if card_id in {"eye-short-wing", "eye-lashline"}:
        if features.get("eye_tilt") == "downturned":
            notes.append("先睁眼标出可见眼尾，眼线不沿下垂方向继续加长。")
        elif features.get("eye_tilt") == "upturned":
            notes.append("先顺着原有上扬眼尾画短线，避免额外拉长。")
    if card_id in {"eyebrow-fill-gaps", "eyebrow-set-shape"}:
        if features.get("brow_shape") in {"straight", "arched", "angled"}:
            notes.append("以原有眉形为边界，不另画一条新的眉峰。")
        if card_id == "eyebrow-fill-gaps" and features.get("brow_density") == "sparse":
            notes.append("只在原眉毛的空隙处用短笔触补色。")
        if card_id == "eyebrow-fill-gaps" and features.get("brow_density") == "dense":
            notes.append("若原眉无明显空隙，跳过补色，仅用眉刷梳理。")
    if card_id == "complexion-under-eye-conceal" and features.get("under_eye_shadow") == "visible":
        notes.append("仅在眼下可见暗沉处点少量遮瑕，边缘向外轻拍。")
    if card_id == "complexion-spot-conceal" and features.get("local_redness") == "visible":
        notes.append("只在可见泛红处薄薄点拍，不扩大到整张脸。")
    if card_id in {"cheek-soft-blush", "cheek-lifted-blush", "cheek-cream-blush"} and features.get("cheek_shadow") == "visible":
        notes.append("先观察两颊可见阴影，再少量上色并对照两侧效果。")
    if card_id == "lip-natural-line" and features.get("lip_shape") in {"thin", "full"}:
        notes.append("沿真实唇缘描线，避免明显外扩。")
    if card_id == "nose-soft-contour" and features.get("nose_wings") == "broad":
        notes.append("在鼻翼外侧用极少量阴影，边缘晕开，不改变真实鼻形。")
    return " ".join(notes) or None
