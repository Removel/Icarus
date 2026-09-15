"""Blackboard 最终 User Prompt 的稳定组合器。"""

import json

from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    ContextBlock,
)


class BlackboardPromptComposer:
    """稳定拍平动态插件上下文，并追加到当前 User Prompt。"""

    def compose(
        self,
        *,
        prompt: str,
        context_blocks: list[ContextBlock],
        context_errors: dict[str, str],
        region_view: dict[str, dict] | None = None,
    ) -> str:
        return self.build_input_prompt(
            prompt=prompt,
            serialized_context=self.serialize_context(context_blocks),
            context_errors=context_errors,
            region_view=region_view or {},
        )

    def serialize_context(self, context_blocks: list[ContextBlock]) -> str:
        normalized = [
            {
                "source_plugin_id": block.source_plugin_id,
                "context_type": block.context_type,
                "content": block.content,
                "metadata": dict(block.metadata),
            }
            for block in sorted(
                context_blocks,
                key=lambda block: (
                    block.source_plugin_id,
                    block.context_type,
                    block.content,
                    json.dumps(
                        block.metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )
        ]
        if not normalized:
            return ""
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def build_input_prompt(
        prompt: str,
        serialized_context: str,
        context_errors: dict[str, str],
        region_view: dict[str, dict] | None = None,
    ) -> str:
        sections: list[str] = []
        if serialized_context:
            sections.append(
                "<plugin_context>\n"
                f"{serialized_context}\n"
                "</plugin_context>"
            )
        if context_errors:
            errors = json.dumps(
                context_errors,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            sections.append(
                "<plugin_context_errors>\n"
                f"{errors}\n"
                "</plugin_context_errors>"
            )
        if region_view:
            regions = json.dumps(
                region_view,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            sections.append(
                "<blackboard_regions>\n"
                f"{regions}\n"
                "</blackboard_regions>"
            )
        sections.append(
            "<user_request>\n"
            f"{prompt}\n"
            "</user_request>"
        )
        return "\n\n".join(sections)
