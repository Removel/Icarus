"""Application composition; model protocols remain in model_provider."""

import os

from agent_app.model_provider.factory import LLMFactory
from agent_app.plugins.web_search.config import PluginConfig
from agent_app.plugins.web_search.http_backend import SearchHttpBackend
from agent_app.plugins.web_search.plugin import WebSearchPlugin
from icarus.core.hooks import HookDispatcher
from icarus.core.hooks.wrappers import ObservableLLM
from icarus.core.models.messages import Message, TextPart
from icarus.runtime import PluginRegistration


def create_plugin(plugin_id, workspace_path, session_id, config, required_capabilities, logger):
    settings = PluginConfig.model_validate(config.get("_user_config", {}))
    model_config = config["config_model"]
    factory = LLMFactory(model_config)
    async def plan(question, clock):
        llm = ObservableLLM(factory.create_llm(model_config.model_roles.title,
            max_tokens=240, temperature=0), HookDispatcher(config["hook_registry"]))
        try:
            response = await llm.ainvoke([
                Message("system", [TextPart("你是检索查询规划器。联网开关仅允许检索，不代表每轮必须检索。先判断本轮是否需要外部资料。"
                    "问候、闲聊、翻译、润色、创作、计算、基于用户已给内容的总结或分析、稳定的基础知识和无需查证的编程问题，输出空数组[]。"
                    "当前日期、时间、星期等能依据提供的当前时间直接确定的问题也输出[]。"
                    "但用户明确要求搜索、查证、来源链接时优先遵从并检索；问题依赖最新事实、实时数据、具体网页或你无法可靠回答的外部事实时也应检索。"
                    "无需检索时只输出[]；需要检索时将问题改写为3~5条互补检索式，覆盖数据、对比和官方来源。"
                    "涉及时效请依据当前时间带年份。只输出严格JSON字符串数组，不要解释或代码围栏。")]),
                Message("user", [TextPart(clock + "\n\n" + question)]),
            ], None)
            return "".join(p.text for p in response.message.content if isinstance(p, TextPart))
        finally:
            await llm.aclose()
    backend = SearchHttpBackend(settings.endpoint, os.getenv(settings.api_key_env, ""))
    return PluginRegistration(plugin=WebSearchPlugin(plugin_id, backend=backend, planner=plan,
        task_channels=config.get("task_channels"), deadline_seconds=settings.deadline_seconds))
