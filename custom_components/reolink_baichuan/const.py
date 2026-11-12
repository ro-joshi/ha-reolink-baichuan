from __future__ import annotations

from typing import Final

DOMAIN: Final = "reolink_baichuan"
DEFAULT_PORT: Final = 9000

STREAM_TYPES: Final[tuple[tuple[str, str, int, str], ...]] = (
    ("sub", "Fluent", 1, "subStream"),
    ("main", "Clear", 0, "mainStream"),
)
