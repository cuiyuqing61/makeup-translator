from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from makeup_translator.illustrations import CARD_VISUALS
from makeup_translator.knowledge import TechniqueKnowledgeBase
from makeup_translator.models import OwnedProduct, UserProfile
from makeup_translator.pipeline import MakeupTutorialPipeline, match_product


def test_rebuilt_library_has_complete_source_and_visual_coverage():
    cards = TechniqueKnowledgeBase().cards
    assert len(cards) == 29
    assert {card.region.value for card in cards} == {'complexion', 'eyebrow', 'eye', 'cheek', 'lip'}
    assert {'base', 'brow', 'eyeshadow', 'eyeliner', 'mascara', 'blush', 'contour', 'highlighter', 'lip'} <= {
        category for card in cards for category in card.product_categories
    }
    assert Counter(card.source_publisher for card in cards).most_common() == [
        ('欧莱雅巴黎官网 Beauty Magazine', 25),
        ('毛戈平形象设计艺术学校「彩妆资讯」', 4),
    ]
    assert {card.id for card in cards} == set(CARD_VISUALS)
    assert all(card.source_type == 'public_reference' and card.review_status == 'draft' for card in cards)
    assert all(card.source_title and card.source_section and card.source_checked_at for card in cards)
    assert all(urlparse(card.source_url).hostname in {'www.lorealparisusa.com', 'www.maogp.com'} for card in cards)
    assert all(len(card.actions) >= 2 and all(action.success_signal for action in card.actions) for card in cards)
    assert not any('draft-v2' in card.id or card.id.endswith('-001') for card in cards)


def test_generated_steps_snapshot_exact_technique_source():
    result = MakeupTutorialPipeline('fixture').run(Path('examples/fixture.png'), UserProfile(), [])
    cards = {card.id: card for card in TechniqueKnowledgeBase().cards}
    for step in result.tutorial.steps:
        card = cards[step.technique_card_id]
        assert step.technique_source_url == card.source_url
        assert step.technique_source_title == card.source_title
        assert step.technique_source_section == card.source_section


def test_broad_inventory_categories_cannot_substitute_wrong_product_function():
    cards = {card.id: card for card in TechniqueKnowledgeBase().cards}
    foundation = OwnedProduct(id='f', category='base', name='粉底液')
    concealer = OwnedProduct(id='c', category='base', name='遮瑕液')
    brow_pencil = OwnedProduct(id='b', category='brow', name='细头眉笔')
    lipstick = OwnedProduct(id='l', category='lip', name='豆沙口红')
    assert match_product('base', [foundation], cards['complexion-spot-conceal']).status == 'missing'
    assert match_product('base', [concealer], cards['complexion-spot-conceal']).selected_product_id == 'c'
    assert match_product('brow', [brow_pencil], cards['eyebrow-set-shape']).status == 'missing'
    assert match_product('lip', [lipstick], cards['lip-center-gloss']).status == 'missing'
    assert match_product('lip', [lipstick], cards['lip-even-lipstick']).selected_product_id == 'l'
    nose_contour = OwnedProduct(id='n', category='contour', name='鼻部修容')
    assert match_product('contour', [nose_contour], cards['nose-soft-contour']).selected_product_id == 'n'
