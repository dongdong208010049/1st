"""특허·실용신안 보유 현황(KIPRIS) — 참고용 지표.

기술력의 대리 지표다. 재무·신용과 상관이 약해서 점수에는 넣지 않고(가중치 0)
"이 회사가 대체 가능한 곳인지" 판단할 때 참고한다. 대체 불가한 기술 보유
협력사가 흔들리면 공급 대안을 찾기 어렵기 때문에 조기 대응 우선순위가 달라진다.

실 API: KIPRIS Plus 특허/실용신안 서지정보 (출원인명 검색)
필요 키: KIPRIS_KEY
"""

import os
import random

from .base import Collector, CollectResult, CollectorError, Reading, get_json, to_float

DEFAULT_ENDPOINT = (
    "http://plus.kipris.or.kr/openapi/rest/patUtiModInfoSearchSevice/applicantNameSearchInfo"
)


class IprCollector(Collector):
    source = "ipr"
    label = "특허·실용신안"
    env_keys = ("KIPRIS_KEY",)
    metric_codes = ("patent_count",)

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        payload = get_json(
            os.environ.get("KIPRIS_API_URL") or DEFAULT_ENDPOINT,
            {
                "ServiceKey": os.environ["KIPRIS_KEY"],
                "applicantName": partner["name"],
                "numOfRows": 1,
                "pageNo": 1,
                "patent": "true",
                "utility": "true",
            },
        )
        total = to_float(
            payload.get("response", {}).get("count", {}).get("totalCount")
        )
        if total is None:
            raise CollectorError(f"{partner['name']}: 특허 검색 결과를 읽을 수 없습니다.")
        latest = periods[-1]
        return CollectResult(
            readings=[Reading(latest, "patent_count", total, text=f"{total:,.0f}건")]
        )

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        rng = random.Random(f"ipr:{partner['biz_no']}")
        small = (partner["profile"] or "").startswith("small")
        count = rng.randint(0, 4) if small else rng.randint(0, 28)
        readings = []
        for index, period in enumerate(periods):
            # 특허는 드물게 늘어난다.
            if index and rng.random() < 0.04:
                count += 1
            readings.append(Reading(period, "patent_count", float(count), text=f"{count}건"))
        return CollectResult(readings=readings)
