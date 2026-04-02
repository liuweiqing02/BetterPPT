from typing import Any

from pydantic import BaseModel, ConfigDict


class APIResponse(BaseModel):
    code: int = 0
    message: str = 'ok'
    data: Any = {}


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
