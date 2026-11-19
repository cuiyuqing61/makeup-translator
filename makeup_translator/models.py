from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Region(str, Enum):
    complexion = "complexion"
    eyebrow = "eyebrow"
    eye = "eye"
    cheek = "cheek"
    lip = "lip"


class ColorSample(StrictModel):
    label: str = "可见颜色"
    hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    source: Literal["product_photo", "reference_photo"]
    confidence: Confidence = Confidence.low
    box: list[float] = Field(default_factory=list, min_length=4, max_length=4)


class MakeupTarget(StrictModel):
    id: str
    region: Region
    role: Literal['base', 'depth', 'highlight', 'line', 'brow', 'blush', 'contour', 'lip', 'finish']
    effect: str
    placement: str
    color_family: list[str] = Field(default_factory=list)
    finish: list[str] = Field(default_factory=list)
    intensity: Literal['light', 'medium', 'strong'] = 'medium'
    evidence: str
    confidence: Confidence
    color_samples: list[ColorSample] = Field(default_factory=list)
    image_boxes: list[list[float]] = Field(default_factory=list)
    required: bool = True


class MakeupFeature(StrictModel):
    region: Region
    effect: str = Field(min_length=2)
    color_family: list[str] = Field(default_factory=list)
    finish: list[str] = Field(default_factory=list)
    placement: str = ""
    intensity: Literal["light", "medium", "strong"] = "medium"
    evidence: str = Field(description="Only visible evidence from the reference image.")
    confidence: Confidence
    color_samples: list[ColorSample] = Field(default_factory=list)
    color_boxes: list[list[float]] = Field(default_factory=list)


class MakeupModuleDecision(StrictModel):
    module: Literal['base', 'brow', 'eyes', 'blush', 'contour_highlight', 'lips', 'decoration', 'overall']
    status: Literal['reproduce', 'light', 'preserve', 'optional', 'occluded', 'uncertain']
    target_ids: list[str] = Field(default_factory=list)
    evidence: str = ''


class ImageMetadata(StrictModel):
    path: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    format: str
    sha256: str


class LookAnalysis(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    provider: str
    image: ImageMetadata
    overall_style: list[str] = Field(min_length=1)
    signature_features: list[str] = Field(min_length=1, max_length=5)
    features: list[MakeupFeature] = Field(min_length=1)
    targets: list[MakeupTarget] = Field(default_factory=list)
    module_decisions: list[MakeupModuleDecision] = Field(default_factory=list)
    uncertain_or_invisible: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    normalization_notes: list[str] = Field(
        default_factory=list,
        description="Machine-readable values normalized from model wording.",
    )


class UserProfile(StrictModel):
    skill_level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    available_minutes: int = Field(default=15, ge=5, le=120)
    skin_tendency: str | None = None
    eye_notes: str | None = None
    face_observation: str | None = None
    face_feature_choices: dict[str, str] = Field(default_factory=dict)
    face_feature_decisions: dict[str, Literal["confirmed", "corrected"]] = Field(default_factory=dict)
    face_feature_photo_version: str | None = None
    face_features_effective: dict[str, str] = Field(default_factory=dict)
    sensitivities: list[str] = Field(default_factory=list)
    must_keep: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    available_tools: list[str] | None = None


class OwnedProduct(StrictModel):
    id: str
    category: str
    name: str
    shade: str | None = None
    color_family: list[str] = Field(default_factory=list)
    finish: list[str] = Field(default_factory=list)
    user_confirmed: bool = True
    color_samples: list[ColorSample] = Field(default_factory=list)


class TechniqueAction(StrictModel):
    order: int = Field(ge=1)
    instruction: str
    success_signal: str


class TechniqueCard(StrictModel):
    id: str
    title: str
    region: Region
    effects: list[str]
    suitable_for: list[str]
    avoid_when: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    product_categories: list[str]
    required_product_kinds: list[str] = Field(default_factory=list)
    actions: list[TechniqueAction]
    beginner_shortcut: str | None = None
    adaptation_rules: list[str] = Field(default_factory=list)
    source_type: Literal["expert_authored", "public_reference", "team_tested", "team_authored"]
    source_note: str
    source_publisher: str | None = None
    source_title: str | None = None
    source_url: str | None = None
    source_section: str | None = None
    source_checked_at: str | None = None
    review_status: Literal["draft", "reviewed", "tested"] = "draft"
    duration_minutes: int = Field(default=3, ge=1, le=30)
    difficulty: Literal['beginner', 'intermediate', 'advanced'] = 'beginner'


class ProductMatch(StrictModel):
    required_category: str
    selected_product_id: str | None = None
    match_reason: str
    status: Literal["owned_match", "owned_approximation", "catalog_candidate", "missing", "not_needed", "pending"]
    selected_color_label: str | None = None
    color_evidence: Literal["photo", "metadata", "none"] = "none"
    catalog_suggestions: list[str] = Field(default_factory=list)
    catalog_product_id: str | None = None
    catalog_product_name: str | None = None
    catalog_source_url: str | None = None


class ApplicationGuide(StrictModel):
    placement: str
    direction: str
    amount: str
    layers: str
    action: str
    completion_check: str


class DrawingGuide(StrictModel):
    anchor: Literal['left_eye', 'right_eye', 'left_cheek', 'right_cheek',
                    'left_brow', 'right_brow', 'lips', 'nose', 'face']
    side: Literal['left', 'right', 'both', 'center'] = 'both'
    offset_x: float = Field(default=0, ge=-0.35, le=0.35)
    offset_y: float = Field(default=0, ge=-0.35, le=0.35)
    radius_x: float = Field(default=0.12, gt=0, le=0.4)
    radius_y: float = Field(default=0.07, gt=0, le=0.3)
    direction: Literal['outward', 'inward', 'upward', 'downward', 'along'] = 'along'
    strength: Literal['light', 'medium', 'strong'] = 'medium'
    layers: int = Field(default=1, ge=1, le=4)


class ProductRequirement(StrictModel):
    category: str
    purpose: str
    suitable_forms: list[str] = Field(default_factory=list)


class TutorialStep(StrictModel):
    order: int = Field(ge=1)
    step_id: str | None = None
    region: Region
    goal: str
    instruction: str
    success_signal: str
    product_match: ProductMatch
    product_requirement: ProductRequirement | None = None
    tool: str | None = None
    tool_status: Literal['available', 'prepare', 'not_needed'] | None = None
    adjustment_reason: str
    technique_card_id: str
    technique_review_status: Literal['draft', 'reviewed', 'tested'] | None = None
    technique_source_title: str | None = None
    technique_source_url: str | None = None
    technique_source_publisher: str | None = None
    technique_source_section: str | None = None
    caution: str | None = None
    depends_on: list[int] = Field(default_factory=list)
    preserves_features: list[str] = Field(default_factory=list)
    duration_minutes: int = Field(default=3, ge=1, le=30)
    target_id: str | None = None
    target_role: str | None = None
    target_evidence: str | None = None
    face_evidence: list[str] = Field(default_factory=list)
    application: ApplicationGuide | None = None
    drawing: DrawingGuide | None = None


class TutorialPlan(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    provider: str
    title: str
    look_summary: str
    preserved_features: list[str]
    personalized_changes: list[str]
    steps: list[TutorialStep] = Field(min_length=1)
    missing_products: list[str] = Field(default_factory=list)
    uncertainty_notice: str
    estimated_minutes: int | None = Field(default=None, ge=5, le=180)
    review_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def steps_are_contiguous(self) -> "TutorialPlan":
        orders = [step.order for step in self.steps]
        expected = list(range(1, len(orders) + 1))
        if orders != expected:
            raise ValueError(f"step orders must be contiguous: expected {expected}, got {orders}")
        return self


class IllustrationArea(StrictModel):
    cx: int = Field(ge=0, le=320)
    cy: int = Field(ge=0, le=210)
    rx: int = Field(gt=0, le=160)
    ry: int = Field(gt=0, le=105)
    rotation: int = Field(default=0, ge=-90, le=90)


class IllustrationArrow(StrictModel):
    x1: int = Field(ge=0, le=320)
    y1: int = Field(ge=0, le=210)
    x2: int = Field(ge=0, le=320)
    y2: int = Field(ge=0, le=210)


class StepIllustration(StrictModel):
    version: Literal['1'] = '1'
    step_order: int = Field(ge=1)
    source_card_id: str
    template: Literal['face', 'eye', 'brow', 'lip']
    placement: str
    direction: str
    amount: str
    areas: list[IllustrationArea]
    arrows: list[IllustrationArrow]
    mode: Literal['standard', 'simplified'] = 'standard'


class PipelineResult(StrictModel):
    analysis: LookAnalysis
    retrieved_card_ids: list[str]
    tutorial: TutorialPlan
    step_illustrations: list[StepIllustration] = Field(default_factory=list)
    agent_trace: list[dict] = Field(default_factory=list)
    knowledge_version: str | None = None
    publication_status: Literal['preview', 'reviewed'] = 'preview'
