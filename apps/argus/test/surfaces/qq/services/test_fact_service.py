from apps.argus.test.factories import item_factory
from apps.argus.src.common.schemas.observation import SelectedItem
from apps.argus.src.surfaces.qq.services.fact_service import QQFactService


def test_facts_only_use_selected_excerpt_and_preserve_group_signals():
    first = item_factory('full secret original', detail={'mentions_bot': False})
    second = item_factory('more full original', detail={'bot_name_hit': True})
    facts = QQFactService().build_facts([
        SelectedItem(item=first, excerpt='short one'), SelectedItem(item=second, excerpt='short two')], permission_level=2)
    assert facts['text'] == 'short one\nshort two'
    assert facts['mentions_bot'] is True and facts['item_count'] == 2
    assert facts['scope_kind'] == 'group' and facts['permission_level'] == 2
    assert 'secret' not in facts['text']
