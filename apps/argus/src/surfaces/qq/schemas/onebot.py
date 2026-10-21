"""OneBot event boundary. Unknown event metadata is intentionally tolerated."""

from pydantic import BaseModel, ConfigDict


class OneBotEvent(BaseModel):
    model_config = ConfigDict(extra='allow')
    self_id: int | str
    post_type: str
