"""First QQ wake/silence policy for Jev."""

from typesafe_sdk import Choice


class QQStrategy:
    version = "qq-wake-v3"
    questions = {
        "should_wake": Choice(
            instructions="这条 QQ 消息是否需要机器人介入对话并回复？",
            criteria={
                "wake": (
                    "is_direct 为 true 的私聊中，对方单独发给机器人、需要回应的内容；"
                    "群聊中直接提及或 @ 机器人（mentioned 为 true，或 name_mentioned 为 true "
                    "即在纯文本里叫机器人的名字）、向机器人提问，或延续与机器人已经开始的对话。"
                ),
                "silence": (
                    "群聊中与机器人无关的聊天、机器人自己发出的消息、"
                    "纯表情或没有实际内容的推送。"
                ),
            },
        )
    }
