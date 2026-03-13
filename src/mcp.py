from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_TIMEOUT = 30000


@dataclass
class Resource:
    name: str
    client: str
    url: Optional[str]
    description: Optional[str] = None
    mimeType: Optional[str] = None


class StatusConnected(BaseModel):
    status: Literal["connected"]


class StatusDisabled(BaseModel):
    status: Literal["disabled"]


class StatusFailed(BaseModel):
    status: Literal["failed"]
    error: str


class StatusNeedsAuth(BaseModel):
    status: Literal["needs_auth"]


class StatusNeedsClientRegistration(BaseModel):
    status: Literal["needs_client_registration"]
    error: str


Status = Union[
    StatusConnected,
    StatusDisabled,
    StatusFailed,
    StatusNeedsAuth,
    StatusNeedsClientRegistration,
]


class McpAuth(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    clientId: Optional[str] = None
    clientSecret: Optional[str] = None
    scope: Optional[str] = None


class McpLocal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: Literal["local"]
    command: list[str]
    environment: Optional[dict[str, str]] = None
    enabled: Optional[bool] = None
    timeout: Optional[int] = None


class McpRemote(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: Literal["remote"]
    url: str
    # Streamable HTTP is the current MCP-recommended transport. We still allow
    # explicit SSE because many existing servers expose `/sse` endpoints.
    transport: Optional[Literal["streamable_http", "sse"]] = None
    oauth: Optional[Union[McpAuth, Literal[False]]] = None
    headers: Optional[dict[str, str]] = None
    enabled: Optional[bool] = None
    timeout: Optional[int] = None


Mcp = Annotated[Union[McpLocal, McpRemote], Field(discriminator="type")]
