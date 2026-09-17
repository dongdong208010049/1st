"""국민연금·고용보험 사업장 가입 현황 수집기.

소규모(비외감) 협력사 모니터링의 1순위 소스다. DART는 상장·외부감사대상 법인만
공시하지만, 국민연금 사업장 자료는 **근로자 1인 이상이면 규모와 무관하게** 존재한다.
여기서 다음을 뽑아낸다.

  - 가입자수(jnngpCnt)                  → 인원 규모와 3개월 급감
  - 당월고지금액(crrmmNtcAmt) / 가입자수 → 1인당 신고소득 추정 → 임금 삭감·무급휴직 징후
  - 신규취득자수 / 상실가입자수          → 이탈률(퇴사 러시)
  - 사업장 가입상태(wkplJnngpStcd)       → '탈퇴'는 폐업·근로자 0명 직전 신호

실 API: 공공데이터포털 국민연금공단 사업장 가입 내역(NpsBplcInfoInqireServiceV2)
필요 키: DATA_GO_KR_KEY
"""

import os
import random

from .base import Collector, CollectResult, CollectorError, Event, Reading, get_json, to_float

BASE_ENDPOINT = "https://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2/getBassInfoSearchV2"
DETAIL_ENDPOINT = "https://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2/getDetailInfoSearchV2"

# 사업장 가입상태: 1 등록(정상), 2 탈퇴
STATUS_MAP = {"1": (0.0, "연금 등록"), "2": (2.0, "연금 탈퇴")}

# 국민연금 보험료율 9%(사업장 기준) — 고지금액에서 평균 보수월액을 역산한다.
PENSION_RATE = 0.09


class InsuranceCollector(Collector):
    source = "insurance"
    label = "국민연금·고용보험"
    env_keys = ("DATA_GO_KR_KEY",)
    metric_codes = (
        "headcount", "headcount_change_3m", "pension_arrears",
        "avg_pay", "avg_pay_change_6m", "turnover_3m", "pension_status",
    )

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        service_key = os.environ["DATA_GO_KR_KEY"]
        # 이 API는 사업자등록번호 앞 6자리로 사업장을 찾는다.
        payload = get_json(
            BASE_ENDPOINT,
            {
                "serviceKey": service_key,
                "bzowr_rgst_no": (partner["biz_no"] or "")[:6],
                "wkpl_nm": partner["name"],
                "numOfRows": 10,
                "pageNo": 1,
                "dataType": "JSON",
            },
        )
        items = _items(payload)
        if not items:
            raise CollectorError(f"{partner['name']}: 국민연금 사업장 자료를 찾지 못했습니다.")

        record = items[0]
        latest = periods[-1]
        readings: list[Reading] = []
        events: list[Event] = []

        headcount = to_float(record.get("jnngpCnt"))
        if headcount:
            readings.append(Reading(latest, "headcount", headcount, text=f"{headcount:,.0f}명"))
            notice = to_float(record.get("crrmmNtcAmt"))
            average = _avg_pay(notice, headcount)
            if average is not None:
                readings.append(Reading(latest, "avg_pay", average, text=f"{average:,.0f}만원"))

        status_code = str(record.get("wkplJnngStcd") or record.get("wkplJnngpStcd") or "").strip()
        value, label = STATUS_MAP.get(status_code, (None, None))
        if value is not None:
            readings.append(Reading(latest, "pension_status", value, text=label))
            if value >= 2.0:
                events.append(
                    Event(
                        kind="연금 사업장",
                        title="국민연금 사업장 탈퇴 확인",
                        occurred_on=f"{latest}-01",
                        severity="critical",
                    )
                )

        # 상세 조회로 신규취득·상실 인원을 받아 이탈률을 만든다.
        seq = record.get("seq")
        if seq:
            detail = _items(
                get_json(
                    DETAIL_ENDPOINT,
                    {"serviceKey": service_key, "seq": seq, "dataType": "JSON"},
                )
            )
            if detail:
                lost = to_float(detail[0].get("lssJnngpCnt"))
                if lost is not None and headcount:
                    readings.append(Reading(latest, "turnover_3m", round(lost / headcount * 100, 1)))

        # 증감률은 저장된 시계열과 비교해야 하므로 수집 후 보완한다.
        return CollectResult(readings=readings, events=events)

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        rng = random.Random(f"insurance:{partner['biz_no']}")
        profile = partner["profile"] or "healthy"
        small = profile.startswith("small")
        headcount = rng.randint(6, 28) if small else rng.randint(40, 240)
        pay = rng.uniform(280, 360) if small else rng.uniform(320, 470)

        monthly_drift = {
            "healthy": rng.uniform(0.2, 1.4),
            "small_healthy": rng.uniform(0.0, 1.2),
            "watch": rng.uniform(-1.6, -0.4),
            "distress": rng.uniform(-6.5, -3.5),
            "small_distress": rng.uniform(-7.5, -4.0),
        }.get(profile, rng.uniform(-9.0, -6.0))
        pay_drift = {
            "healthy": rng.uniform(0.1, 0.6),
            "small_healthy": rng.uniform(0.0, 0.5),
            "watch": rng.uniform(-0.6, 0.1),
        }.get(profile, rng.uniform(-2.6, -1.2))

        series: list[tuple[str, int, float]] = []
        for period in periods:
            headcount = max(1, round(headcount * (1 + monthly_drift / 100)))
            pay = max(210.0, pay * (1 + pay_drift / 100))
            series.append((period, headcount, pay))

        readings: list[Reading] = []
        events: list[Event] = []
        for index, (period, count, average) in enumerate(series):
            readings.append(Reading(period, "headcount", float(count), text=f"{count}명"))
            readings.append(Reading(period, "avg_pay", round(average, 0), text=f"{average:,.0f}만원"))

            if index >= 3:
                base = series[index - 3][1]
                readings.append(Reading(period, "headcount_change_3m", round((count - base) / base * 100, 1)))
                # 인원이 줄어든 만큼은 최소 이탈로 보고, 정상 이탈률을 더한다.
                shrink = max(0, base - count)
                turnover = (shrink + rng.uniform(0, 1.2)) / base * 100
                readings.append(Reading(period, "turnover_3m", round(turnover, 1)))
            if index >= 6:
                base_pay = series[index - 6][2]
                readings.append(
                    Reading(period, "avg_pay_change_6m", round((average - base_pay) / base_pay * 100, 1))
                )

            arrears = 1.0 if profile in ("distress", "closed", "small_distress") and index >= len(series) - 2 else 0.0
            readings.append(
                Reading(period, "pension_arrears", arrears, text="연금 체납" if arrears else "연금 정상")
            )

            withdrawn = profile == "closed" and index >= len(series) - 1
            readings.append(
                Reading(
                    period, "pension_status", 2.0 if withdrawn else 0.0,
                    text="연금 탈퇴" if withdrawn else "연금 등록",
                )
            )
            if withdrawn:
                events.append(
                    Event(
                        kind="연금 사업장", title="국민연금 사업장 탈퇴 확인",
                        occurred_on=f"{period}-10", severity="critical",
                    )
                )
        return CollectResult(readings=readings, events=events)


def _items(payload: dict) -> list[dict]:
    items = (payload.get("response", {}).get("body", {}).get("items") or {}).get("item") or []
    if isinstance(items, dict):
        return [items]
    return items


def _avg_pay(notice_amount: float | None, headcount: float | None) -> float | None:
    """당월고지금액과 가입자수로 1인당 평균 보수월액(만원)을 역산한다."""
    if not notice_amount or not headcount:
        return None
    monthly_pay = notice_amount / headcount / PENSION_RATE
    return round(monthly_pay / 10_000, 0)


def fill_changes(history: dict[str, list[tuple[str, float]]]) -> list[Reading]:
    """저장된 시계열로 증감 지표를 채운다(실 API가 당월 값만 줄 때의 보완 경로).

    history는 {"headcount": [(period, value), ...], "avg_pay": [...]} 형태.
    """
    readings: list[Reading] = []
    counts = history.get("headcount", [])
    for index in range(3, len(counts)):
        period, count = counts[index]
        base = counts[index - 3][1]
        if base:
            readings.append(Reading(period, "headcount_change_3m", round((count - base) / base * 100, 1)))
    pays = history.get("avg_pay", [])
    for index in range(6, len(pays)):
        period, pay = pays[index]
        base = pays[index - 6][1]
        if base:
            readings.append(Reading(period, "avg_pay_change_6m", round((pay - base) / base * 100, 1)))
    return readings
