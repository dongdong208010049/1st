"""협력사 정기 제출자료(징구) 수집기.

소규모·비외감 협력사는 DART에 재무가 없다. 이때 실무에서 통하는 확실한 경로는
'국세청이 발급한 증명'을 정기적으로 받는 것이다. 협력사가 임의로 꾸밀 수 없고,
제출 자체를 거부·지연하는 것도 그 자체로 강한 위험 신호다.

  - 표준재무제표증명 (홈택스 발급)   → 매출·부채 등 재무를 손으로 채울 수 있다
  - 부가세 과세표준증명            → 분기 매출 확인(연 1회 재무보다 빠르다)
  - 납세증명서 / 4대보험 완납증명   → 국세·보험료 체납 여부

이 수집기는 제출 기록으로 '자료 제출 경과(개월)'를 지표화한다. 제출된 수치 자체는
협력사 상세 화면에서 손으로 입력하고(source=manual), 같은 지표 코드를 쓰기 때문에
DART가 있는 협력사와 같은 기준으로 채점된다.
"""

from datetime import date

from .. import models
from .base import Collector, CollectResult, Reading

# 제출 기록이 아예 없을 때 쓸 경과월. bad_value보다 크게 두어 0점이 되게 한다.
NEVER_SUBMITTED_MONTHS = 36.0


class SubmissionCollector(Collector):
    source = "submission"
    label = "협력사 제출자료(징구)"
    metric_codes = ("doc_freshness",)

    def available(self) -> bool:
        # 외부 키가 필요 없는 내부 자료다. 항상 live로 본다.
        return True

    def mode(self) -> str:
        return "live"

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        if conn is None:
            return CollectResult()

        latest_period = periods[-1]
        submissions = models.list_submissions(conn, partner["id"], limit=200)
        required = [row for row in submissions if row["doc_type"] in models.REQUIRED_DOC_TYPES]

        readings: list[Reading] = []
        for period in periods:
            # 그 기간까지 제출된 자료만 보고 경과월을 계산한다(과거 시점 재현).
            past = [row for row in required if row["submitted_on"][:7] <= period]
            if past:
                latest = max(row["submitted_on"] for row in past)
                months = _months_between(latest[:7], period)
                text = f"제출 {months:.0f}개월 전"
            else:
                months = NEVER_SUBMITTED_MONTHS
                text = "제출 없음"
            readings.append(Reading(period, "doc_freshness", float(months), text=text))

        # 마지막 기간은 실제 오늘 기준으로 한 번 더 덮어써 화면 표시를 맞춘다.
        if required:
            latest = max(row["submitted_on"] for row in required)
            months = _months_between(latest[:7], date.today().strftime("%Y-%m"))
            readings.append(
                Reading(latest_period, "doc_freshness", float(months), text=f"제출 {months:.0f}개월 전")
            )
        return CollectResult(readings=readings)

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        return self.collect_live(partner, periods, conn)


def _months_between(from_period: str, to_period: str) -> int:
    from_year, from_month = (int(part) for part in from_period.split("-"))
    to_year, to_month = (int(part) for part in to_period.split("-"))
    return max(0, (to_year * 12 + to_month) - (from_year * 12 + from_month))
