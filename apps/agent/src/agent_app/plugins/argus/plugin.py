"""Argus owns observation contribution; application services own reliable admission."""
from collections import OrderedDict
import asyncio

from icarus.core.plugin import BasePlugin, handles
from icarus.core.contracts.input import UserInputEvent, InputFinishedEvent
from icarus.core.contracts.context import BlackboardContextReadyEvent
from agent_app.contracts.prompt import PromptContributionEvent, PromptSection


class ArgusPlugin(BasePlugin):
    def __init__(self, plugin_id, bridge, task_channels):
        super().__init__(plugin_id)
        self.bridge = bridge
        self.task_channels = task_channels
        self._inputs: OrderedDict[str, None] = OrderedDict()
        self._watchers: dict[str,asyncio.Task] = {}
        self._signals: dict[str,asyncio.Event] = {}
        self._watermarks: dict[str,str] = {}

    def static_prompt_sections(self):
        from .prompts import ARGUS_GUIDANCE
        return (PromptSection('argus', 'session', ARGUS_GUIDANCE),)

    def accepts_event(self, source_plugin_id, event):
        if isinstance(event, (UserInputEvent, InputFinishedEvent)):
            return source_plugin_id == 'user-input'
        return isinstance(event, BlackboardContextReadyEvent) and source_plugin_id == 'blackboard'

    @handles(UserInputEvent)
    async def on_input(self, source, event):
        if source != 'user-input' or not event.task_id or event.task_id in self._inputs:
            return
        self._inputs[event.task_id] = None
        while len(self._inputs) > 128:
            self._inputs.popitem(last=False)
        binding = self.bridge.bindings.get(event.task_id)
        if not binding or binding.get('finished'):
            await self.publish(PromptContributionEvent(task_id=event.task_id,status='failed',error='argus_binding_unavailable'))
            return
        if binding.get('view_version',1) < 3:
            # Old immutable requests already carry their observations in user_request.
            await self.publish(PromptContributionEvent(task_id=event.task_id,status='completed'))
            return
        body = binding.get('observation_prompt')
        if not isinstance(body,str) or not body:
            await self.publish(PromptContributionEvent(task_id=event.task_id,status='failed',error='argus_observations_unavailable'))
            return
        binding['context_visible'] = False
        self._signals[event.task_id]=asyncio.Event()
        binding['availability_signal']=self._signals[event.task_id]
        if hasattr(self.bridge,'listeners'):
            self.bridge.listeners[event.task_id]=self.notify
        await self.publish(PromptContributionEvent(task_id=event.task_id,status='completed',sections=(
            PromptSection('observations','contributor',body),)))

    @handles(BlackboardContextReadyEvent)
    async def on_context_ready(self, source, event):
        if source != 'blackboard':
            return
        binding = self.bridge.bindings.get(event.task_id)
        if binding and binding.get('view_version',1) >= 3:
            body = binding.get('observation_prompt')
            from .presentation import observations_visible
            binding['context_visible'] = observations_visible(body or '',event.input_prompt)
            if (binding['context_visible'] and event.task_id in self._signals and event.task_id not in self._watchers
                    and self._background_work_starter is not None):
                from .notifications import watch
                self._watchers[event.task_id]=self.start_background_work(
                    lambda:watch(self,event.task_id,binding,self._signals[event.task_id]),name='argus-availability:'+event.task_id)

    def notify(self, task_id, watermark):
        signal=self._signals.get(task_id)
        binding=self.bridge.bindings.get(task_id)
        channel=self.task_channels.get(task_id)
        from icarus.core.run_control import TaskChannelStatus
        if (signal is None or not binding or binding.get('finished') or channel is None
                or channel.status not in {TaskChannelStatus.ACCEPTED,TaskChannelStatus.PREPARING_CONTEXT,TaskChannelStatus.RUNNING}):
            return False
        if self._watermarks.get(task_id)!=watermark:
            self._watermarks[task_id]=watermark
            signal.set()
        return True

    async def _stop_watcher(self,task_id):
        task=self._watchers.pop(task_id,None)
        if task:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        signal=self._signals.pop(task_id,None)
        self._watermarks.pop(task_id,None)
        if hasattr(self.bridge,'listeners'):
            self.bridge.listeners.pop(task_id,None)
        binding=self.bridge.bindings.get(task_id)
        if binding:
            binding.pop('availability_signal',None)

    async def quiesce(self):
        for task_id in tuple(self._signals):
            await self._stop_watcher(task_id)

    async def stop(self):
        await self.quiesce()

    @handles(InputFinishedEvent)
    async def on_finished(self, source, event):
        if source != 'user-input':
            return
        self._inputs.pop(event.task_id,None)
        await self._stop_watcher(event.task_id)

    def build_tools(self):
        from .tools import ArgusTool, TOOL_NAMES
        return tuple(ArgusTool(self, name) for name in TOOL_NAMES)
