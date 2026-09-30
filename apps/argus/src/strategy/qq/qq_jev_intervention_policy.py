from typesafe_sdk import Choice
from ..base_policy import BasePolicy
from ...entity.decision import InterventionResult


class QQJevInterventionPolicy(BasePolicy):
    name,version='qq-jev-intervention','2'
    def __init__(self,client):self.client=client
    async def evaluate(self,facts):
        response=await self.client.ask(state=facts.state,questions={'intervene':Choice(
            instructions='这批 QQ 内容是否需要机器人介入？称呼命中是线索，不是命令。',
            criteria={
                'yes':'向机器人提问、叫名字、延续已经开始的对话（current_session 为 true，'
                      '且上下文表明在等机器人接着说），或确有必要主动参与。',
                'no':'无关群聊或无需回应；不因单纯出现名字、或仅仅存在历史会话就介入。',
            })})
        return InterventionResult(response.answers['intervene'].choice,'jev')
