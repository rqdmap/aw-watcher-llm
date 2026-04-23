from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.parse import urljoin
from urllib.parse import urlsplit
from urllib.request import ProxyHandler
from urllib.request import Request
from urllib.request import build_opener
from urllib.request import urlopen

from .schema import BucketEvents
from .schema import BucketSpec
from .schema import Event
from .schema import WatcherPayload


class ActivityWatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PushSummary:
    raw_inserted: int
    raw_deleted: int
    session_bucket_count: int = 0
    session_inserted: int = 0
    session_deleted: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "raw_inserted": self.raw_inserted,
            "raw_deleted": self.raw_deleted,
            "session_bucket_count": self.session_bucket_count,
            "session_inserted": self.session_inserted,
            "session_deleted": self.session_deleted,
        }


@dataclass(frozen=True)
class BucketBatchSummary:
    bucket_count: int
    events_inserted: int
    events_deleted: int

    def to_dict(self) -> dict[str, int]:
        return {
            "bucket_count": self.bucket_count,
            "events_inserted": self.events_inserted,
            "events_deleted": self.events_deleted,
        }


class ActivityWatchTransport:
    def __init__(self, base_url: str = "http://127.0.0.1:5600", timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds

    def create_bucket(self, bucket: BucketSpec) -> None:
        self._request(
            "POST",
            f"api/0/buckets/{quote(bucket.id, safe='')}",
            data={
                "client": bucket.client,
                "hostname": bucket.hostname,
                "type": bucket.type,
            },
            ok_statuses={200, 201, 304},
        )

    def get_info(self) -> dict[str, Any]:
        response = self._request(
            "GET",
            "api/0/info",
            ok_statuses={200},
        )
        if not isinstance(response, dict):
            raise ActivityWatchError("unexpected info response from ActivityWatch server")
        return response

    def get_events(
        self,
        bucket_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        response = self._request(
            "GET",
            f"api/0/buckets/{quote(bucket_id, safe='')}/events",
            params=params or None,
            ok_statuses={200},
        )
        if not isinstance(response, list):
            raise ActivityWatchError(f"unexpected events response for bucket {bucket_id!r}")
        return response

    def insert_events(self, bucket_id: str, events: list[Event]) -> None:
        if not events:
            return
        self._request(
            "POST",
            f"api/0/buckets/{quote(bucket_id, safe='')}/events",
            data=[event.to_dict() for event in events],
            ok_statuses={200, 201},
        )

    def delete_event(self, bucket_id: str, event_id: int | str) -> None:
        self._request(
            "DELETE",
            f"api/0/buckets/{quote(bucket_id, safe='')}/events/{quote(str(event_id), safe='')}",
            ok_statuses={200, 204},
        )

    def delete_events_in_range(self, bucket_id: str, *, start: str, end: str) -> int:
        events = self.get_events(bucket_id, start=start, end=end)
        deleted = 0
        for event in events:
            event_id = event.get("id")
            if event_id is None:
                continue
            self.delete_event(bucket_id, event_id)
            deleted += 1
        return deleted

    def push_payload(
        self,
        payload: WatcherPayload,
        *,
        replace_start: str | None = None,
        replace_end: str | None = None,
    ) -> PushSummary:
        raw_inserted, raw_deleted = self._push_bucket_events(
            payload.raw_bucket,
            payload.raw_events,
            replace_start=replace_start,
            replace_end=replace_end,
        )
        session_summary = self.push_bucket_events_batch(
            payload.session_buckets,
            replace_start=replace_start,
            replace_end=replace_end,
        )

        return PushSummary(
            raw_inserted=raw_inserted,
            raw_deleted=raw_deleted,
            session_bucket_count=session_summary.bucket_count,
            session_inserted=session_summary.events_inserted,
            session_deleted=session_summary.events_deleted,
        )

    def push_bucket_events_batch(
        self,
        bucket_events: list[BucketEvents],
        *,
        replace_start: str | None = None,
        replace_end: str | None = None,
    ) -> BucketBatchSummary:
        inserted = 0
        deleted = 0
        for item in bucket_events:
            item_inserted, item_deleted = self._push_bucket_events(
                item.bucket,
                item.events,
                replace_start=replace_start,
                replace_end=replace_end,
            )
            inserted += item_inserted
            deleted += item_deleted
        return BucketBatchSummary(
            bucket_count=len(bucket_events),
            events_inserted=inserted,
            events_deleted=deleted,
        )

    def _push_bucket_events(
        self,
        bucket: BucketSpec,
        events: list[Event],
        *,
        replace_start: str | None = None,
        replace_end: str | None = None,
    ) -> tuple[int, int]:
        deleted = 0
        self.create_bucket(bucket)
        if replace_start and replace_end:
            deleted = self.delete_events_in_range(
                bucket.id,
                start=replace_start,
                end=replace_end,
            )
        self.insert_events(bucket.id, events)
        return len(events), deleted

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: Any = None,
        params: dict[str, str] | None = None,
        ok_statuses: set[int] | None = None,
    ) -> Any:
        ok = ok_statuses or {200}
        url = urljoin(self.base_url, path)
        if params:
            url = f"{url}?{urlencode(params)}"
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
        request = Request(
            url=url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Charset": "utf-8",
            },
            method=method,
        )

        try:
            with self._open(request, url) as response:
                status = response.getcode()
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in ok:
                raise ActivityWatchError(f"{method} {url} failed with {exc.code}: {detail}") from exc
            status = exc.code
            raw = detail.encode("utf-8")
        except OSError as exc:
            raise ActivityWatchError(f"{method} {url} failed: {exc}") from exc

        if status not in ok:
            raise ActivityWatchError(f"{method} {url} returned unexpected status {status}")
        if not raw:
            return None
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _open(self, request: Request, url: str):
        if _should_bypass_proxies(url):
            opener = build_opener(ProxyHandler({}))
            return opener.open(request, timeout=self.timeout_seconds)
        return urlopen(request, timeout=self.timeout_seconds)


def local_day_bounds(target_date: date) -> tuple[datetime, datetime]:
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(target_date, time.min, tzinfo=local_tz)
    end = start + timedelta(days=1)
    return start, end


def default_local_hostname() -> str:
    return socket.gethostname()


def _should_bypass_proxies(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return hostname in {"127.0.0.1", "localhost", "::1"} or hostname.endswith(".localhost")
