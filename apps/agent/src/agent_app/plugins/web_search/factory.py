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
                Message("system", [TextPart("你是检索查询规划器。将问题改写为3~5条互补检索式，覆盖定义、数据、对比和官方来源。"
                    "涉及时效请依据当前时间带年份。只输出严格JSON字符串数组，不要解释或代码围栏。")]),
                Message("user", [TextPart(clock + "\n\n" + question)]),
            ], None)
            return "".join(p.text for p in response.message.content if isinstance(p, TextPart))
        finally:
            await llm.aclose()
    backend = SearchHttpBackend(settings.endpoint, os.getenv(settings.api_key_env, ""))
    return PluginRegistration(plugin=WebSearchPlugin(plugin_id, backend=backend, planner=plan,
        task_channels=config.get("task_channels"), deadline_seconds=settings.deadline_seconds))
