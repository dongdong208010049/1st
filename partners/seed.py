"""지표 정의 기본값과 데모용 샘플 협력사.

METRIC_DEFS의 행을 추가하면 리스트·상세·점수에 자동 반영된다(항목 확장 지점).
"""

from datetime import date

from . import models
from .collectors import run_collection

# (code, category, label, unit, direction, good, bad, weight, source, sort)
METRIC_DEFS = (
    ("biz_status", "기업상태", "사업자상태", None, "lower_better", 0, 2, 0, "nts", 10),
    ("pension_status", "기업상태", "연금 사업장", None, "lower_better", 0, 2, 0, "insurance", 11),
    ("debt_ratio", "재무", "부채비율", "%", "lower_better", 100, 400, 20, "dart", 20),
    ("current_ratio", "재무", "유동비율", "%", "higher_better", 150, 80, 10, "dart", 21),
    ("equity_impairment", "재무", "자본잠식률", "%", "lower_better", 0, 50, 10, "dart", 22),
    # 신용평가 등급은 DART가 커버하지 않는 소규모 협력사의 재무 대체 지표다.
    ("credit_score", "재무", "신용평가 등급", "점", "higher_better", 80, 40, 15, "credit", 23),
    ("revenue", "매출", "매출액", "억원", "info", None, None, 0, "dart", 30),
    ("revenue_yoy", "매출", "매출 증감률", "%", "higher_better", 10, -30, 15, "dart", 31),
    ("op_margin", "매출", "영업이익률", "%", "higher_better", 8, -5, 10, "dart", 32),
    ("headcount", "인원", "가입자수", "명", "info", None, None, 0, "insurance", 40),
    ("headcount_change_3m", "인원", "3개월 인원 증감", "%", "higher_better", 0, -20, 15, "insurance", 41),
    ("turnover_3m", "인원", "3개월 이탈률", "%", "lower_better", 5, 30, 10, "insurance", 42),
    ("avg_pay", "인원", "1인당 신고소득", "만원", "info", None, None, 0, "insurance", 43),
    ("avg_pay_change_6m", "인원", "6개월 소득 변화", "%", "higher_better", 0, -15, 10, "insurance", 44),
    ("pension_arrears", "인원", "연금 체납", None, "lower_better", 0, 1, 5, "insurance", 45),
    ("wage_arrears_count", "경영환경", "임금체불", "건", "lower_better", 0, 2, 10, "risk_list", 50),
    ("accident_count", "경영환경", "산재·중대재해", "건", "lower_better", 0, 3, 5, "risk_list", 51),
    ("sanction_count", "경영환경", "행정제재", "건", "lower_better", 0, 2, 5, "risk_list", 52),
    ("legal_count", "경영환경", "회생·파산·부도·경매", "건", "lower_better", 0, 1, 10, "risk_list", 53),
    ("doc_freshness", "경영환경", "자료 제출 경과", "개월", "lower_better", 3, 15, 10, "submission", 54),
)

# 기업개요 항목 기본값. 화면(설정)에서 추가할 수 있고, 수집기가 새 코드를 들고 오면
# models.DEFAULT_PROFILE_LABELS를 보고 자동 등록된다.
# (code, label, kind, source, sort_order)
PROFILE_FIELDS = (
    ("address", "주소", "text", "insurance", 10),
    ("ceo_name", "대표자", "text", "dart", 20),
    ("established_on", "설립일", "date", "dart", 30),
    ("main_product", "주요 생산품", "text", "manual", 40),
    ("phone", "전화", "text", "dart", 50),
    ("homepage", "홈페이지", "text", "dart", 60),
    ("latitude", "위도", "number", "manual", 90),
    ("longitude", "경도", "number", "manual", 91),
)

# 업(業) 분류 기본값. 화면(설정)에서 추가·수정할 수 있고, 여기에 행을 더해도 된다.
# (code, label, sort_order)
CATEGORIES = (
    ("mold", "금형", 10),
    ("production", "양산처", 20),
    ("jig", "지그", 30),
    ("inspection", "검사구", 40),
    ("etc", "기타", 90),
)

# (name, biz_no, category_code, industry, manager, tier, profile)
SAMPLE_PARTNERS = (
    ("대한정밀공업", "1048201234", "mold", "금속 가공", "김철수", "1차", "distress"),
    ("동성테크", "2208102345", "production", "전자부품", "김철수", "1차", "healthy"),
    ("세방기전", "3138503456", "production", "전기장비", "이영희", "1차", "watch"),
    ("한울소재", "4028104567", "production", "화학소재", "이영희", "2차", "healthy"),
    ("우진몰드", "5178205678", "mold", "금형", "박민수", "2차", "watch"),
    ("삼환전자", "6068306789", "inspection", "반도체부품", "박민수", "1차", "healthy"),
    ("태광하이텍", "7098407890", "jig", "정밀가공", "정지훈", "2차", "closed"),
    ("나라프레스", "8108508901", "production", "프레스 가공", "정지훈", "2차", "suspended"),
    # 아래 두 곳은 외부감사 대상이 아닌 소규모 업체다. DART에 재무가 없으므로
    # 연금(인원·신고소득)·신용등급·제출자료·법적 사건으로만 감시된다.
    ("성진지그", "9218609012", "jig", "치공구 제작", "김철수", "2차", "small_healthy"),
    ("명진검사구", "1338709123", "inspection", "검사구 제작", "이영희", "2차", "small_distress"),
)

# 정기 징구 자료 샘플. (biz_no, doc_type, 제출일, 대상기간)
SAMPLE_SUBMISSIONS = (
    ("1048201234", "표준재무제표증명", -7, "2025 회계연도"),
    ("2208102345", "표준재무제표증명", -2, "2025 회계연도"),
    ("2208102345", "납세증명서", -1, None),
    ("3138503456", "표준재무제표증명", -5, "2025 회계연도"),
    ("4028104567", "표준재무제표증명", -2, "2025 회계연도"),
    ("5178205678", "부가세 과세표준증명", -4, "2026년 1기"),
    ("6068306789", "표준재무제표증명", -1, "2025 회계연도"),
    ("9218609012", "표준재무제표증명", -2, "2025 회계연도"),
    ("9218609012", "4대보험 완납증명", -1, None),
    ("9218609012", "부가세 과세표준증명", -3, "2026년 1기"),
    # 명진검사구는 독촉에도 제출이 없다. 그 자체가 신호다.
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


def seed_profile_fields(conn) -> None:
    for code, label, kind, source, order in PROFILE_FIELDS:
        models.upsert_profile_field(
            conn, code=code, label=label, kind=kind, source=source, active=1, sort_order=order
        )


def seed_categories(conn) -> None:
    for code, label, sort_order in CATEGORIES:
        models.upsert_category(conn, code, label, sort_order)


def seed_sample_partners(conn) -> list[int]:
    return [
        models.upsert_partner(
            conn,
            name=name,
            biz_no=biz_no,
            category_code=category_code,
            industry=industry,
            manager=manager,
            tier=tier,
            profile=profile,
        )
        for name, biz_no, category_code, industry, manager, tier, profile in SAMPLE_PARTNERS
    ]


def seed_sample_submissions(conn) -> None:
    """샘플 제출 기록. 제출일은 '이번 달 기준 n개월 전'으로 만든다."""
    today = date.today()
    for biz_no, doc_type, month_offset, period in SAMPLE_SUBMISSIONS:
        partner = conn.execute("SELECT id FROM partners WHERE biz_no = ?", (biz_no,)).fetchone()
        if partner is None:
            continue
        stamp = models.shift_period(models.current_period(today), month_offset)
        models.add_submission(
            conn,
            partner["id"],
            doc_type=doc_type,
            period=period,
            submitted_on=f"{stamp}-{min(today.day, 28):02d}",
        )


# (biz_no, 주요 생산품)
SAMPLE_PRODUCTS = (
    ("1048201234", "프레스 금형 (차체 패널)"),
    ("2208102345", "커넥터 하우징"),
    ("3138503456", "배전반 어셈블리"),
    ("4028104567", "엔지니어링 플라스틱 컴파운드"),
    ("5178205678", "사출 금형"),
    ("6068306789", "반도체 검사 소켓"),
    ("7098407890", "용접 지그"),
    ("8108508901", "프레스 가공품"),
    ("9218609012", "조립 지그·치공구"),
    ("1338709123", "검사구·게이지"),
)

# 개요 변경 이력 샘플. (biz_no, field, 이전값, 이전 확인일, 새값, 새 확인일)
SAMPLE_PROFILE_HISTORY = (
    ("1048201234", "ceo_name", "김성곤", "2023-02-10", "박영수", "2026-04-02"),
    ("3138503456", "address", "경상남도 김해시 주촌면 골든루트로 12", "2023-03-15",
     "경상남도 김해시 주촌면 골든루트로 210", "2026-06-11"),
)


# 공시가 없는 소규모 협력사의 대표자·설립일은 사업자등록증·등기부를 징구해 손으로 넣는다.
SAMPLE_MANUAL_PROFILE = (
    ("9218609012", "ceo_name", "정우진", "2023-01-20"),
    ("9218609012", "established_on", "2009-05-14", "2023-01-20"),
    ("1338709123", "ceo_name", "명동현", "2023-01-20"),
    ("1338709123", "established_on", "2013-11-02", "2023-01-20"),
)


def seed_profile_samples(conn) -> None:
    for biz_no, field, value, valid_from in SAMPLE_MANUAL_PROFILE:
        partner = conn.execute("SELECT id FROM partners WHERE biz_no = ?", (biz_no,)).fetchone()
        if partner:
            models.put_profile_value(conn, partner["id"], field, value, valid_from, "manual")
    for biz_no, product in SAMPLE_PRODUCTS:
        partner = conn.execute("SELECT id FROM partners WHERE biz_no = ?", (biz_no,)).fetchone()
        if partner:
            models.put_profile_value(conn, partner["id"], "main_product", product, "2023-01-01", "manual")
    for biz_no, field, old_value, old_on, new_value, new_on in SAMPLE_PROFILE_HISTORY:
        partner = conn.execute("SELECT id FROM partners WHERE biz_no = ?", (biz_no,)).fetchone()
        if not partner:
            continue
        models.put_profile_value(conn, partner["id"], field, old_value, old_on, "seed")
        models.put_profile_value(conn, partner["id"], field, new_value, new_on, "seed")
        models.add_change(
            conn, partner["id"], "개요",
            f"{models.DEFAULT_PROFILE_LABELS.get(field, (field,))[0]} 변경",
            f"{old_value} → {new_value}", severity="warn", anchor="overview",
        )
    conn.commit()


def seed_changes(conn, recent_months: int = 4) -> None:
    """최근 사건·상태 변화를 티커용 변경 로그로 옮긴다.

    첫 적재는 '변경'이 아니라서 change_log가 비는데, 화면 확인용으로 최근 몇 달의
    실제 사건과 상태 전환을 옮겨 담는다. 운영 중에는 수집이 알아서 쌓는다.
    """
    floor = models.shift_period(models.current_period(), -recent_months)
    for partner in models.list_partners(conn):
        for event in models.list_risk_events(conn, partner["id"]):
            if event["occurred_on"][:7] < floor:
                continue
            models.add_change(
                conn, partner["id"], "사건", f"{event['kind']}: {event['title']}",
                event["occurred_on"], severity=event["severity"], anchor="events",
            )
        for code in ("biz_status", "pension_status"):
            timeline = models.status_timeline(conn, partner["id"], code)
            for entry in timeline[1:]:
                if entry["since"] < floor:
                    continue
                models.add_change(
                    conn, partner["id"], "상태", f"{entry['label']}로 변경",
                    f"{entry['since']} 확인",
                    severity="critical" if (entry["value"] or 0) >= 1 else "good",
                    anchor="overview",
                )
    conn.commit()


def seed_all(conn, months: int | None = None, start: str = models.HISTORY_START) -> None:
    """지표 정의 + 샘플 협력사 + 이력(기본 2023-01~현재) 수집값을 한 번에 채운다."""
    models.init_db(conn)
    seed_categories(conn)
    seed_profile_fields(conn)
    seed_metric_defs(conn)
    seed_sample_partners(conn)
    seed_sample_submissions(conn)
    seed_profile_samples(conn)
    periods = (
        models.recent_periods(models.current_period(), months)
        if months else models.history_periods(start=start)
    )
    run_collection(conn, periods)
    seed_changes(conn)
