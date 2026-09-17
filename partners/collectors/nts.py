"""국세청 사업자등록 상태(휴·폐업) 수집기.

실 API: 공공데이터포털 국세청 사업자등록정보 진위확인 및 상태조회
        https://api.odcloud.kr/api/nts-businessman/v1/status
필요 키: DATA_GO_KR_KEY
폐업·휴업은 공급 단절 그 자체라서 점수와 무관하게 즉시위험(E)으로 처리한다.
"""

import os
import random

from .base import Collector, CollectResult, CollectorError, Event, Reading, post_json

NTS_ENDPOINT = "https://api.odcloud.kr/api/nts-businessman/v1/status"

# 국세청 상태코드 → (내부 점수값, 표시 문자열)
STATUS_MAP = {
    "01": (0.0, "계속사업자"),
    "02": (1.0, "휴업"),
    "03": (2.0, "폐업"),
}


class NtsCollector(Collector):
    source = "nts"
    label = "국세청 사업자상태"
    env_keys = ("DATA_GO_KR_KEY",)
    metric_codes = ("biz_status",)

    def collect_live(self, partner, periods: list[str]) -> CollectResult:
        payload = post_json(
            NTS_ENDPOINT,
            {"b_no": [partner["biz_no"]]},
            {"serviceKey": os.environ["DATA_GO_KR_KEY"]},
        )
        data = payload.get("data") or []
        if not data:
            raise CollectorError(f"{partner['name']}: 국세청 사업자상태 조회 결과가 없습니다.")

        record = data[0]
        code = (record.get("b_stt_cd") or "").strip()
        value, label = STATUS_MAP.get(code, (None, record.get("b_stt") or "확인불가"))
        latest = periods[-1]
        events: list[Event] = []
        if value and value >= 1.0:
            closed_on = record.get("end_dt") or ""
            events.append(
                Event(
                    kind="사업자상태",
                    title=f"{label} 확인" + (f" (일자 {closed_on})" if closed_on else ""),
                    occurred_on=_as_date(closed_on) or f"{latest}-01",
                    severity="critical",
                )
            )
        return CollectResult(readings=[Reading(latest, "biz_status", value, text=label)], events=events)

    def collect_mock(self, partner, periods: list[str]) -> CollectResult:
        profile = partner["profile"] or "healthy"
        rng = random.Random(f"nts:{partner['biz_no']}")
        readings: list[Reading] = []
        events: list[Event] = []
        # 폐업/휴업 시나리오는 마지막 1~2개월에만 상태가 바뀐다.
        flip_at = len(periods) - rng.randint(1, 2)
        for index, period in enumerate(periods):
            if profile == "closed" and index >= flip_at:
                value, label = 2.0, "폐업"
            elif profile == "suspended" and index >= flip_at:
                value, label = 1.0, "휴업"
            else:
                value, label = 0.0, "계속사업자"
            readings.append(Reading(period, "biz_status", value, text=label))
            if value >= 1.0 and index == flip_at:
                events.append(
                    Event(kind="사업자상태", title=f"{label} 확인", occurred_on=f"{period}-15", severity="critical")
                )
        return CollectResult(readings=readings, events=events)


def _as_date(raw: str) -> str | None:
    """'20250131' → '2025-01-31'."""
    digits = (raw or "").strip()
    if len(digits) != 8 or not digits.isdigit():
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
