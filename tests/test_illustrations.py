from pathlib import Path

from makeup_translator.illustrations import CARD_VISUALS, attach_illustrations
from makeup_translator.knowledge import TechniqueKnowledgeBase
from makeup_translator.models import OwnedProduct, UserProfile
from makeup_translator.pipeline import MakeupTutorialPipeline
from makeup_translator.revisions import RevisionRequest, initial_session, preview_revision


def tutorial_result():
    pipeline = MakeupTutorialPipeline('fixture')
    result = pipeline.run(Path('examples/fixture.png'), UserProfile(), [
        OwnedProduct(id='lip', category='lip', name='唇膏')])
    return pipeline, result


def test_all_techniques_have_an_illustration_and_each_step_gets_a_snapshot():
    cards = TechniqueKnowledgeBase().cards
    assert {card.id for card in cards} == set(CARD_VISUALS)
    pipeline, result = tutorial_result()
    assert len(result.step_illustrations) == len(result.tutorial.steps)
    for step, image in zip(result.tutorial.steps, result.step_illustrations):
        assert image.step_order == step.order
        assert image.source_card_id == step.technique_card_id
        assert image.areas
        assert image.placement and image.direction and image.amount
        assert image.template in {'face', 'eye', 'brow', 'lip'}

    # A saved result keeps its actual geometry when the registry changes later.
    before = result.model_dump(mode='json')['step_illustrations']
    first = result.tutorial.steps[0].technique_card_id
    original = CARD_VISUALS[first]
    try:
        CARD_VISUALS[first] = ('eye-center', 'changed', 'changed', 'changed')
        attach_illustrations(result)
        assert result.model_dump(mode='json')['step_illustrations'] == before
    finally:
        CARD_VISUALS[first] = original


def test_legacy_result_and_simplified_revision_get_matching_diagrams():
    pipeline, result = tutorial_result()
    old_data = result.model_dump(mode='json')
    old_data.pop('step_illustrations')
    metadata = {
        'profile': UserProfile().model_dump(mode='json'),
        'products': [OwnedProduct(id='lip', category='lip', name='唇膏').model_dump(mode='json')],
        'knowledge_cards': [card.model_dump(mode='json') for card in pipeline.knowledge.cards],
    }
    session = initial_session(old_data)
    changed_order = result.tutorial.steps[1].order
    preview = preview_revision(session, metadata, RevisionRequest(
        action='simplify', step=changed_order, base_version=session['active_version']))
    images = preview['result']['step_illustrations']
    assert len(images) == len(result.tutorial.steps)
    assert images[changed_order - 1]['mode'] == 'simplified'
    assert all(image['mode'] == 'standard' for image in images if image['step_order'] != changed_order)
    assert images[changed_order - 1]['source_card_id'] == result.tutorial.steps[changed_order - 1].technique_card_id
    assert not session['versions'][0]['result'].get('step_illustrations')
