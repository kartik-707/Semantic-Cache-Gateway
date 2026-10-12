from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .decision import CacheDecision


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "developer", "user", "assistant"]
    content: str


class ProxyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)

    temperature: float = Field(
        default=0.7,
        ge=0,
        le=2,
        allow_inf_nan=False,
    )
    max_tokens: int = Field(default=1024, gt=0)

    # Prevent feature names from becoming arbitrary file paths.
    feature_name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    user_id: str | None = None
    session_id: str | None = None

    tags: list[str] = Field(default_factory=list)
    cache_disabled: bool = False
    cache_ttl_override: int | None = Field(default=None, gt=0)

    @field_validator("model", "user_id", "session_id")
    @classmethod
    def reject_blank_identifiers(cls, value: str | None):
        if value is not None and not value.strip():
            raise ValueError("Identifiers cannot be blank")
        return value


class ProxyResponse(BaseModel):
    id: str
    model: str
    choices: list[dict]
    usage: dict | None = None
    cache_meta: CacheDecision


ProxyResponse.model_rebuild()