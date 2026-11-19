from pathlib import Path

from fastapi.testclient import TestClient

from makeup_translator.api import create_app
from makeup_translator.face_profile import (
    ANALYSIS_PROMPT_VERSION, effective_features, instruction_note, normalize_analysis,
)
from makeup_translator.storage import Storage
from makeup_translator.models import UserProfile
from makeup_translator.pipeline import MakeupTutorialPipeline


def test_normalized_features_and_photo_scoped_user_correction():
    observations = normalize_analysis({"features": {
        "eyelid": {"value": "inner_double", "certainty": "clear", "evidence": "褶线可见"},
        "local_redness": {"value": "visible", "certainty": "uncertain", "evidence": "光线偏红"},
        "eye_shape": {"value": "invented", "certainty": "clear", "evidence": ""},
    }})
    assert observations["prompt_version"] == ANALYSIS_PROMPT_VERSION
    assert observations["features"]["eye_shape"]["value"] == "unknown"
    assert effective_features(observations, {}, None, "p1") == {"eyelid": "inner_double"}
    choices = {"eyelid": "double", "local_redness": "not_visible"}
    assert effective_features(observations, choices, "p1", "p1") == choices
    assert effective_features(observations, choices, "p1", "p2") == {"eyelid": "inner_double"}
    assert instruction_note("eye-neutral-shadow", {"eyelid": "inner_double"})
    assert instruction_note("eye-crease-definition", {"eye_shape": "peach_blossom"})
    assert instruction_note("eyebrow-fill-gaps", {"brow_shape": "straight"})
    assert instruction_note("cheek-soft-blush", {"cheek_shadow": "visible"})


def test_profile_feature_api_validates_options_and_keeps_manual_fields(tmp_path: Path):
    database = tmp_path / "profile.sqlite"
    Storage(database.with_suffix('.sqlite3')).put("profile_photo", "user", {
        "version": "photo-v1", "observations": {"features": {
            "eye_shape": {"value": "almond", "certainty": "clear", "evidence": "轮廓可见"},
        }},
    })
    app = create_app(store_path=database, frontend_dist=None)
    with TestClient(app) as client:
        response = client.patch('/api/profile', json={"skin_tendency": "偏干"})
        assert response.status_code == 200
        assert client.put('/api/profile/features', json={"photo_version": "old", "choices": {"eye_shape": "almond"}, "decision": "confirmed"}).status_code == 409
        assert client.put('/api/profile/features', json={"photo_version": "photo-v1", "choices": {"eye_shape": "round"}, "decision": "confirmed"}).status_code == 409
        assert client.put('/api/profile/features', json={"photo_version": "photo-v1", "choices": {"eye_shape": "alien"}, "decision": "corrected"}).status_code == 422
        corrected = client.put('/api/profile/features', json={"photo_version": "photo-v1", "choices": {"eye_shape": "peach_blossom"}, "decision": "corrected"})
        assert corrected.status_code == 200
        assert corrected.json()["face_feature_choices"]["eye_shape"] == "peach_blossom"
        assert corrected.json()["skin_tendency"] == "偏干"
        assert client.get('/api/profile/feature-options').json()['features']['eye_shape']['options']['peach_blossom'] == '桃花眼'


def test_visible_eye_feature_changes_tutorial_step():
    result = MakeupTutorialPipeline('fixture').run(
        Path('examples/fixture.png'), UserProfile(face_features_effective={'eyelid': 'inner_double'}), [])
    assert any('睁眼确认能看见的范围' in step.instruction for step in result.tutorial.steps)
    assert any(step.adjustment_reason.startswith('根据你的面部特点调整') for step in result.tutorial.steps)
