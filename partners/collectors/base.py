"""수집기 공통 인터페이스.

수집기는 '정부/공공 자료 1종'을 담당하며, 지표 코드 몇 개를 채운다.
API 키가 있으면 실 API(live), 없으면 목업(mock)으로 동작한다.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date

HTTP_TIMEOUT_SECONDS = 15


class CollectorError(RuntimeError):
    """외부 자료를 가져오지 못했을 때 발생한다."""


@dataclass
class Reading:
    """지표 1개의 특정 기간 측정값."""

    period: str
    code: str
    value: float | None
    text: str | None = None


@dataclass
class Event:
    """리스크 이벤트(경영환경 사건)."""

    kind: str
    title: str
    occurred_on: str
    severity: str = "info"
    url: str | None = None


@dataclass
class ProfileFact:
    """기업개요 항목 하나(주소·대표자·설립일 등). 값이 바뀌면 이력으로 쌓인다."""

    code: str
    value: str | None
    valid_from: str | None = None


@dataclass
class CollectResult:
    readings: list[Reading] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    profiles: list[ProfileFact] = field(default_factory=list)


class Collector:
    source = ""
    label = ""
    env_keys: tuple[str, ...] = ()
    metric_codes: tuple[str, ...] = ()

    def available(self) -> bool:
        """실 API 호출에 필요한 키가 모두 있는지."""
        return all(os.environ.get(key) for key in self.env_keys)

    def collect(self, partner, periods: list[str], conn=None) -> CollectResult:
        if self.available():
            return self.collect_live(partner, periods, conn)
        return self.collect_mock(partner, periods, conn)

    def mode(self) -> str:
        return "live" if self.available() else "mock"

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        raise NotImplementedError

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        raise NotImplementedError


def get_json(url: str, params: dict | None = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CollectorError(f"{url} 호출 실패: {exc}") from exc


def post_json(url: str, payload: dict, params: dict | None = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CollectorError(f"{url} 호출 실패: {exc}") from exc


def clamp_date(value: str) -> str:
    """오늘 이후 날짜는 오늘로 맞춘다(미래에 일어난 사건은 없다)."""
    today = date.today().isoformat()
    return min(value, today) if value else today


def to_float(raw) -> float | None:
    """'1,234,000' 같은 공공데이터 문자열 숫자를 float으로."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = str(raw).replace(",", "").replace(" ", "").strip()
    if cleaned in ("", "-", "--"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None
