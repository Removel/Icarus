import asyncio
from types import SimpleNamespace

from agent_app.plugins.argus.plugin import ArgusPlugin
from agent_app.plugins.argus.tools import ArgusTool, TOOL_NAMES
from icarus.core.run_control import TaskChannelRegistry


def test_static_argus_guidance_is_short_and_has_no_dynamic_state():
    plugin = ArgusPlugin('argus', SimpleNamespace(), {})
    assert hasattr(plugin,'static_prompt_sections'), 'stable Argus guidance belongs in system'
    sections = plugin.static_prompt_sections()
    text = '\n'.join(s.content for s in sections)
    assert 'argus_guide' in text
    assert 'def main' not in text
    assert len(text) < 1800
    assert all(s.layer=='session' for s in sections)


def test_guidance_distinguishes_platform_delivery_from_ordinary_chat():
    text = '\n'.join(s.content for s in ArgusPlugin('argus', SimpleNamespace(), {}).static_prompt_sections())
    assert 'your platform identity' in text
    assert 'ordinary chat' in text
    assert 'internal operator' in text
    assert 'before argus_commit' in text
    assert 'no_action' in text
    assert 'mcp_tool_search' in text and 'mcp_tool_execute' in text
    assert 'reply to every message' in text


def test_commit_help_and_description_require_actions_before_reporting():
    from agent_app.plugins.argus.guide import guide
    from agent_app.plugins.argus.tools import DESCRIPTIONS
    help_text = guide('commit', {})['text']
    assert 'send' in help_text and 'before' in help_text
    assert 'assistant' in help_text and 'no_action' in help_text
    assert 'before' in DESCRIPTIONS['argus_commit']
    assert 'platform' in DESCRIPTIONS['argus_commit']


def test_guide_is_read_only_and_topic_scoped():
    assert 'argus_guide' in TOOL_NAMES, 'missing on-demand guide'
    channels=TaskChannelRegistry();channels.create('task')
    binding={'wake':{'rule_sdk':{'version':2,'max_calls':3}},'finished':False}
    bridge=SimpleNamespace(bindings={'task':binding})
    tool=ArgusTool(SimpleNamespace(bridge=bridge,task_channels=channels),'argus_guide')
    async def run():
        overview=await tool.ainvoke({'topic':'overview'},task_id='task')
        assert overview.success and 'def main' not in str(overview.output)
        rules=await tool.ainvoke({'topic':'rules'},task_id='task')
        assert rules.success and 'def main(event, channel)' in str(rules.output)
        assert rules.output['sdk']['max_calls']==3
        assert not (await tool.ainvoke({'topic':'invented'},task_id='task')).success
        assert binding=={'wake':{'rule_sdk':{'version':2,'max_calls':3}},'finished':False}
    asyncio.run(run())
