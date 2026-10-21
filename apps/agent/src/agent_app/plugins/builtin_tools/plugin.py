from agent_app.contracts.prompt import PromptSection
from agent_app.plugins.builtin_tools.prompts import BUILTIN_TOOLS_GUIDANCE
from agent_app.plugins.builtin_tools.config import PluginConfig
from icarus.core.plugin import BasePlugin


class BuiltinToolsPlugin(BasePlugin):
    config_schema = PluginConfig

    def static_prompt_sections(self):
        return (PromptSection("builtin-tool-use", "session", BUILTIN_TOOLS_GUIDANCE),)

    def build_tools(self):
        return self._tools
