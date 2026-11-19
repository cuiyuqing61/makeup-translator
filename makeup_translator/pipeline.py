from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

from .knowledge import TechniqueKnowledgeBase, attach_technique_sources
from .illustrations import attach_illustrations
from .models import (
    LookAnalysis,
    MakeupFeature,
    OwnedProduct,
    PipelineResult,
    ProductMatch,
    ProductRequirement,
    Region,
    TechniqueCard,
    TutorialPlan,
    TutorialStep,
    UserProfile,
)
from .providers import FixtureVisionAnalyzer, QwenTutorialPlanner, QwenVisionAnalyzer


CATEGORY_BY_REGION: dict[Region, list[str]] = {
    Region.complexion: ["base", "contour"],
    Region.eyebrow: ["brow"],
    Region.eye: ["eyeshadow", "eyeliner", "mascara"],
    Region.cheek: ["contour", "highlighter", "blush"],
    Region.lip: ["lip"],
}


def inferred_product_kinds(product: OwnedProduct) -> set[str]:
    """Infer only explicit product functions; uncertain broad labels stay unknown."""
    name = product.name.casefold()
    kinds: set[str] = set()
    keywords = {
        'foundation': ('粉底', '气垫', 'foundation', 'cushion'),
        'concealer': ('遮瑕', 'concealer'),
        'setting_powder': ('散粉', '定妆粉', '蜜粉', 'setting powder', 'loose powder'),
        'brow_filler': ('眉笔', '眉粉', 'brow pencil', 'brow powder', 'brow pen'),
        'brow_gel': ('眉胶', '染眉膏', 'brow gel', 'brow mascara'),
        'eye_primer': ('眼部打底', '眼影打底', 'eye primer', 'eyeshadow primer'),
        'lip_balm': ('润唇', '护唇', 'lip balm'),
        'lip_liner': ('唇线笔', 'lip liner'),
        'lip_gloss': ('唇蜜', '透明唇釉', 'lip gloss'),
        'lip_color': ('口红', '唇釉', '唇彩', '唇泥', '唇膏', 'lipstick', 'lip tint', 'lip stain'),
    }
    for kind, terms in keywords.items():
        if any(term in name for term in terms):
            kinds.add(kind)
    if 'lip_balm' in kinds or 'lip_liner' in kinds:
        kinds.discard('lip_color')
    if 'lip_gloss' in kinds and ('透明' in name or 'clear' in name):
        kinds.discard('lip_color')
    return kinds


def product_satisfies_card(product: OwnedProduct, card: TechniqueCard, category: str) -> bool:
    if product.category != category:
        return False
    return not card.required_product_kinds or bool(
        inferred_product_kinds(product) & set(card.required_product_kinds)
    )


def match_product(category: str, products: list[OwnedProduct], card: TechniqueCard,
                  feature: MakeupFeature | None = None) -> ProductMatch:
    products = [p for p in products if p.user_confirmed and p.category in CATEGORY_BY_REGION.get(card.region, [])]
    compatible = [p for p in products if product_satisfies_card(p, card, category)]
    from .makeup_colors import catalog_suggestions, color_distance
    target_samples = [s for s in (feature.color_samples if feature else []) if s.confidence.value != 'low']
    photo_ranked = []
    if target_samples:
        for product in compatible:
            choices = [s for s in product.color_samples if s.confidence.value != 'low']
            if choices:
                nearest = [min(((color_distance(a.hex, b.hex), b) for b in choices),
                               key=lambda pair: pair[0]) for a in target_samples]
                score = sum(distance for distance, _ in nearest) / len(nearest)
                labels = '、'.join(dict.fromkeys(sample.label for _, sample in nearest))
                photo_ranked.append((score, product, labels))
    photo_ranked.sort(key=lambda item: item[0])
    # Delta E 76 is a ranking aid, not a calibrated probability. Leave visibly
    # distant swatches for user review instead of presenting an exact match.
    if photo_ranked and photo_ranked[0][0] <= 45:
        distance, exact, swatch_labels = photo_ranked[0]
        return ProductMatch(
            required_category=category,
            selected_product_id=exact.id,
            selected_color_label=swatch_labels,
            color_evidence='photo',
            match_reason=f"已有产品“{exact.name}”的用途匹配，照片中“{swatch_labels}”的可见颜色较接近参考图；光线和上脸效果仍需确认。",
            status="owned_match",
        )
    families = feature.color_family if feature else []
    metadata_ranked = sorted(compatible, key=lambda p: (
        -len(set(map(str.casefold, p.color_family)) & set(map(str.casefold, families))),
        -len(set(map(str.casefold, p.finish)) & set(map(str.casefold, feature.finish if feature else []))),
    ))
    if metadata_ranked and not photo_ranked:
        exact = metadata_ranked[0]
        return ProductMatch(required_category=category, selected_product_id=exact.id,
                            match_reason=f"已有产品“{exact.name}”的类别与用途匹配；缺少可比较的可靠照片颜色，请确认实际色号。",
                            status='owned_match', color_evidence='metadata' if families else 'none')
    if photo_ranked:
        return ProductMatch(required_category=category, status='missing',
                            match_reason='已有同类产品，但照片中的可见颜色与参考图差异较大；请在实物和相同光线下确认。',
                            catalog_suggestions=catalog_suggestions(category, families))
    return ProductMatch(
        required_category=category,
        match_reason=("已有同类产品，但无法确认是否具有这一步所需的具体用途；请核实或补录。"
                      if any(p.category == category for p in products)
                      else "现有清单中未找到可确认的同类产品；可先跳过或由用户补录。"),
        status="missing",
        catalog_suggestions=catalog_suggestions(category, families),
    )


class MakeupTutorialPipeline:
    def __init__(self, provider: str = "fixture") -> None:
        if provider == "qwen":
            self.analyzer = QwenVisionAnalyzer()
            self.planner = None
        elif provider == "fixture":
            self.analyzer = FixtureVisionAnalyzer()
            self.planner = None
        else:
            raise ValueError("provider must be 'fixture' or 'qwen'")
        self.provider = provider
        self.knowledge = TechniqueKnowledgeBase()

    def analyze_image(self, image_path: Path) -> LookAnalysis:
        return self.analyzer.analyze(image_path)

    def generate_tutorial(
        self,
        analysis: LookAnalysis,
        profile: UserProfile,
        products: list[OwnedProduct],
        cards: list[TechniqueCard],
    ) -> TutorialPlan:
        """Deterministic baseline generator; model-based synthesis is the next adapter."""
        visible_regions = {feature.region for feature in analysis.features}
        selected_cards: list[TechniqueCard] = []
        for region in [Region.complexion, Region.eyebrow, Region.eye, Region.cheek, Region.lip]:
            if region not in visible_regions:
                continue
            preferred = {Region.eye: 'eye-neutral-shadow', Region.cheek: 'cheek-soft-blush',
                         Region.lip: 'lip-even-lipstick'}
            card = next((candidate for candidate in cards if candidate.region == region
                         and candidate.id == preferred.get(region)), None) or next(
                             (candidate for candidate in cards if candidate.region == region), None)
            if card:
                selected_cards.append(card)

        steps: list[TutorialStep] = []
        from .face_profile import instruction_note
        for card in selected_cards:
            category = card.product_categories[0]
            feature = next((f for f in analysis.features if f.region == card.region), None)
            product_match = match_product(category, products, card, feature)
            actions = card.actions
            if profile.skill_level == "beginner" and card.beginner_shortcut:
                instruction = card.beginner_shortcut
                success_signal = actions[-1].success_signal
            else:
                instruction = " ".join(action.instruction for action in actions)
                success_signal = actions[-1].success_signal
            note = instruction_note(card.id, profile.face_features_effective)
            if note:
                instruction += " " + note
            target = next((t for t in analysis.targets if t.region == card.region), None)
            steps.append(
                TutorialStep(
                    order=len(steps) + 1,
                    region=card.region,
                    goal=card.title,
                    instruction=instruction,
                    success_signal=success_signal,
                    product_match=product_match,
                    tool=card.tools[0] if card.tools else None,
                    adjustment_reason=("根据你的面部特点调整：" + note if note else
                        f"根据你选择的 {profile.skill_level} 熟练度调整动作。"),
                    technique_card_id=card.id,
                    step_id=f'{target.id}:{card.id}:{category}:1' if target else None,
                    product_requirement=ProductRequirement(category=category,
                        purpose=target.effect if target else card.title,
                        suitable_forms=card.required_product_kinds),
                    target_id=target.id if target else None,
                    target_role=target.role if target else None,
                    target_evidence=target.evidence if target else None,
                    caution=("；".join(profile.sensitivities) if profile.sensitivities else None),
                )
            )

        warnings = self.review(analysis, profile, steps)
        missing = sorted(
            {
                step.product_match.required_category
                for step in steps
                if step.product_match.status == "missing"
            }
        )
        preserved = profile.must_keep or analysis.signature_features[:2]
        return TutorialPlan(
            provider="deterministic-baseline:v1",
            title="把参考妆转成你的可执行版本",
            look_summary="、".join(analysis.overall_style),
            preserved_features=preserved,
            personalized_changes=[
                f"按参考妆效规划 {len(steps)} 个步骤",
                "优先匹配已有产品；无法确认时明确标记缺口",
            ],
            steps=steps,
            missing_products=missing,
            uncertainty_notice=(
                "这是根据成妆可见效果推导的一种可行复刻方案，不代表原作者的真实产品或操作顺序。"
            ),
            estimated_minutes=None,
            review_warnings=warnings,
        )

    @staticmethod
    def review(
        analysis: LookAnalysis, profile: UserProfile, steps: list[TutorialStep]
    ) -> list[str]:
        warnings: list[str] = []
        covered = {step.region for step in steps}
        visible = {feature.region for feature in analysis.features}
        missing_regions = visible - covered
        if missing_regions:
            warnings.append("以下可见区域暂无技法卡：" + "、".join(r.value for r in missing_regions))
        if not steps:
            warnings.append("未生成步骤；需要补充或修正技法卡。")
        if any(feature.confidence == "low" for feature in analysis.features):
            warnings.append("参考图含低置信观察项，用户应先确认颜色与位置描述。")
        return warnings

    def run(
        self,
        image_path: Path,
        profile: UserProfile,
        products: list[OwnedProduct],
        progress: Callable[[int, str, str], None] | None = None,
    ) -> PipelineResult:
        report = progress or (lambda _percent, _stage, _message: None)
        if self.provider == 'qwen':
            from .personalized_tutorial import run_personalized_tutorial
            return run_personalized_tutorial(self, image_path, profile, products, report)
        report(8, "image_analysis", "正在读取图片和识别妆面特征")
        analysis = self.analyze_image(image_path)
        report(55, "knowledge_retrieval", "正在匹配适合你的技法知识卡")
        cards = self.knowledge.retrieve(analysis, profile)
        report(68, "tutorial_generation", "正在生成个性化化妆步骤")
        if self.planner:
            tutorial = self.planner.generate(analysis, profile, products, cards)
            tutorial.review_warnings.extend(self.review(analysis, profile, tutorial.steps))
        else:
            tutorial = self.generate_tutorial(analysis, profile, products, cards)
        known_products = {p.id: p for p in products if p.user_confirmed}
        for step in tutorial.steps:
            match = step.product_match
            product = known_products.get(match.selected_product_id)
            if match.selected_product_id and (not product or product.category not in CATEGORY_BY_REGION.get(step.region, [])):
                match.selected_product_id = None
                match.status = 'missing'
                match.match_reason = '产品与使用部位不匹配，已移除推荐；请补充该部位专用产品。'
            elif product:
                match.match_reason += ' 色号和上脸效果仍需现场确认，不保证与参考图一致。'
        tutorial.missing_products = sorted({s.product_match.required_category for s in tutorial.steps if s.product_match.status == 'missing'})
        attach_technique_sources(tutorial, cards)
        report(94, "quality_review", "正在检查步骤完整性和产品匹配")
        return attach_illustrations(PipelineResult(
            analysis=analysis,
            retrieved_card_ids=[card.id for card in cards],
            tutorial=tutorial,
        ))
