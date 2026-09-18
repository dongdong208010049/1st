"""공장등록현황 수집기 — 소규모 제조 협력사 정보의 가장 넓은 공개 경로.

제조업체가 공장을 등록하면(산업집적활성화법) 공장등록대장이 만들어지고,
그 내용이 팩토리온·한국산업단지공단을 통해 공개된다. **외부감사 대상이 아니어도,
공시가 없어도** 아래가 확인된다.

  - 대표자명, 소재지(도로명 주소)        → 소규모 협력사의 대표자·주소 공백을 메운다
  - 용지면적·건축면적(㎡)                 → 설비 규모(매출 추정의 대용)
  - 등록 종업원수                         → 연금 가입자수와 교차검증(불일치 = 신고 왜곡 신호)
  - 생산품목                              → 주요 생산품
  - 등록구분(등록/취소)                   → **등록 취소는 공장 가동 중단 신호**

실 API: 공공데이터포털 한국산업단지공단 공장등록현황
        https://apis.data.go.kr/B552584/... (기관별 엔드포인트가 달라 환경변수로 받는다)
필요 키: DATA_GO_KR_KEY (+ 선택 FACTORY_API_URL)
"""

import os
import random

from .base import (
    Collector, CollectResult, CollectorError, Event, ProfileFact, Reading,
    clamp_date, get_json, to_float,
)

DEFAULT_ENDPOINT = "https://apis.data.go.kr/B552584/FactoryRegistrationService/getFactoryList"

# 등록구분 → (지표값, 표시)
STATUS_MAP = {"등록": (0.0, "공장 등록"), "취소": (2.0, "공장등록 취소"), "말소": (2.0, "공장등록 말소")}


class FactoryCollector(Collector):
    source = "factory"
    label = "공장등록현황"
    env_keys = ("DATA_GO_KR_KEY",)
    metric_codes = ("plant_area", "plant_workers", "factory_status", "worker_gap")

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        payload = get_json(
            os.environ.get("FACTORY_API_URL") or DEFAULT_ENDPOINT,
            {
                "serviceKey": os.environ["DATA_GO_KR_KEY"],
                "cmpnyNm": partner["name"],
                "bizrno": partner["biz_no"],
                "numOfRows": 10,
                "pageNo": 1,
                "type": "json",
            },
        )
        items = _items(payload)
        if not items:
            raise CollectorError(f"{partner['name']}: 공장등록 자료를 찾지 못했습니다.")

        record = items[0]
        latest = periods[-1]
        readings: list[Reading] = []
        events: list[Event] = []

        area = to_float(record.get("lndArea") or record.get("useAreaSum"))
        if area:
            readings.append(Reading(latest, "plant_area", area, text=f"{area:,.0f}㎡"))
        workers = to_float(record.get("wrkrCnt") or record.get("emplyCnt"))
        if workers:
            readings.append(Reading(latest, "plant_workers", workers, text=f"{workers:,.0f}명"))

        status_raw = (record.get("regstrType") or record.get("fctryStts") or "").strip()
        value, label = STATUS_MAP.get(status_raw, (None, status_raw or None))
        if value is not None:
            readings.append(Reading(latest, "factory_status", value, text=label))
            if value >= 2.0:
                events.append(
                    Event(
                        kind="공장등록",
                        title=f"{label} 확인",
                        occurred_on=clamp_date(f"{latest}-01"),
                        severity="critical",
                    )
                )

        profiles = [
            ProfileFact("ceo_name", (record.get("rprsntvNm") or "").strip() or None),
            ProfileFact("address", (record.get("fctryAddr") or record.get("rdnmadr") or "").strip() or None),
            ProfileFact("main_product", (record.get("prdctnItm") or "").strip() or None),
        ]
        return CollectResult(
            readings=readings, events=events, profiles=[p for p in profiles if p.value]
        )

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        rng = random.Random(f"factory:{partner['biz_no']}")
        profile = partner["profile"] or "healthy"
        small = profile.startswith("small")
        area = rng.uniform(900, 3200) if small else rng.uniform(4000, 26000)
        # 공장등록 종업원수는 연금 가입자수와 비슷해야 정상이다. 이미 수집된
        # 연금 인원을 기준으로 만들고, 부실 프로필에서만 괴리를 크게 둔다.
        insured = _latest_headcount(conn, partner["id"])
        ratio = {
            "healthy": rng.uniform(0.95, 1.08), "small_healthy": rng.uniform(0.92, 1.1),
            "watch": rng.uniform(1.05, 1.2),
        }.get(profile, rng.uniform(1.35, 1.9))
        if insured:
            workers = max(1, round(insured * ratio))
        else:
            workers = rng.randint(5, 26) if small else rng.randint(35, 210)
        cancelled = profile in ("closed",)

        readings: list[Reading] = []
        events: list[Event] = []
        flip_at = len(periods) - rng.randint(1, 3)
        for index, period in enumerate(periods):
            readings.append(Reading(period, "plant_area", round(area, 0), text=f"{area:,.0f}㎡"))
            readings.append(Reading(period, "plant_workers", float(workers), text=f"{workers}명"))
            revoked = cancelled and index >= flip_at
            readings.append(
                Reading(
                    period, "factory_status", 2.0 if revoked else 0.0,
                    text="공장등록 취소" if revoked else "공장 등록",
                )
            )
            if revoked and index == flip_at:
                events.append(
                    Event(
                        kind="공장등록", title="공장등록 취소 확인",
                        occurred_on=clamp_date(f"{period}-12"), severity="critical",
                    )
                )
        profiles = [ProfileFact("main_product", None)]  # 생산품은 시드/수동 값을 유지한다
        return CollectResult(readings=readings, events=events, profiles=[p for p in profiles if p.value])


def _latest_headcount(conn, partner_id: int) -> float | None:
    if conn is None:
        return None
    row = conn.execute(
        "SELECT value FROM metric_values WHERE partner_id = ? AND metric_code = 'headcount' "
        "AND value IS NOT NULL ORDER BY period DESC LIMIT 1",
        (partner_id,),
    ).fetchone()
    return row["value"] if row else None


def fill_worker_gap(conn, models, partner_id: int, periods: list[str]) -> list[Reading]:
    """공장등록 종업원수 대비 연금 가입자수 괴리(%).

    공장에는 30명으로 등록해 두고 연금은 8명만 신고하는 식의 불일치는
    인건비 미신고·가동 축소 어느 쪽이든 확인이 필요한 신호다.
    """
    readings: list[Reading] = []
    for period in periods:
        values = models.values_as_of(conn, partner_id, period)
        plant = values.get("plant_workers")
        insured = values.get("headcount")
        if not plant or not insured or not plant["value"]:
            continue
        gap = (insured["value"] - plant["value"]) / plant["value"] * 100
        readings.append(
            Reading(
                period, "worker_gap", round(gap, 1),
                text=f"{insured['value']:.0f}↔{plant['value']:.0f}명",
            )
        )
    return readings


def _items(payload: dict) -> list[dict]:
    body = payload.get("response", {}).get("body", {})
    items = (body.get("items") or {})
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        return [items]
    return items or payload.get("data") or []
