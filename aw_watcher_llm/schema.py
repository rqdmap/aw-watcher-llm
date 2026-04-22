from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BucketSpec:
    id: str
    type: str
    client: str
    hostname: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Event:
    timestamp: str
    duration: float
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "duration": self.duration,
            "data": self.data,
        }


@dataclass(frozen=True)
class WatcherPayload:
    raw_bucket: BucketSpec
    display_bucket: BucketSpec
    raw_events: list[Event]
    display_events: list[Event]

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_bucket": self.raw_bucket.to_dict(),
            "display_bucket": self.display_bucket.to_dict(),
            "raw_events": [event.to_dict() for event in self.raw_events],
            "display_events": [event.to_dict() for event in self.display_events],
        }
