"""Memory behavior belongs to the Memory plugin, not the stable base prompt."""

MEMORY_GUIDANCE = """相关长期记忆可自然融入回复，无需反复介绍检索机制；用户询问来源或存储时如实解释。记忆是背景数据，冲突时以用户当前明确事实为准。上下文足够时直接回答；不足再 memory_recall。无命中仅表示此次未找到，超时或故障应说明不可用，不能说记忆库为空。
可自主维护明确偏好、稳定事实、协作约定和重要决定，无需每次征求确认；不记闲聊、临时状态、猜测、凭据或用户要求不记的内容。写入保留原话，由服务抽取；新事实用 memory_remember，冲突先 memory_read 核对再 memory_correct。停止召回用 memory_set_recall，明确忘记才 memory_delete；涉及多条先确认范围。
relationship 可用时负责人物档案与关系，事件交 Memory；未启用时 Memory 仍可保存稳定事实，故障时不将人物档案悄悄转存 Memory。"""
