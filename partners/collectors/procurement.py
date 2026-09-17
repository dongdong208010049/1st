"""조달청 낙찰·계약 실적 수집기 — 소기업의 '확인 가능한 매출'.

나라장터 입찰·낙찰 정보는 공개다. 공시 매출이 없는 소규모 협력사도 공공부문
납품 실적은 사업자번호 단위로 확인된다. 매출 전체는 아니지만,
  - 최근 1년 공공 수주 금액·건수 → 매출 추세의 실측 대용
  - 수주가 끊긴 시점                → 영업 중단 신호
  - 부정당업자 제재                  → 리스크 명단과 교차
로 쓸 수 있다.

실 API: 공공데이터포털 조달청 낙찰정보(ScsbidInfoService)
필요 키: DATA_GO_KR_KEY
"""

import os
import random

from .base import Collector, CollectResult, CollectorError, Reading, get_json, to_float

DEFAULT_ENDPOINT = (
    "https://apis.data.go.kr/1230000/ScsbidInfoService/getScsbidListSttusThngPPSSrch"
)
MILLION = 1_000_000


class ProcurementCollector(Collector):
    source = "procurement"
    label = "조달청 낙찰실적"
    env_keys = ("DATA_GO_KR_KEY",)
    metric_codes = ("public_award", "public_award_change")

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        start = periods[0].replace("-", "") + "01"
        end = periods[-1].replace("-", "") + "28"
        payload = get_json(
            os.environ.get("PROCUREMENT_API_URL") or DEFAULT_ENDPOINT,
            {
                "serviceKey": os.environ["DATA_GO_KR_KEY"],
                "inqryDiv": "1",
                "inqryBgnDt": start,
                "inqryEndDt": end,
                "bidwinnrBizno": partner["biz_no"],
                "numOfRows": 200,
                "pageNo": 1,
                "type": "json",
            },
        )
        items = _items(payload)
        if not items:
            raise CollectorError(f"{partner['name']}: 조달 낙찰 실적이 없습니다.")

        # 월별 낙찰금액 합계 → 12개월 누계로 환산한다.
        by_period: dict[str, float] = {}
        for item in items:
            stamp = (item.get("rlOpengDt") or item.get("opengDt") or "").strip()
            amount = to_float(item.get("sucsfbidAmt") or item.get("bidwinnrAmt"))
            if len(stamp) < 7 or amount is None:
                continue
            key = stamp[:7].replace("/", "-")
            by_period[key] = by_period.get(key, 0.0) + amount
        return CollectResult(readings=_rolling_year(by_period, periods))

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        rng = random.Random(f"procurement:{partner['biz_no']}")
        profile = partner["profile"] or "healthy"
        if rng.random() < 0.25 and not profile.startswith("small"):
            return CollectResult()  # 공공납품이 없는 협력사도 있다

        base = rng.uniform(40, 260) if profile.startswith("small") else rng.uniform(200, 1800)
        drift = {
            "healthy": rng.uniform(0.2, 1.6), "small_healthy": rng.uniform(0.0, 1.4),
            "watch": rng.uniform(-1.8, -0.2), "distress": rng.uniform(-4.5, -2.0),
            "small_distress": rng.uniform(-5.5, -2.5),
        }.get(profile, rng.uniform(-6.0, -3.0))

        by_period: dict[str, float] = {}
        level = base
        for period in periods:
            level = max(0.0, level * (1 + drift / 100))
            # 공공 수주는 매월 있는 게 아니라 띄엄띄엄 들어온다.
            by_period[period] = round(level * MILLION * rng.choice((0, 0, 1, 1, 2)), 0)
        return CollectResult(readings=_rolling_year(by_period, periods))


def _rolling_year(by_period: dict[str, float], periods: list[str]) -> list[Reading]:
    """12개월 이동 누계(백만원)와 전년 대비 증감률."""
    readings: list[Reading] = []
    totals: dict[str, float] = {}
    for index, period in enumerate(periods):
        window = periods[max(0, index - 11): index + 1]
        total = sum(by_period.get(key, 0.0) for key in window) / MILLION
        totals[period] = total
        readings.append(Reading(period, "public_award", round(total, 1), text=f"{total:,.0f}백만원"))
        previous = totals.get(periods[index - 12]) if index >= 12 else None
        if previous:
            readings.append(
                Reading(period, "public_award_change", round((total - previous) / previous * 100, 1))
            )
    return readings


def _items(payload: dict) -> list[dict]:
    body = payload.get("response", {}).get("body", {})
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    return items or []
