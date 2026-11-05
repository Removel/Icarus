"""Argus adapter leaf plugin; helpers are not child plugins."""
from icarus.core.plugin import BasePlugin

class ArgusPlugin(BasePlugin):
    def __init__(self, plugin_id, bridge, task_channels):
        super().__init__(plugin_id)
        self.bridge = bridge
        self.task_channels = task_channels

    def static_prompt_sections(self):
        from agent_app.contracts.prompt import PromptSection
        from .prompts import ARGUS_GUIDANCE
        return (PromptSection('argus', 'session', ARGUS_GUIDANCE),)

    def accepts_event(self, source_plugin_id, event):
        return False

    def build_tools(self):
        from .tools import ArgusTool, TOOL_NAMES
        return tuple(ArgusTool(self, name) for name in TOOL_NAMES)
