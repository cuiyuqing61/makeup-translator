from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from openai import OpenAI
from PIL import Image

from .models import (
    Confidence,
    ImageMetadata,
    LookAnalysis,
    MakeupFeature,
    OwnedProduct,
    Region,
    TechniqueCard,
    TutorialPlan,
    UserProfile,
)
from .prompts import (
    ANALYSIS_SYSTEM_PROMPT,
    ANALYSIS_USER_PROMPT,
    TUTORIAL_SYSTEM_PROMPT,
    TUTORIAL_USER_PROMPT,
)


def image_metadata(path: Path) -> ImageMetadata:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("image exceeds the 20 MB API input limit")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with Image.open(path) as image:
        width, height = image.size
        fmt = image.format or path.suffix.lstrip(".").upper()
    return ImageMetadata(
        path=str(path), width=width, height=height, format=fmt, sha256=digest
    )


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response did not contain a JSON object")
    return json.loads(cleaned[start : end + 1])


def _canonical_label(value: Any) -> str:
    return str(value).strip().lower().replace("_", " ").replace("-", " ")


def normalize_analysis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Repair common free-text variants while keeping an auditable note."""
    intensity_aliases = {
        "light": "light",
        "low": "light",
        "subtle": "light",
        "soft": "light",
        "sheer": "light",
        "very light": "light",
        "轻": "light",
        "弱": "light",
        "medium": "medium",
        "moderate": "medium",
        "light to medium": "medium",
        "medium to light": "medium",
        "中": "medium",
        "中等": "medium",
        "适中": "medium",
        "strong": "strong",
        "high": "strong",
        "intense": "strong",
        "bold": "strong",
        "heavy": "strong",
        "medium to strong": "strong",
        "浓": "strong",
        "强": "strong",
    }
    confidence_aliases = {
        "low": "low",
        "low confidence": "low",
        "较低": "low",
        "低": "low",
        "medium": "medium",
        "moderate": "medium",
        "medium confidence": "medium",
        "中": "medium",
        "中等": "medium",
        "high": "high",
        "high confidence": "high",
        "较高": "high",
        "高": "high",
    }
    region_aliases = {
        "complexion": "complexion",
        "base": "complexion",
        "face": "complexion",
        "底妆": "complexion",
        "eyebrow": "eyebrow",
        "eyebrows": "eyebrow",
        "brow": "eyebrow",
        "brows": "eyebrow",
        "眉": "eyebrow",
        "eye": "eye",
        "eyes": "eye",
        "眼": "eye",
        "眼妆": "eye",
        "cheek": "cheek",
        "cheeks": "cheek",
        "blush": "cheek",
        "腮红": "cheek",
        "lip": "lip",
        "lips": "lip",
        "唇": "lip",
        "唇妆": "lip",
    }
    notes = list(payload.get("normalization_notes") or [])
    for index, feature in enumerate(payload.get("features") or []):
        for field, aliases in (
            ("intensity", intensity_aliases),
            ("confidence", confidence_aliases),
            ("region", region_aliases),
        ):
            raw = feature.get(field)
            canonical = aliases.get(_canonical_label(raw))
            if canonical is not None and canonical != raw:
                feature[field] = canonical
                notes.append(
                    f"features[{index}].{field}: {raw!r} normalized to {canonical!r}"
                )
    payload["normalization_notes"] = notes
    return payload


class QwenVisionAnalyzer:
    def __init__(self) -> None:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for provider=qwen")
        self.model = os.getenv("QWEN_VISION_MODEL", "qwen3-vl-plus")
        self.client = OpenAI(
            max_retries=0,
            timeout=60,
            api_key=api_key,
            base_url=os.getenv(
                "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
        )

    def analyze(self, path: Path) -> LookAnalysis:
        metadata = image_metadata(path)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        options = {} if self.model.lower().startswith(('gpt-5', 'gpt-6', 'o1', 'o3', 'o4')) else {'temperature': 0.1}
        response = self.client.chat.completions.create(
            model=self.model,
            **options,
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        },
                        {"type": "text", "text": ANALYSIS_USER_PROMPT},
                    ],
                },
            ],
        )
        self.last_usage = response.usage.model_dump() if response.usage else None
        payload = normalize_analysis_payload(
            _extract_json(response.choices[0].message.content or "")
        )
        analysis = LookAnalysis(provider=f"qwen:{self.model}", image=metadata, **payload)
        from .makeup_colors import add_reference_samples
        add_reference_samples(path, analysis.features)
        return analysis


class FixtureVisionAnalyzer:
    """Deterministic contract fixture; it does not infer makeup from image pixels."""

    def analyze(self, path: Path) -> LookAnalysis:
        metadata = image_metadata(path)
        return LookAnalysis(
            provider="fixture:not-a-real-image-analysis",
            image=metadata,
            overall_style=["低饱和暖棕", "通勤柔雾"],
            signature_features=["暖棕渐层眼妆", "斜向柔和腮红", "柔雾豆沙唇"],
            features=[
                MakeupFeature(
                    region=Region.eye,
                    effect="暖棕渐层并轻微拉长眼尾",
                    color_family=["暖棕", "香槟"],
                    finish=["matte", "shimmer"],
                    placement="浅色铺上眼皮，深色集中眼尾，细闪点在眼中",
                    intensity="medium",
                    evidence="离线契约测试的固定样例，不来自像素推断",
                    confidence=Confidence.low,
                ),
                MakeupFeature(
                    region=Region.cheek,
                    effect="自然提气色并轻微向上提拉",
                    color_family=["蜜桃", "裸粉"],
                    finish=["satin"],
                    placement="颧骨外侧向太阳穴方向",
                    intensity="light",
                    evidence="离线契约测试的固定样例，不来自像素推断",
                    confidence=Confidence.low,
                ),
                MakeupFeature(
                    region=Region.lip,
                    effect="边缘柔化的豆沙唇",
                    color_family=["豆沙", "玫瑰棕"],
                    finish=["velvet"],
                    placement="唇内侧略深，边缘柔化",
                    intensity="medium",
                    evidence="离线契约测试的固定样例，不来自像素推断",
                    confidence=Confidence.low,
                ),
            ],
            uncertain_or_invisible=[
                "fixture 不进行真实像素语义识别",
                "无法由单张成妆图确认原作者使用的品牌、色号和真实顺序",
            ],
            safety_notes=["眼部产品避免进入眼睛；有已知敏感成分时优先排除"],
        )


class QwenTutorialPlanner:
    def __init__(self) -> None:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for Qwen tutorial planning")
        self.model = os.getenv("QWEN_TEXT_MODEL", "qwen-plus-latest")
        self.client = OpenAI(
            api_key=api_key,
            base_url=os.getenv(
                "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
        )

    def generate(
        self,
        analysis: LookAnalysis,
        profile: UserProfile,
        products: list[OwnedProduct],
        cards: list[TechniqueCard],
    ) -> TutorialPlan:
        context = {
            "analysis": analysis.model_dump(mode="json", exclude={"image"}),
            "profile": profile.model_dump(mode="json"),
            "owned_products": [item.model_dump(mode="json") for item in products],
            "technique_cards": [item.model_dump(mode="json") for item in cards],
        }
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.15,
            messages=[
                {"role": "system", "content": TUTORIAL_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": TUTORIAL_USER_PROMPT
                    + "\n\n输入数据：\n"
                    + json.dumps(context, ensure_ascii=False),
                },
            ],
        )
        payload = _extract_json(response.choices[0].message.content or "")
        return TutorialPlan(provider=f"qwen:{self.model}", **payload)
