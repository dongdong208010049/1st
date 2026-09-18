"""건강보험 사업장 수집기 — 연금 자료의 교차검증.

국민연금과 내용이 겹치지만, 두 자료를 **맞대보는 것** 자체가 신호다.
같은 사업장인데 연금 가입자와 건보 가입자 수가 크게 다르면, 신고 누락이든
일용·외주 전환이든 확인이 필요하다. 건보료 체납도 연금 체납과 별개로 잡힌다.

실 API: 공공데이터포털 국민건강보험공단 사업장 정보
필요 키: DATA_GO_KR_KEY (+ 선택 HEALTH_API_URL)
"""

import os
import random

from .base import Collector, CollectResult, CollectorError, Reading, get_json, to_float

DEFAULT_ENDPOINT = "https://apis.data.go.kr/B550928/hi_wkpl_info/getWkplInfo"


class HealthInsuranceCollector(Collector):
    source = "health"
    label = "건강보험 사업장"
    env_keys = ("DATA_GO_KR_KEY",)
    metric_codes = ("health_insured", "insured_gap", "health_arrears")

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        payload = get_json(
            os.environ.get("HEALTH_API_URL") or DEFAULT_ENDPOINT,
            {
                "serviceKey": os.environ["DATA_GO_KR_KEY"],
                "bzowrRgstNo": partner["biz_no"],
                "wkplNm": partner["name"],
                "numOfRows": 10,
                "pageNo": 1,
                "type": "json",
            },
        )
        items = _items(payload)
        if not items:
            raise CollectorError(f"{partner['name']}: 건강보험 사업장 자료를 찾지 못했습니다.")

        insured = to_float(items[0].get("jnngpCnt") or items[0].get("emplyCnt"))
        latest = periods[-1]
        readings: list[Reading] = []
        if insured:
            readings.append(Reading(latest, "health_insured", insured, text=f"{insured:,.0f}명"))
        arrears = to_float(items[0].get("arrearsYn") or 0)
        readings.append(
            Reading(latest, "health_arrears", 1.0 if arrears else 0.0, text="건보 체납" if arrears else "건보 정상")
        )
        return CollectResult(readings=readings)

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        rng = random.Random(f"health:{partner['biz_no']}")
        profile = partner["profile"] or "healthy"
        # 정상이면 연금 가입자와 거의 같고, 부실이면 벌어진다(일용·외주 전환).
        ratio = {
            "healthy": rng.uniform(0.98, 1.02), "small_healthy": rng.uniform(0.96, 1.03),
            "watch": rng.uniform(0.9, 0.99),
        }.get(profile, rng.uniform(0.62, 0.85))

        readings: list[Reading] = []
        for index, period in enumerate(periods):
            pension = _headcount_at(conn, partner["id"], period)
            if pension:
                insured = max(1, round(pension * ratio))
                readings.append(Reading(period, "health_insured", float(insured), text=f"{insured}명"))
            arrears = (
                1.0 if profile in ("distress", "small_distress", "closed") and index >= len(periods) - 3
                else 0.0
            )
            readings.append(
                Reading(period, "health_arrears", arrears, text="건보 체납" if arrears else "건보 정상")
            )
        return CollectResult(readings=readings)


def fill_insured_gap(conn, models, partner_id: int, periods: list[str]) -> list[Reading]:
    """연금 대비 건보 가입자 괴리(%). 두 자료가 다 모인 뒤 계산한다."""
    readings: list[Reading] = []
    for period in periods:
        values = models.values_as_of(conn, partner_id, period)
        pension = values.get("headcount")
        health = values.get("health_insured")
        if not pension or not health or not pension["value"]:
            continue
        gap = (health["value"] - pension["value"]) / pension["value"] * 100
        readings.append(
            Reading(
                period, "insured_gap", round(gap, 1),
                text=f"{health['value']:.0f}↔{pension['value']:.0f}명",
            )
        )
    return readings


def _headcount_at(conn, partner_id: int, period: str) -> float | None:
    if conn is None:
        return None
    row = conn.execute(
        "SELECT value FROM metric_values WHERE partner_id = ? AND metric_code = 'headcount' "
        "AND period <= ? AND value IS NOT NULL ORDER BY period DESC LIMIT 1",
        (partner_id, period),
    ).fetchone()
    return row["value"] if row else None


def _items(payload: dict) -> list[dict]:
    body = payload.get("response", {}).get("body", {})
    items = body.get("items") or {}
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        return [items]
    return items or payload.get("data") or []
