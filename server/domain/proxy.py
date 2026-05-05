from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ProxyRequest:
    method: str
    path: str
    query_string: bytes
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class ProxyResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
