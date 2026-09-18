"""내부 구매·납기 데이터 수집기 — 소규모 협력사에서 가장 빠른 신호.

공공데이터는 아무리 모아도 한두 달 늦는다. 정작 가장 먼저 알 수 있는 곳은
우리 자신의 거래 기록이다. 협력사가 흔들리기 시작하면 아래가 먼저 움직인다.

  - 납기 준수율이 떨어진다(자재를 못 사거나 인력이 빠져서)
  - 선급금·조기결제를 요청한다(현금이 마른 직접 신호)
  - 수입검사 불량률이 오른다(숙련공 이탈·설비 정비 중단)
  - 우리 발주 의존도가 높다(우리가 발주를 줄이면 그대로 무너진다)

적재 경로
  1) ERP/구매시스템 연동: PARTNERS_ERP_URL(+ PARTNERS_ERP_TOKEN)에서 JSON을 받는다
  2) CSV 적재: PARTNERS_INTERNAL_CSV 또는 data/internal_trade.csv
     컬럼: biz_no,period,order_amount,otd_rate,reject_rate,prepay_requests,dependency_ratio
  둘 다 없으면 목업으로 동작한다.
"""

import csv
import os
import random
from pathlib import Path

from .base import Collector, CollectResult, CollectorError, Event, Reading, clamp_date, get_json, to_float

DEFAULT_CSV_PATH = Path("data/internal_trade.csv")


class InternalTradeCollector(Collector):
    source = "internal"
    label = "내부 구매·납기"
    metric_codes = (
        "otd_rate", "reject_rate", "prepay_requests", "dependency_ratio", "order_change_3m",
    )

    def available(self) -> bool:
        return bool(os.environ.get("PARTNERS_ERP_URL")) or _csv_path().exists()

    def mode(self) -> str:
        if os.environ.get("PARTNERS_ERP_URL"):
            return "live"
        return "live" if _csv_path().exists() else "mock"

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        rows = (
            _from_erp(partner, periods)
            if os.environ.get("PARTNERS_ERP_URL")
            else [row for row in _read_csv(_csv_path()) if row.get("biz_no") == partner["biz_no"]]
        )
        if not rows:
            raise CollectorError(f"{partner['name']}: 내부 거래 자료가 없습니다.")

        by_period = {row["period"]: row for row in rows if row.get("period")}
        readings: list[Reading] = []
        events: list[Event] = []
        orders: dict[str, float] = {}

        for period in periods:
            row = by_period.get(period)
            if not row:
                continue
            orders[period] = to_float(row.get("order_amount")) or 0.0
            readings.extend(_row_readings(period, row))
            prepay = to_float(row.get("prepay_requests")) or 0.0
            if prepay >= 1:
                events.append(
                    Event(
                        kind="선급금 요청",
                        title=f"선급금·조기결제 요청 {prepay:.0f}건",
                        occurred_on=clamp_date(f"{period}-15"),
                        severity="warn",
                    )
                )
        readings.extend(_order_change(orders, periods))
        return CollectResult(readings=readings, events=events)

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        rng = random.Random(f"internal:{partner['biz_no']}")
        profile = partner["profile"] or "healthy"

        otd = {"healthy": 99.0, "small_healthy": 98.0, "watch": 95.0}.get(profile, 88.0)
        reject = {"healthy": 0.4, "small_healthy": 0.7, "watch": 1.8}.get(profile, 4.2)
        otd_drift = {"healthy": 0.0, "small_healthy": 0.0, "watch": -0.15}.get(profile, -0.55)
        reject_drift = {"healthy": 0.0, "small_healthy": 0.01, "watch": 0.05}.get(profile, 0.18)
        dependency = rng.uniform(12, 38) if not profile.startswith("small") else rng.uniform(45, 82)
        order = rng.uniform(80, 900) if not profile.startswith("small") else rng.uniform(12, 90)
        order_drift = {
            "healthy": rng.uniform(0.1, 1.2), "small_healthy": rng.uniform(0.0, 1.0),
            "watch": rng.uniform(-1.4, -0.2), "distress": rng.uniform(-4.0, -1.8),
            "small_distress": rng.uniform(-5.0, -2.2),
        }.get(profile, rng.uniform(-6.0, -3.0))

        readings: list[Reading] = []
        events: list[Event] = []
        orders: dict[str, float] = {}
        for index, period in enumerate(periods):
            otd = max(55.0, min(100.0, otd + otd_drift + rng.uniform(-0.4, 0.4)))
            reject = min(8.5, max(0.05, reject + reject_drift + rng.uniform(-0.05, 0.05)))
            order = max(0.0, order * (1 + order_drift / 100))
            orders[period] = order

            readings.append(Reading(period, "otd_rate", round(otd, 1)))
            readings.append(Reading(period, "reject_rate", round(reject, 2)))
            readings.append(Reading(period, "dependency_ratio", round(dependency, 1)))

            # 현금이 마르면 선급금 요청이 나온다. 마지막 몇 달에만.
            prepay = 0.0
            if profile in ("distress", "small_distress", "closed") and index >= len(periods) - 4:
                prepay = float(rng.randint(1, 3))
            readings.append(Reading(period, "prepay_requests", prepay))
            if prepay:
                events.append(
                    Event(
                        kind="선급금 요청",
                        title=f"선급금·조기결제 요청 {prepay:.0f}건",
                        occurred_on=clamp_date(f"{period}-{rng.randint(6, 24):02d}"),
                        severity="warn",
                    )
                )
        readings.extend(_order_change(orders, periods))
        return CollectResult(readings=readings, events=events)


def _row_readings(period: str, row: dict) -> list[Reading]:
    readings = []
    for code, key in (
        ("otd_rate", "otd_rate"), ("reject_rate", "reject_rate"),
        ("prepay_requests", "prepay_requests"), ("dependency_ratio", "dependency_ratio"),
    ):
        value = to_float(row.get(key))
        if value is not None:
            readings.append(Reading(period, code, value))
    return readings


def _order_change(orders: dict[str, float], periods: list[str]) -> list[Reading]:
    """3개월 발주 증감률. 우리 발주가 줄어든 것인지 그쪽이 못 받는 것인지는
    사람이 판단해야 하므로 점수에는 넣지 않고(가중치 0) 흐름만 보여준다."""
    readings: list[Reading] = []
    for index in range(3, len(periods)):
        period = periods[index]
        current, base = orders.get(period), orders.get(periods[index - 3])
        if current is None or not base:
            continue
        readings.append(
            Reading(period, "order_change_3m", round((current - base) / base * 100, 1))
        )
    return readings


def _from_erp(partner, periods: list[str]) -> list[dict]:
    payload = get_json(
        os.environ["PARTNERS_ERP_URL"],
        {
            "biz_no": partner["biz_no"],
            "from": periods[0],
            "to": periods[-1],
            "token": os.environ.get("PARTNERS_ERP_TOKEN", ""),
        },
    )
    return payload.get("rows") or payload.get("data") or []


def _csv_path() -> Path:
    return Path(os.environ.get("PARTNERS_INTERNAL_CSV") or DEFAULT_CSV_PATH)


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
