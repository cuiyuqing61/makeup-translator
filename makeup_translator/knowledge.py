from __future__ import annotations

import json
from pathlib import Path

from .models import LookAnalysis, MakeupTarget, TechniqueCard, TutorialPlan, UserProfile


DEFAULT_KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge_cards.json"


class TechniqueKnowledgeBase:
    def __init__(self, path: Path = DEFAULT_KNOWLEDGE_PATH):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.cards = [TechniqueCard.model_validate(item) for item in raw]
        if path == DEFAULT_KNOWLEDGE_PATH:
            if len({card.id for card in self.cards}) != len(self.cards):
                raise ValueError('技法库存在重复 ID')
            if any(not card.source_url or not card.source_section for card in self.cards):
                raise ValueError('公开技法卡必须注明原文链接和对应章节')

    @staticmethod
    def candidates_for_target(target: MakeupTarget, cards: list[TechniqueCard]) -> list[TechniqueCard]:
        from .personalized_tutorial import _card_matches_target

        candidates = [card for card in cards if _card_matches_target(card, target)]
        words = ' '.join((target.effect, target.placement, *target.color_family,
                          *target.finish, target.intensity)).casefold()

        def score(card: TechniqueCard) -> tuple[int, str]:
            terms = [*card.effects, *card.suitable_for, card.title]
            matches = sum(3 if term.casefold() in words else 0 for term in terms if term)
            return matches, card.id

        return sorted(candidates, key=score, reverse=True)[:3]

    def retrieve(self, analysis: LookAnalysis, profile: UserProfile | None = None,
                 limit: int | None = None) -> list[TechniqueCard]:
        """Select up to three cards per reference target, independent of the user profile.

        The legacy profile and limit arguments remain accepted by older callers.
        A global limit would erase some targets, so it is intentionally ignored.
        """
        targets = analysis.targets or [MakeupTarget(
            id=f'feature-{index}', region=feature.region, role={
                'complexion': 'base', 'eyebrow': 'brow', 'eye': 'base',
                'cheek': 'blush', 'lip': 'lip'}[feature.region.value],
            effect=feature.effect, placement=feature.placement,
            color_family=feature.color_family, finish=feature.finish,
            intensity=feature.intensity, evidence=feature.evidence,
            confidence=feature.confidence) for index, feature in enumerate(analysis.features)]
        selected: dict[str, TechniqueCard] = {}
        for target in targets:
            for card in self.candidates_for_target(target, self.cards):
                selected.setdefault(card.id, card)
        return list(selected.values())


def attach_technique_sources(tutorial: TutorialPlan, cards: list[TechniqueCard]) -> TutorialPlan:
    by_id = {card.id: card for card in cards}
    for step in tutorial.steps:
        card = by_id.get(step.technique_card_id)
        if card and card.source_url:
            step.technique_review_status = card.review_status
            step.technique_source_title = card.source_title
            step.technique_source_url = card.source_url
            step.technique_source_publisher = card.source_publisher
            step.technique_source_section = card.source_section
    return tutorial
