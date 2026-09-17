"""DART 전자공시(재무·매출) 수집기.

실 API: https://opendart.fss.or.kr  단일회사 전체 재무제표(fnlttSinglAcntAll)
필요 키: DART_API_KEY (오픈DART 인증키)
한계: 상장사·외부감사대상 법인만 공시 대상이라 소규모 협력사는 자료가 없다.
      이 경우 지표는 빈 값으로 남고 종합점수는 남은 지표의 가중치로만 계산된다.
"""

import random

from .base import Collector, CollectResult, CollectorError, Reading, get_json, to_float

DART_ENDPOINT = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
ANNUAL_REPORT_CODE = "11011"  # 사업보고서

# 재무제표 계정명 → 내부 키
ACCOUNT_MAP = {
    "매출액": "revenue",
    "수익(매출액)": "revenue",
    "영업이익": "operating_profit",
    "영업이익(손실)": "operating_profit",
    "부채총계": "liabilities",
    "자본총계": "equity",
    "자본금": "capital_stock",
    "유동자산": "current_assets",
    "유동부채": "current_liabilities",
}

HUNDRED_MILLION = 100_000_000  # 억원 환산


class DartCollector(Collector):
    source = "dart"
    label = "DART 전자공시"
    env_keys = ("DART_API_KEY",)
    metric_codes = (
        "revenue", "revenue_yoy", "op_margin",
        "debt_ratio", "current_ratio", "equity_impairment",
    )

    def collect_live(self, partner, periods: list[str], conn=None) -> CollectResult:
        import os

        corp_code = partner["dart_corp_code"]
        if not corp_code:
            raise CollectorError(f"{partner['name']}: DART 고유번호(dart_corp_code)가 없습니다.")

        years = sorted({int(period.split("-")[0]) for period in periods})
        # 전년 대비 증감률 계산을 위해 직전 연도도 함께 조회한다.
        years = sorted(set(years) | {min(years) - 1})
        accounts_by_year: dict[int, dict[str, float]] = {}
        for year in years:
            payload = get_json(
                DART_ENDPOINT,
                {
                    "crtfc_key": os.environ["DART_API_KEY"],
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": ANNUAL_REPORT_CODE,
                    "fs_div": "OFS",  # 재무상태표 기준 개별/별도
                },
            )
            if payload.get("status") not in ("000", None):
                # 013 = 조회된 데이터 없음. 해당 연도만 건너뛴다.
                if payload.get("status") == "013":
                    continue
                raise CollectorError(f"DART 오류 {payload.get('status')}: {payload.get('message')}")
            accounts_by_year[year] = _parse_accounts(payload.get("list", []))

        readings: list[Reading] = []
        for year, accounts in accounts_by_year.items():
            period = f"{year:04d}-12"
            if period not in periods:
                continue
            previous = accounts_by_year.get(year - 1, {})
            readings.extend(_derive(period, accounts, previous))
        return CollectResult(readings=readings)

    def collect_mock(self, partner, periods: list[str], conn=None) -> CollectResult:
        rng = random.Random(f"dart:{partner['biz_no']}")
        profile = partner["profile"] or "healthy"
        if profile.startswith("small"):
            # 외부감사 대상이 아니면 DART에 재무가 없다. 빈 결과가 정상이다.
            return CollectResult()
        revenue = rng.uniform(40, 900)  # 억원
        debt_ratio = {"healthy": rng.uniform(45, 110), "watch": rng.uniform(170, 240)}.get(
            profile, rng.uniform(330, 480)
        )
        margin = {"healthy": rng.uniform(4, 11), "watch": rng.uniform(-1, 3)}.get(profile, rng.uniform(-9, -2))
        impairment = {"healthy": 0.0, "watch": 0.0}.get(profile, rng.uniform(25, 85))
        yoy_drift = {"healthy": rng.uniform(3, 14), "watch": rng.uniform(-12, -2)}.get(
            profile, rng.uniform(-38, -22)
        )

        readings: list[Reading] = []
        for index, period in enumerate(periods):
            step = index / max(len(periods) - 1, 1)
            month_revenue = revenue * (1 + yoy_drift / 100 * step)
            month_debt = debt_ratio * (1 + 0.25 * step if profile != "healthy" else 1 - 0.05 * step)
            readings.extend(
                [
                    Reading(period, "revenue", round(month_revenue, 1)),
                    Reading(period, "revenue_yoy", round(yoy_drift + rng.uniform(-1.5, 1.5), 1)),
                    Reading(period, "op_margin", round(margin + rng.uniform(-0.8, 0.8), 1)),
                    Reading(period, "debt_ratio", round(month_debt, 1)),
                    Reading(period, "current_ratio", round(max(30.0, 190 - month_debt * 0.3), 1)),
                    Reading(period, "equity_impairment", round(impairment * (1 + step), 1)),
                ]
            )
        return CollectResult(readings=readings)


def _parse_accounts(rows: list[dict]) -> dict[str, float]:
    accounts: dict[str, float] = {}
    for row in rows:
        key = ACCOUNT_MAP.get((row.get("account_nm") or "").strip())
        if not key or key in accounts:
            continue
        amount = to_float(row.get("thstrm_amount"))
        if amount is not None:
            accounts[key] = amount
    return accounts


def _derive(period: str, accounts: dict[str, float], previous: dict[str, float]) -> list[Reading]:
    """재무제표 계정에서 지표를 계산한다."""
    readings: list[Reading] = []
    revenue = accounts.get("revenue")
    equity = accounts.get("equity")
    liabilities = accounts.get("liabilities")
    capital_stock = accounts.get("capital_stock")

    if revenue is not None:
        readings.append(Reading(period, "revenue", round(revenue / HUNDRED_MILLION, 1)))
        previous_revenue = previous.get("revenue")
        if previous_revenue:
            readings.append(
                Reading(period, "revenue_yoy", round((revenue - previous_revenue) / abs(previous_revenue) * 100, 1))
            )
        operating_profit = accounts.get("operating_profit")
        if operating_profit is not None and revenue:
            readings.append(Reading(period, "op_margin", round(operating_profit / revenue * 100, 1)))

    if liabilities is not None and equity:
        readings.append(Reading(period, "debt_ratio", round(liabilities / equity * 100, 1)))

    current_assets = accounts.get("current_assets")
    current_liabilities = accounts.get("current_liabilities")
    if current_assets is not None and current_liabilities:
        readings.append(Reading(period, "current_ratio", round(current_assets / current_liabilities * 100, 1)))

    if capital_stock and equity is not None:
        # 자본잠식률 = (자본금 - 자본총계) / 자본금 × 100, 음수는 0으로 본다.
        impairment = max(0.0, (capital_stock - equity) / capital_stock * 100)
        readings.append(Reading(period, "equity_impairment", round(impairment, 1)))

    return readings
