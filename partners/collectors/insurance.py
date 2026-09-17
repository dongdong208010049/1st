"""국민연금·고용보험 사업장 가입 현황(인원) 수집기.

실 API: 공공데이터포털 국민연금공단 사업장 가입 내역(NpsBplcInfoInqireService)
필요 키: DATA_GO_KR_KEY
인원 급감(3개월 기준)은 협력사 위기의 가장 빠른 선행지표라서 즉시위험 규칙에 들어간다.
"""

import os
import random

from .base import Collector, CollectResult, CollectorError, Reading, get_json, to_float

NPS_ENDPOINT = "https://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2/getBassInfoSearchV2"


class InsuranceCollector(Collector):
    source = "insurance"
    label = "국민연금·고용보험"
    env_keys = ("DATA_GO_KR_KEY",)
    metric_codes = ("headcount", "headcount_change_3m", "pension_arrears")

    def collect_live(self, partner, periods: list[str]) -> CollectResult:
        # 이 API는 사업자등록번호 앞 6자리로 사업장을 조회한다.
        payload = get_json(
            NPS_ENDPOINT,
            {
                "serviceKey": os.environ["DATA_GO_KR_KEY"],
                "bzowr_rgst_no": (partner["biz_no"] or "")[:6],
                "wkpl_nm": partner["name"],
                "numOfRows": 10,
                "pageNo": 1,
                "dataType": "JSON",
            },
        )
        items = (payload.get("response", {}).get("body", {}).get("items") or {}).get("item") or []
        if isinstance(items, dict):
            items = [items]
        if not items:
            raise CollectorError(f"{partner['name']}: 국민연금 사업장 자료를 찾지 못했습니다.")

        headcount = to_float(items[0].get("jnngpCnt"))
        latest = periods[-1]
        readings = [Reading(latest, "headcount", headcount)]
        # 증감률은 저장된 과거 값과 비교해야 하므로 views 단계에서 채운다.
        return CollectResult(readings=readings)

    def collect_mock(self, partner, periods: list[str]) -> CollectResult:
        rng = random.Random(f"insurance:{partner['biz_no']}")
        profile = partner["profile"] or "healthy"
        headcount = rng.randint(18, 240)
        monthly_drift = {
            "healthy": rng.uniform(0.2, 1.4),
            "watch": rng.uniform(-1.6, -0.4),
            "distress": rng.uniform(-6.5, -3.5),
        }.get(profile, rng.uniform(-9.0, -6.0))

        series: list[tuple[str, int]] = []
        for period in periods:
            headcount = max(1, round(headcount * (1 + monthly_drift / 100)))
            series.append((period, headcount))

        readings: list[Reading] = []
        for index, (period, count) in enumerate(series):
            readings.append(Reading(period, "headcount", float(count), text=f"{count}명"))
            if index >= 3:
                base = series[index - 3][1]
                change = (count - base) / base * 100
                readings.append(Reading(period, "headcount_change_3m", round(change, 1)))
            arrears = 1.0 if profile in ("distress", "closed") and index >= len(series) - 2 else 0.0
            readings.append(
                Reading(period, "pension_arrears", arrears, text="연금 체납" if arrears else "연금 정상")
            )
        return CollectResult(readings=readings)


def fill_headcount_change(history: list[tuple[str, float]]) -> list[Reading]:
    """저장된 인원 시계열로 3개월 증감률을 채운다(실 API 경로 보조)."""
    readings: list[Reading] = []
    for index in range(3, len(history)):
        period, count = history[index]
        base = history[index - 3][1]
        if base:
            readings.append(Reading(period, "headcount_change_3m", round((count - base) / base * 100, 1)))
    return readings
