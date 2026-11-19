from pathlib import Path

from makeup_translator.models import OwnedProduct, UserProfile
from makeup_translator.pipeline import MakeupTutorialPipeline
from makeup_translator.providers import normalize_analysis_payload


DEMO_IMAGE = Path("examples/fixture.png")


def test_fixture_pipeline_runs_end_to_end():
    products = [
        OwnedProduct(id="e1", category="eyeshadow", name="暖棕盘"),
        OwnedProduct(id="c1", category="blush", name="裸粉腮红"),
        OwnedProduct(id="l1", category="lip", name="豆沙唇釉"),
    ]
    result = MakeupTutorialPipeline(provider="fixture").run(
        DEMO_IMAGE,
        UserProfile(skill_level="beginner", available_minutes=12),
        products,
    )

    assert result.analysis.provider.startswith("fixture")
    assert result.analysis.image.width > 0
    assert len(result.retrieved_card_ids) <= len(result.analysis.features) * 3
    assert {'eye', 'cheek', 'lip'} <= {id.split('-')[0] for id in result.retrieved_card_ids}
    assert {step.region.value for step in result.tutorial.steps} == {"eye", "cheek", "lip"}
    assert [step.order for step in result.tutorial.steps] == [1, 2, 3]
    assert not result.tutorial.missing_products


def test_missing_products_are_explicit():
    result = MakeupTutorialPipeline(provider="fixture").run(
        DEMO_IMAGE, UserProfile(), []
    )
    assert set(result.tutorial.missing_products) == {"eyeshadow", "blush", "lip"}


def test_knowledge_retrieval_does_not_add_invisible_regions():
    result = MakeupTutorialPipeline(provider="fixture").run(
        DEMO_IMAGE, UserProfile(skill_level="beginner"), []
    )
    assert not any(card.startswith("complexion") for card in result.retrieved_card_ids)
    assert not any(card.startswith("brow") for card in result.retrieved_card_ids)


def test_model_wording_is_normalized_and_audited():
    payload = {
        "features": [
            {
                "region": "cheeks",
                "intensity": "light to medium",
                "confidence": "medium confidence",
            }
        ]
    }
    normalized = normalize_analysis_payload(payload)
    feature = normalized["features"][0]
    assert feature == {
        "region": "cheek",
        "intensity": "medium",
        "confidence": "medium",
    }
    assert len(normalized["normalization_notes"]) == 3
