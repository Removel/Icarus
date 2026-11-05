from agent_app.plugins.prompt.base_prompt import DEFAULT_SYSTEM_PROMPT
from icarus.core.tools.result_budget import conservative_token_count


def test_base_prompt_is_compact_and_does_not_own_plugin_policy():
    assert conservative_token_count(DEFAULT_SYSTEM_PROMPT) <= 600
    assert "长期记忆" not in DEFAULT_SYSTEM_PROMPT
    assert "memory_recall" not in DEFAULT_SYSTEM_PROMPT
    assert "Icarus Agent" in DEFAULT_SYSTEM_PROMPT
    assert "授权" in DEFAULT_SYSTEM_PROMPT
    assert "验证" in DEFAULT_SYSTEM_PROMPT
    assert "普通聊天默认简短、自然、直接" in DEFAULT_SYSTEM_PROMPT


def test_base_owns_authority_and_keeps_loading_separate():
    assert "常驻工具直接调用" in DEFAULT_SYSTEM_PROMPT
    assert "每个新 Run 重新加载" in DEFAULT_SYSTEM_PROMPT
    assert "加载不代替用户授权" in DEFAULT_SYSTEM_PROMPT
    assert "读保存文件不加载" in DEFAULT_SYSTEM_PROMPT
    assert "不改变事实、工具、授权或任务要求" in DEFAULT_SYSTEM_PROMPT
    assert "档案与自我观察仅为背景" in DEFAULT_SYSTEM_PROMPT
    assert "不为扮演拒绝合理请求" in DEFAULT_SYSTEM_PROMPT
