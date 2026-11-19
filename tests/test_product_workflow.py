from pathlib import Path
import threading

import pytest

from makeup_translator.product_models import (
    ProductEnrichment,
    ProductSource,
    ProductStatus,
    SearchBudget,
    SourceValidation,
    SourceType,
)
from makeup_translator.product_enrichment import (
    SearchController,
    reconcile_search_sources,
    validate_enrichment_sources,
)
from makeup_translator.product_store import ProductStore


def sample_enrichment() -> ProductEnrichment:
    official_url = "https://example-brand.test/product/123"
    return ProductEnrichment(
        match_status="exact_match",
        canonical_brand="示例品牌",
        canonical_name="示例唇膏",
        model_or_shade="123",
        market="中国大陆",
        category="lip",
        color_family=["豆沙"],
        finish=["glossy"],
        official_description="示例官方描述",
        official_url=official_url,
        sources=[
            ProductSource(
                title="示例官方产品页",
                url=official_url,
                source_type=SourceType.official,
                supports_fields=["canonical_name", "model_or_shade", "finish"],
                validation=SourceValidation(
                    reachable=True,
                    http_status=200,
                    final_url=official_url,
                    official_domain_verified=True,
                ),
            )
        ],
    )


def test_draft_pending_confirmed_state_machine(tmp_path: Path):
    store = ProductStore(tmp_path / "products.json")
    draft = store.add_draft("示例品牌", "示例唇膏", "123", "中国大陆")
    assert draft.status == ProductStatus.draft

    with pytest.raises(ValueError, match="only pending_user_review"):
        store.confirm(draft.id)

    pending = store.set_pending(draft.id, sample_enrichment())
    assert pending.status == ProductStatus.pending_user_review
    assert pending.confirmed_at is None
    assert store.export_confirmed() == []

    confirmed = store.confirm(draft.id)
    assert confirmed.status == ProductStatus.confirmed
    assert confirmed.confirmed_at is not None
    assert store.export_confirmed() == [
        {
            "id": draft.id,
            "category": "lip",
            "name": "示例品牌 示例唇膏",
            "shade": "123",
            "color_family": ["豆沙"],
            "finish": ["glossy"],
            "user_confirmed": True,
        }
    ]


def test_pending_product_cannot_be_enriched_twice(tmp_path: Path):
    store = ProductStore(tmp_path / "products.json")
    draft = store.add_draft("示例品牌", "示例唇膏")
    store.set_pending(draft.id, sample_enrichment())
    with pytest.raises(ValueError, match="only draft"):
        store.set_pending(draft.id, sample_enrichment())


def test_official_url_must_be_in_sources():
    with pytest.raises(ValueError, match="official_url must also appear"):
        ProductEnrichment(
            match_status="likely_match",
            canonical_brand="示例品牌",
            canonical_name="示例产品",
            category="other",
            official_url="https://official.test/product",
            sources=[
                ProductSource(
                    title="其他来源",
                    url="https://retailer.test/product",
                    source_type="retailer",
                )
            ],
        )


def test_confirmation_blocks_unreachable_official_source(tmp_path: Path):
    store = ProductStore(tmp_path / "products.json")
    draft = store.add_draft("示例品牌", "示例唇膏")
    enrichment = sample_enrichment()
    payload = enrichment.model_dump()
    source_payload = payload["sources"][0]
    source_payload["validation"] = SourceValidation(
        reachable=False,
        http_status=404,
        official_domain_verified=True,
    )
    store.set_pending(draft.id, ProductEnrichment.model_validate(payload))

    with pytest.raises(ValueError, match="官网产品页当前不可访问"):
        store.confirm(draft.id)

    confirmed = store.confirm(draft.id, acknowledge_unverified=True)
    assert confirmed.status == ProductStatus.confirmed


def test_only_dashscope_search_urls_survive_reconciliation(tmp_path: Path):
    registry = tmp_path / "domains.json"
    registry.write_text('{"示例品牌": ["brand.test"]}', encoding="utf-8")
    payload = {
        "canonical_brand": "示例品牌",
        "official_url": "https://brand.test/invented-product",
        "color_family": ["豆沙"],
        "finish": ["matte"],
        "official_description": "模型描述",
        "official_usage": None,
        "unresolved_fields": [],
        "validation_warnings": [],
        "sources": [
            {
                "title": "模型拼接页",
                "url": "https://brand.test/invented-product",
                "source_type": "official",
                "supports_fields": ["color_family", "finish", "official_description"],
                "note": None,
            }
        ],
    }
    reconciled = reconcile_search_sources(
        payload,
        [{"title": "真实搜索页", "url": "https://brand.test/real-page"}],
        registry,
    )
    assert reconciled["official_url"] is None
    assert reconciled["color_family"] == []
    assert reconciled["finish"] == []
    assert reconciled["sources"][0]["url"] == "https://brand.test/real-page"
    assert "invented-product" in reconciled["validation_warnings"][0]


def test_other_webpage_is_not_enough_to_confirm_identity(tmp_path: Path):
    store = ProductStore(tmp_path / "products.json")
    draft = store.add_draft("示例品牌", "示例唇膏")
    enrichment = ProductEnrichment(
        match_status="ambiguous",
        canonical_brand="示例品牌",
        canonical_name="示例唇膏",
        category="lip",
        sources=[
            ProductSource(
                title="普通讨论页",
                url="https://discussion.test/page",
                source_type="other",
                supports_fields=[],
                validation=SourceValidation(
                    reachable=True,
                    http_status=200,
                    final_url="https://discussion.test/page",
                ),
            )
        ],
    )
    store.set_pending(draft.id, enrichment)
    with pytest.raises(ValueError, match="没有可访问且能支持产品身份"):
        store.confirm(draft.id)


def test_search_controller_refuses_calls_beyond_budget():
    controller = SearchController(SearchBudget())
    controller.claim("identity_search")
    with pytest.raises(RuntimeError, match="budget exhausted"):
        controller.claim("identity_search")


def test_review_search_result_keeps_review_evidence_boundary(tmp_path: Path):
    registry = tmp_path / "domains.json"
    registry.write_text('{"示例品牌": ["brand.test"]}', encoding="utf-8")
    payload = {
        "canonical_brand": "示例品牌",
        "canonical_name": "示例唇膏",
        "unresolved_fields": [],
        "sources": [],
    }
    reconciled = reconcile_search_sources(
        payload,
        [
            {
                "title": "示例品牌 示例唇膏实测",
                "url": "https://www.xiaohongshu.com/example-lipstick",
                "snippet": "示例品牌示例唇膏的试色和质地",
                "_search_scope": "review",
            }
        ],
        registry,
    )
    assert reconciled["sources"][0]["source_type"] == "review"


def test_only_two_pages_are_validated_concurrently(monkeypatch, tmp_path: Path):
    barrier = threading.Barrier(2)
    visited: list[str] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, url: str):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, _method: str, url: str):
            visited.append(url)
            barrier.wait(timeout=1)
            return FakeResponse(url)

    monkeypatch.setattr("makeup_translator.product_enrichment.httpx.Client", FakeClient)
    registry = tmp_path / "domains.json"
    registry.write_text('{"示例品牌": ["brand.test"]}', encoding="utf-8")
    enrichment = ProductEnrichment(
        match_status="likely_match",
        canonical_brand="示例品牌",
        canonical_name="示例唇膏",
        category="lip",
        official_url="https://brand.test/product",
        sources=[
            ProductSource(
                title="官网",
                url="https://brand.test/product",
                source_type="official",
            ),
            ProductSource(
                title="测评",
                url="https://reviews.test/product",
                source_type="review",
            ),
            ProductSource(
                title="第三个来源",
                url="https://retailer.test/product",
                source_type="retailer",
            ),
        ],
    )
    checked = validate_enrichment_sources(
        enrichment,
        registry,
        max_pages=2,
        concurrency=2,
        page_timeout_seconds=1,
    )
    assert set(visited) == {
        "https://brand.test/product",
        "https://reviews.test/product",
    }
    assert sum(source.validation is not None for source in checked.sources) == 2
