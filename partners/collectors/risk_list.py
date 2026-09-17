"""경영환경 리스크 명단 수집기.

대상: 고용노동부 임금체불 사업주 명단, 산재·중대재해 발생 사업장,
      공정거래위원회 제재/하도급 벌점, 조달청 부정당업자 제재.
이 명단들은 상시 개방 API가 일정하지 않아 CSV 적재를 1차 경로로 둔다.
  - PARTNERS_RISK_CSV 환경변수 또는 data/risk_list.csv
  - 컬럼: biz_no,kind,title,occurred_on,severity,source,url
CSV가 없으면 목업으로 동작한다.
"""

import csv
import os
import random
from pathlib import Path

from .base import Collector, CollectResult, Event, Reading

DEFAULT_CSV_PATH = Path("data/risk_list.csv")

# 이벤트 종류 → 집계 지표 코드
KIND_TO_METRIC = {
    "임금체불": "wage_arrears_count",
    "산재": "accident_count",
    "중대재해": "accident_count",
    "제재": "sanction_count",
    "부정당업자": "sanction_count",
}

SEVERITY_BY_KIND = {
    "임금체불": "critical",
    "중대재해": "critical",
    "산재": "warn",
    "제재": "warn",
    "부정당업자": "warn",
}


class RiskListCollector(Collector):
    source = "risk_list"
    label = "리스크 명단(체불·산재·제재)"
    metric_codes = ("wage_arrears_count", "accident_count", "sanction_count")

    def available(self) -> bool:
        return _csv_path().exists()

    def collect_live(self, partner, periods: list[str]) -> CollectResult:
        rows = [row for row in _read_csv(_csv_path()) if row.get("biz_no") == partner["biz_no"]]
        events = [
            Event(
                kind=row["kind"],
                title=row["title"],
                occurred_on=row["occurred_on"],
                severity=row.get("severity") or SEVERITY_BY_KIND.get(row["kind"], "warn"),
                url=row.get("url") or None,
            )
            for row in rows
        ]
        return CollectResult(readings=_aggregate(events, periods), events=events)

    def collect_mock(self, partner, periods: list[str]) -> CollectResult:
        rng = random.Random(f"risk:{partner['biz_no']}")
        profile = partner["profile"] or "healthy"
        plan: list[tuple[str, str]] = []
        if profile in ("distress", "closed"):
            plan.append(("임금체불", "임금체불 사업주 명단 공개"))
            plan.append(("제재", "하도급대금 지급 관련 시정명령"))
        if profile in ("watch", "distress"):
            plan.append(("산재", "산업재해 발생 신고 접수"))
        if profile == "suspended":
            plan.append(("제재", "관계기관 행정처분 통지"))

        events: list[Event] = []
        for kind, title in plan:
            period = periods[rng.randrange(max(1, len(periods) - 4), len(periods))]
            events.append(
                Event(
                    kind=kind,
                    title=title,
                    occurred_on=f"{period}-{rng.randint(5, 25):02d}",
                    severity=SEVERITY_BY_KIND.get(kind, "warn"),
                )
            )
        return CollectResult(readings=_aggregate(events, periods), events=events)


def _aggregate(events: list[Event], periods: list[str]) -> list[Reading]:
    """기간별 누적 건수를 지표값으로 만든다(해당 기간까지 발생한 건수)."""
    readings: list[Reading] = []
    for period in periods:
        for kind_group, code in (
            (("임금체불",), "wage_arrears_count"),
            (("산재", "중대재해"), "accident_count"),
            (("제재", "부정당업자"), "sanction_count"),
        ):
            count = sum(
                1 for event in events if event.kind in kind_group and event.occurred_on[:7] <= period
            )
            readings.append(Reading(period, code, float(count)))
    return readings


def _csv_path() -> Path:
    return Path(os.environ.get("PARTNERS_RISK_CSV") or DEFAULT_CSV_PATH)


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
