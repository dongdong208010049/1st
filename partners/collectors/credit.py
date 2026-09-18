"""신용평가 등급 수집기(비외감 소규모 협력사의 재무 대체 경로).

DART가 커버하지 않는 소기업도 신용평가사는 등급을 산출한다(나이스디앤비,
이크레더블, 한국평가데이터 등). 실무에서는 두 가지로 들어온다.

  1) 협력사가 제출하는 기업신용평가서 → 등급을 CSV/화면으로 적재
  2) 평가사 기업정보 API 구독 → 사업자번호로 조회

이 저장소는 평가사 규격이 계약마다 달라 두 경로를 둔다.
  1) 구독 API: CREDIT_API_URL + CREDIT_API_KEY
     응답 필드명이 평가사마다 달라 CREDIT_FIELD_GRADE(기본 grade),
     CREDIT_FIELD_DATE(기본 evaluated_on)로 지정한다.
  2) CSV 적재: PARTNERS_CREDIT_CSV 또는 data/credit_grades.csv
     컬럼: biz_no,grade,evaluated_on,watch(선택),note(선택)
둘 다 없으면 목업으로 동작한다.
"""

import csv
import os
import random
from pathlib import Path

from .base import Collector, CollectResult, Event, Reading, clamp_date, get_json

DEFAULT_CSV_PATH = Path("data/credit_grades.csv")

# 기업신용등급 → 점수. 국내 평가사 공통 표기(AAA~D)를 기준으로 한다.
GRADE_SCORES = {
    "AAA": 100.0, "AA": 95.0, "A": 88.0, "BBB": 78.0, "BB": 68.0,
    "B": 58.0, "CCC": 45.0, "CC": 35.0, "C": 25.0, "D": 10.0,
    "R": 10.0,  # 등급취소·평가불가
}


class CreditCollector(Collector):
    source = "credit"
    label = "신용평가 등급"
    metric_codes = ("credit_score",)

    def available(self) -> bool:
        return bool(os.environ.get("CREDIT_API_URL") and os.environ.get("CREDIT_API_KEY")) or _csv_path().exists()

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        if os.environ.get("CREDIT_API_URL"):
            rows = _from_api(partner)
        else:
            rows = [row for row in _read_csv(_csv_path()) if row.get("biz_no") == partner["biz_no"]]
        if not rows:
            return CollectResult()

        readings: list[Reading] = []
        events: list[Event] = []
        for row in rows:
            grade = (row.get("grade") or "").strip().upper()
            score = GRADE_SCORES.get(grade)
            if score is None:
                continue
            evaluated = (row.get("evaluated_on") or "").strip()
            period = evaluated[:7] if len(evaluated) >= 7 else periods[-1]
            if period < periods[0]:
                period = periods[0]
            readings.append(Reading(period, "credit_score", score, text=f"신용 {grade}"))
            if score <= GRADE_SCORES["CCC"]:
                events.append(
                    Event(
                        kind="신용등급",
                        title=f"신용등급 {grade} (투자부적격)",
                        occurred_on=evaluated or f"{period}-01",
                        severity="warn",
                    )
                )
        return CollectResult(readings=readings, events=events)

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        rng = random.Random(f"credit:{partner['biz_no']}")
        profile = partner["profile"] or "healthy"
        grades = {
            "healthy": ("A", "BBB"),
            "small_healthy": ("BBB", "BB"),
            "watch": ("BB", "B"),
            "distress": ("CCC", "CC"),
            "small_distress": ("CC", "C"),
            "closed": ("D",),
            "suspended": ("C", "CCC"),
        }.get(profile, ("BB",))

        readings: list[Reading] = []
        events: list[Event] = []
        # 평가는 연 1회라서 시계열 중간에 한 번만 값이 생긴다.
        anchor = periods[max(0, len(periods) - 9)]
        grade = grades[0]
        readings.append(Reading(anchor, "credit_score", GRADE_SCORES[grade], text=f"신용 {grade}"))
        if len(grades) > 1 and rng.random() < 0.7:
            downgraded = grades[-1]
            recent = periods[max(0, len(periods) - 3)]
            readings.append(Reading(recent, "credit_score", GRADE_SCORES[downgraded], text=f"신용 {downgraded}"))
            events.append(
                Event(
                    kind="신용등급",
                    title=f"신용등급 하락 {grade} → {downgraded}",
                    occurred_on=clamp_date(f"{recent}-20"),
                    severity="critical" if GRADE_SCORES[downgraded] <= GRADE_SCORES["CCC"] else "warn",
                )
            )
        return CollectResult(readings=readings, events=events)


def _from_api(partner) -> list[dict]:
    """평가사 구독 API 응답을 CSV와 같은 모양으로 맞춘다."""
    payload = get_json(
        os.environ["CREDIT_API_URL"],
        {"key": os.environ["CREDIT_API_KEY"], "bizNo": partner["biz_no"]},
    )
    records = payload.get("data") or payload.get("rows") or []
    if isinstance(records, dict):
        records = [records]
    grade_field = os.environ.get("CREDIT_FIELD_GRADE", "grade")
    date_field = os.environ.get("CREDIT_FIELD_DATE", "evaluated_on")
    return [
        {
            "biz_no": partner["biz_no"],
            "grade": str(record.get(grade_field) or "").strip(),
            "evaluated_on": str(record.get(date_field) or "").strip(),
        }
        for record in records
    ]


def _csv_path() -> Path:
    return Path(os.environ.get("PARTNERS_CREDIT_CSV") or DEFAULT_CSV_PATH)


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
