from pathlib import Path

from apps.argus.test.factories import item_factory
from apps.argus.src.common.services.judgement_service import JudgementService
from apps.argus.src.common.services.rule_service import load_rule_set
from apps.argus.src.surfaces.qq.services.fact_service import QQFactService


async def test_judgement_summary_does_not_copy_original_text():
    surface = QQFactService()
    rules = load_rule_set(Path(__file__).resolve().parents[2] / 'rules/qq.yaml', known_facts=set(surface.known_facts))
    value = item_factory('private original text', detail={'mentions_bot': True})
    result = await JudgementService(rules, surface, None).judge([value], permission_level=2)
    assert result.decision == 'wake'
    assert 'private original text' not in str(result.decision_detail)
