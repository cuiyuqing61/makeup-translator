from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .models import ColorSample, OwnedProduct, StrictModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProductStatus(str, Enum):
    draft = "draft"
    pending_user_review = "pending_user_review"
    confirmed = "confirmed"


class SourceType(str, Enum):
    official = "official"
    official_store = "official_store"
    retailer = "retailer"
    review = "review"
    other = "other"


class SearchBudget(StrictModel):
    """Hard limits enforced by the product-search harness, not by the model."""

    qwen_identity_search_calls: int = Field(default=1, ge=0, le=1)
    qwen_review_search_calls: int = Field(default=1, ge=0, le=1)
    qwen_extraction_calls: int = Field(default=1, ge=0, le=1)
    schema_repair_calls: int = Field(default=1, ge=0, le=1)
    pages_to_validate: int = Field(default=2, ge=0, le=2)
    page_validation_concurrency: int = Field(default=2, ge=1, le=2)
    page_timeout_seconds: float = Field(default=5, gt=0, le=10)
    foreground_wait_seconds: float = Field(default=8, gt=0, le=15)
    hard_deadline_seconds: float = Field(default=60, gt=0, le=60)


class SearchAttempt(StrictModel):
    stage: Literal[
        "identity_search",
        "review_search",
        "extraction",
        "schema_repair",
        "page_validation",
        "image_comparison",
    ]
    status: Literal["success", "failed", "timeout", "skipped"]
    duration_ms: int = Field(ge=0)
    result_count: int | None = Field(default=None, ge=0)
    detail: str | None = None


class SearchAudit(StrictModel):
    status: Literal["completed", "partial", "timeout", "failed"]
    elapsed_ms: int = Field(ge=0)
    crossed_foreground_boundary: bool = False
    budget: SearchBudget = Field(default_factory=SearchBudget)
    attempts: list[SearchAttempt] = Field(default_factory=list)


class SourceValidation(StrictModel):
    checked: bool = True
    reachable: bool
    http_status: int | None = None
    final_url: str | None = None
    official_domain_verified: bool = False
    checked_at: datetime = Field(default_factory=utc_now)
    note: str | None = None


class ProductSource(StrictModel):
    image_url: str | None = None
    title: str
    url: str = Field(pattern=r"^https?://")
    source_type: SourceType
    supports_fields: list[str] = Field(default_factory=list)
    note: str | None = None
    validation: SourceValidation | None = None


class ProductAlternative(StrictModel):
    brand: str
    product_name: str
    model_or_shade: str | None = None
    market: str | None = None
    url: str = Field(pattern=r"^https?://")
    difference: str


class ProductEnrichment(StrictModel):
    field_evidence: dict[str, dict[str, str]] = Field(default_factory=dict)
    agent_trace: list[dict] = Field(default_factory=list)
    image_comparison: str | None = None
    match_status: Literal["exact_match", "likely_match", "ambiguous"]
    canonical_brand: str
    canonical_name: str
    product_line: str | None = None
    model_or_shade: str | None = None
    market: str | None = None
    category: str
    color_family: list[str] = Field(default_factory=list)
    finish: list[str] = Field(default_factory=list)
    official_description: str | None = None
    official_usage: str | None = None
    official_url: str | None = None
    review_observations: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    alternatives: list[ProductAlternative] = Field(default_factory=list)
    sources: list[ProductSource] = Field(min_length=1)
    search_audit: SearchAudit | None = None
    searched_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def official_url_must_be_sourced(self) -> "ProductEnrichment":
        if self.official_url and self.official_url not in {source.url for source in self.sources}:
            raise ValueError("official_url must also appear in sources")
        return self

    def blocking_source_issues(self) -> list[str]:
        issues: list[str] = []
        if self.match_status == 'ambiguous' or self.conflicts:
            issues.append('产品型号或版本存在歧义，请编辑或对照包装明确确认')
        if self.official_url:
            official = next(
                (source for source in self.sources if source.url == self.official_url), None
            )
            if official is None or official.validation is None:
                issues.append("官网来源尚未执行可访问性校验")
            elif not official.validation.reachable:
                issues.append("官网产品页当前不可访问")
            elif not official.validation.official_domain_verified:
                issues.append("官网域名尚未通过品牌域名表校验")
        elif not any(
            source.validation
            and source.validation.reachable
            and source.source_type
            in {SourceType.official, SourceType.official_store, SourceType.retailer}
            and bool(
                {"canonical_name", "model_or_shade", "category"}
                & set(source.supports_fields)
            )
            for source in self.sources
        ):
            issues.append("没有可访问且能支持产品身份的官网、官方店或零售商来源")
        return issues


class ProductRecord(StrictModel):
    id: str
    status: ProductStatus = ProductStatus.draft
    photo_urls: list[str] = Field(default_factory=list, max_length=2)
    opened_photo_url: str | None = None
    opened_photo_version: str | None = None
    color_samples: list[ColorSample] = Field(default_factory=list)
    color_task_id: str | None = None
    color_error: str | None = None
    raw_brand: str = Field(min_length=1)
    raw_name: str = Field(min_length=1)
    raw_model_or_shade: str | None = None
    raw_market: str | None = None
    enrichment: ProductEnrichment | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    confirmed_at: datetime | None = None

    @model_validator(mode="after")
    def state_has_required_data(self) -> "ProductRecord":
        if self.status != ProductStatus.draft and self.enrichment is None:
            raise ValueError(f"{self.status.value} requires enrichment data")
        if self.status == ProductStatus.confirmed and self.confirmed_at is None:
            raise ValueError("confirmed product requires confirmed_at")
        if self.status != ProductStatus.confirmed and self.confirmed_at is not None:
            raise ValueError("only confirmed product may have confirmed_at")
        return self

    def to_owned_product(self) -> OwnedProduct:
        if self.status != ProductStatus.confirmed or self.enrichment is None:
            raise ValueError("only confirmed products can enter the tutorial product list")
        return OwnedProduct(
            id=self.id,
            category=self.enrichment.category,
            name=f"{self.enrichment.canonical_brand} {self.enrichment.canonical_name}",
            shade=self.enrichment.model_or_shade,
            color_family=self.enrichment.color_family,
            finish=self.enrichment.finish,
            user_confirmed=True,
            color_samples=self.color_samples,
        )


class ProductDatabase(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    products: list[ProductRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def product_ids_are_unique(self) -> "ProductDatabase":
        ids = [product.id for product in self.products]
        if len(ids) != len(set(ids)):
            raise ValueError("product ids must be unique")
        return self
