from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NodeDescriptor:
    node_id: str
    endpoint: str
    kind: Literal["local", "http"]

