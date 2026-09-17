"""지표 정의 기본값과 데모용 샘플 협력사.

METRIC_DEFS의 행을 추가하면 리스트·상세·점수에 자동 반영된다(항목 확장 지점).
"""

from . import models
from .collectors import run_collection

# (code, category, label, unit, direction, good, bad, weight, source, sort)
METRIC_DEFS = (
    ("biz_status", "기업상태", "사업자상태", None, "lower_better", 0, 2, 0, "nts", 10),
    ("debt_ratio", "재무", "부채비율", "%", "lower_better", 100, 400, 20, "dart", 20),
    ("current_ratio", "재무", "유동비율", "%", "higher_better", 150, 80, 10, "dart", 21),
    ("equity_impairment", "재무", "자본잠식률", "%", "lower_better", 0, 50, 10, "dart", 22),
    ("revenue", "매출", "매출액", "억원", "info", None, None, 0, "dart", 30),
    ("revenue_yoy", "매출", "매출 증감률", "%", "higher_better", 10, -30, 15, "dart", 31),
    ("op_margin", "매출", "영업이익률", "%", "higher_better", 8, -5, 10, "dart", 32),
    ("headcount", "인원", "가입자수", "명", "info", None, None, 0, "insurance", 40),
    ("headcount_change_3m", "인원", "3개월 인원 증감", "%", "higher_better", 0, -20, 15, "insurance", 41),
    ("pension_arrears", "인원", "연금 체납", None, "lower_better", 0, 1, 5, "insurance", 42),
    ("wage_arrears_count", "경영환경", "임금체불", "건", "lower_better", 0, 2, 10, "risk_list", 50),
    ("accident_count", "경영환경", "산재·중대재해", "건", "lower_better", 0, 3, 5, "risk_list", 51),
    ("sanction_count", "경영환경", "행정제재", "건", "lower_better", 0, 2, 5, "risk_list", 52),
)

# (name, biz_no, industry, manager, tier, profile)
SAMPLE_PARTNERS = (
    ("대한정밀공업", "1048201234", "금속 가공", "김철수", "1차", "distress"),
    ("동성테크", "2208102345", "전자부품", "김철수", "1차", "healthy"),
    ("세방기전", "3138503456", "전기장비", "이영희", "1차", "watch"),
    ("한울소재", "4028104567", "화학소재", "이영희", "2차", "healthy"),
    ("우진몰드", "5178205678", "금형", "박민수", "2차", "watch"),
    ("삼환전자", "6068306789", "반도체부품", "박민수", "1차", "healthy"),
    ("태광하이텍", "7098407890", "정밀가공", "정지훈", "2차", "closed"),
    ("나라프레스", "8108508901", "프레스 가공", "정지훈", "2차", "suspended"),
)


def seed_metric_defs(conn) -> None:
    for code, category, label, unit, direction, good, bad, weight, source, order in METRIC_DEFS:
        models.upsert_metric_def(
            conn,
            code=code,
            category=category,
            label=label,
            unit=unit,
            direction=direction,
            good_value=good,
            bad_value=bad,
            weight=weight,
            source=source,
            active=1,
            sort_order=order,
        )


def seed_sample_partners(conn) -> list[int]:
    return [
        models.upsert_partner(
            conn,
            name=name,
            biz_no=biz_no,
            industry=industry,
            manager=manager,
            tier=tier,
            profile=profile,
        )
        for name, biz_no, industry, manager, tier, profile in SAMPLE_PARTNERS
    ]


def seed_all(conn, months: int = 12) -> None:
    """지표 정의 + 샘플 협력사 + 최근 12개월 수집값을 한 번에 채운다."""
    models.init_db(conn)
    seed_metric_defs(conn)
    seed_sample_partners(conn)
    periods = models.recent_periods(models.current_period(), months)
    run_collection(conn, periods)
