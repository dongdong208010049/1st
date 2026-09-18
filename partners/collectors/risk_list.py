"""경영환경 리스크 명단 수집기.

대상(규모와 무관하게 공개되는 사건들 — 소규모 협력사 감시의 핵심 축):
  - 고용노동부 임금체불 사업주 명단, 산재·중대재해 발생 사업장
  - 공정거래위원회 제재/하도급 벌점, 조달청 부정당업자 제재
  - 법원 회생·파산 사건 공고 (대한민국 법원 공고)
  - 부도(당좌거래정지), 공장·부동산 경매·공매 개시 (법원경매·온비드)
  - 국세·지방세 체납 (고액상습체납자 공개, 납세증명서 미발급)
  - 법인등기 변동 (해산·청산, 대표자 변경, 본점 이전 — 등기 열람 후 적재)
  - 환경·안전 위반 (환경부 위반사업장, 작업중지·조업정지 처분)
소기업은 재무 공시가 없어도 위 사건은 그대로 드러난다. 부도·회생·경매 개시는
재무제표보다 빠르고 확실한 신호라서 즉시위험으로 처리한다.
이 명단들은 상시 개방 API가 일정하지 않아 CSV 적재를 1차 경로로 둔다.
  - PARTNERS_RISK_CSV 환경변수 또는 data/risk_list.csv
  - 컬럼: biz_no,kind,title,occurred_on,severity,source,url
CSV가 없으면 목업으로 동작한다.
"""

import csv
import os
import random
from pathlib import Path

from .base import Collector, CollectResult, Event, Reading, clamp_date

DEFAULT_CSV_PATH = Path("data/risk_list.csv")

# 이벤트 종류 → 집계 지표 코드
KIND_TO_METRIC = {
    "임금체불": "wage_arrears_count",
    "산재": "accident_count",
    "중대재해": "accident_count",
    "제재": "sanction_count",
    "부정당업자": "sanction_count",
    "회생": "legal_count",
    "파산": "legal_count",
    "부도": "legal_count",
    "경매": "legal_count",
    "공매": "legal_count",
    "체납": "legal_count",
    "해산": "legal_count",
    "등기변동": "sanction_count",
    "환경위반": "sanction_count",
    "조업정지": "legal_count",
}

SEVERITY_BY_KIND = {
    "임금체불": "critical",
    "중대재해": "critical",
    "회생": "critical",
    "파산": "critical",
    "부도": "critical",
    "경매": "critical",
    "공매": "warn",
    "체납": "warn",
    "해산": "critical",
    "조업정지": "critical",
    "등기변동": "info",
    "환경위반": "warn",
    "산재": "warn",
    "제재": "warn",
    "부정당업자": "warn",
}


class RiskListCollector(Collector):
    source = "risk_list"
    label = "리스크 명단(체불·산재·제재)"
    metric_codes = ("wage_arrears_count", "accident_count", "sanction_count", "legal_count")

    def available(self) -> bool:
        return _csv_path().exists()

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
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

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
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
        if profile == "small_distress":
            # 소규모 부실은 재무 공시가 아니라 이런 사건으로 먼저 드러난다.
            plan.append(("부도", "당좌거래정지(어음 부도) 확인"))
            plan.append(("경매", "공장 부동산 임의경매 개시결정"))
            plan.append(("체납", "국세 체납으로 납세증명서 미발급"))

        events: list[Event] = []
        for kind, title in plan:
            period = periods[rng.randrange(max(1, len(periods) - 4), len(periods))]
            events.append(
                Event(
                    kind=kind,
                    title=title,
                    occurred_on=clamp_date(f"{period}-{rng.randint(5, 25):02d}"),
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
            (("제재", "부정당업자", "등기변동", "환경위반"), "sanction_count"),
            (("회생", "파산", "부도", "경매", "공매", "체납", "해산", "조업정지"), "legal_count"),
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
