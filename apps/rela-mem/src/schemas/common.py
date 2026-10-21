from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
PersonId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
AttrKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$", max_length=128)]
Kind = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
SourceRef = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
