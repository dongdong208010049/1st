"""협력사 종합등급 산정.

산정 방식(확정안):
  1) 지표별 0~100 점수 = good_value~bad_value 구간 선형 보간
  2) 종합점수 = 값이 있는 지표들의 가중 평균(없는 지표는 가중치에서 제외)
  3) 자동 감점군 = 데이터 노후, 최근 12개월 리스크 이벤트 건수
  4) 즉시위험(critical) 사유가 하나라도 걸리면 점수와 무관하게 E / 🔴
"""

from dataclasses import dataclass, field
from datetime import date, datetime

# 신호등 경계
SIGNAL_WARN = 70.0   # 이상이면 정상(green)
SIGNAL_ALERT = 40.0  # 이상이면 주의(amber), 미만이면 경고(red)

# 등급 경계 (점수 이상이면 해당 등급)
GRADE_CUTOFFS = ((85.0, "A"), (70.0, "B"), (55.0, "C"), (40.0, "D"), (0.0, "E"))

# 데이터가 이 일수보다 오래되면 노후로 보고 감점한다.
STALE_AFTER_DAYS = 90
STALE_PENALTY = 5.0

# 리스크 이벤트 1건당 감점과 상한
EVENT_PENALTY = 3.0
EVENT_PENALTY_CAP = 15.0
EVENT_WINDOW_DAYS = 365

# 자료 충족률이 낮으면 등급에 상한을 둔다.
# 소규모(비외감) 협력사는 재무 공시가 없어서 '남은 지표만 좋으면 A'가 되기 쉽다.
# 자료가 없는 것은 안전하다는 뜻이 아니므로, 모르는 만큼 등급을 눌러 둔다.
COVERAGE_CAPS = ((0.35, "C"), (0.55, "B"))
GRADE_CEILING = {"C": SIGNAL_WARN - 0.1, "B": 84.9}

# 즉시위험 규칙: (지표코드, 비교, 기준값, 사유)
# 소규모 협력사는 재무제표보다 아래 신호가 먼저·확실하게 나타난다.
CRITICAL_RULES = (
    ("biz_status", ">=", 2.0, "국세청 사업자상태: 폐업"),
    ("biz_status", "==", 1.0, "국세청 사업자상태: 휴업"),
    ("pension_status", ">=", 2.0, "국민연금 사업장 탈퇴(근로자 0명·폐업 직전)"),
    ("factory_status", ">=", 2.0, "공장등록 취소·말소(가동 중단)"),
    ("equity_impairment", ">=", 100.0, "완전자본잠식"),
    ("wage_arrears_count", ">=", 1.0, "고용노동부 임금체불 사업주 명단 등재"),
    ("legal_count", ">=", 1.0, "회생·파산·부도·경매 등 법적 사건 발생"),
    ("headcount_change_3m", "<=", -30.0, "3개월 인원 30% 이상 급감"),
)

_COMPARATORS = {
    ">=": lambda value, threshold: value >= threshold,
    "<=": lambda value, threshold: value <= threshold,
    "==": lambda value, threshold: value == threshold,
}


@dataclass
class MetricScore:
    code: str
    category: str
    label: str
    unit: str | None
    direction: str
    value: float | None
    text: str | None
    score: float | None
    weight: float
    period: str | None
    collected_at: str | None
    source: str | None = None
    stale: bool = False

    @property
    def signal(self) -> str:
        return signal_of(self.score)

    @property
    def is_clean(self) -> bool:
        """'0건'처럼 이상 없음을 뜻하는 값 — 리스트 요약에서 감춘다."""
        return self.direction == "lower_better" and self.value == 0

    @property
    def summary(self) -> str:
        return self.text or f"{self.label} {self.display}"

    @property
    def display(self) -> str:
        if self.text:
            return self.text
        if self.value is None:
            return "—"
        formatted = f"{self.value:,.1f}".rstrip("0").rstrip(".")
        return f"{formatted}{self.unit or ''}"


@dataclass
class PartnerScore:
    score: float | None
    grade: str
    signal: str
    metrics: list[MetricScore] = field(default_factory=list)
    critical_reasons: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)
    coverage: float = 0.0
    grade_cap: str | None = None
    last_collected_at: str | None = None
    stale: bool = False

    @property
    def sources(self) -> list[str]:
        """실제로 값이 들어온 자료원 목록. 소규모 협력사의 커버리지 확인용."""
        return sorted({m.source for m in self.metrics if m.source and m.value is not None})

    @property
    def by_category(self) -> dict[str, list[MetricScore]]:
        grouped: dict[str, list[MetricScore]] = {}
        for metric in self.metrics:
            grouped.setdefault(metric.category, []).append(metric)
        return grouped

    def category_signal(self, category: str) -> str:
        """카테고리 신호등: 그 안에서 가장 나쁜 지표를 따른다."""
        scores = [m.score for m in self.metrics if m.category == category and m.score is not None]
        if not scores:
            return "none"
        return signal_of(min(scores))

    def category_metrics(self, category: str) -> list[MetricScore]:
        return [
            m for m in self.metrics
            if m.category == category and (m.value is not None or m.text)
        ]

    def category_headline(self, category: str) -> str:
        """픽토그램 옆에 붙일 한 조각.

        문제가 있으면 가장 나쁜 지표를, 다 정상이면 그 카테고리의 대표 지표를
        보여준다. 참고용(info) 지표는 채점 지표보다 뒤에 둔다.
        """
        items = self.category_metrics(category)
        if not items:
            return "—"
        scored = [m for m in items if m.score is not None]
        if scored:
            worst = min(scored, key=lambda m: m.score)
            if worst.score < SIGNAL_WARN:
                return worst.summary
            return scored[0].summary  # 정의 순서상 첫 채점 지표가 대표값
        notable = [m for m in items if not m.is_clean]
        return notable[0].summary if notable else "이상 없음"

    def category_tip(self, category: str) -> str:
        """마우스를 올렸을 때 보여줄 전체 내역(줄바꿈 포함)."""
        items = self.category_metrics(category)
        if not items:
            return f"{category}: 수집된 자료가 없습니다"
        lines = [f"{category} · {SIGNAL_TEXT[self.category_signal(category)]}"]
        for metric in items:
            mark = SIGNAL_MARK[metric.signal]
            score = f" {metric.score:.0f}점" if metric.score is not None else ""
            weight = f" (가중 {metric.weight:g})" if metric.weight else ""
            lines.append(f"{mark} {metric.label} {metric.display}{score}{weight}")
        return "\n".join(lines)

    def category_summary(self, category: str, limit: int = 3) -> str:
        items = [
            m for m in self.metrics
            if m.category == category and (m.value is not None or m.text)
        ]
        if not items:
            return "자료 없음"
        notable = [m for m in items if not m.is_clean]
        if not notable:
            # 전부 이상 없음. 지표가 하나뿐인 카테고리는 그 값을 그대로 보여준다.
            return items[0].summary if len(items) == 1 else "이상 없음"
        return " · ".join(m.summary for m in notable[:limit])


# 신호등을 글자·기호로도 표현한다(색만으로 뜻을 전달하지 않기 위해).
SIGNAL_TEXT = {"green": "정상", "amber": "주의", "red": "경고", "none": "자료없음"}
SIGNAL_MARK = {"green": "●", "amber": "▲", "red": "■", "none": "·"}


def signal_of(score: float | None) -> str:
    if score is None:
        return "none"
    if score >= SIGNAL_WARN:
        return "green"
    if score >= SIGNAL_ALERT:
        return "amber"
    return "red"


def grade_of(score: float | None) -> str:
    if score is None:
        return "-"
    for cutoff, grade in GRADE_CUTOFFS:
        if score >= cutoff:
            return grade
    return "E"


def score_metric(defn, value: float | None) -> float | None:
    """지표 정의의 good/bad 값을 기준으로 0~100 점수를 만든다."""
    if value is None or defn["direction"] == "info":
        return None
    good, bad = defn["good_value"], defn["bad_value"]
    if good is None or bad is None or good == bad:
        return None
    ratio = (value - bad) / (good - bad)
    return max(0.0, min(100.0, ratio * 100.0))


def _days_since(timestamp: str | None, today: date) -> int | None:
    if not timestamp:
        return None
    try:
        stamp = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return (today - stamp.date()).days


def score_partner(metric_defs, values: dict, events=(), today: date | None = None) -> PartnerScore:
    """지표 정의 + 측정값 + 리스크 이벤트로 종합점수를 낸다.

    values는 {지표코드: sqlite3.Row(또는 dict)} 형태이며 models.values_as_of()의 결과를 그대로 받는다.
    """
    today = today or date.today()
    metrics: list[MetricScore] = []
    weighted_sum = 0.0
    weight_total = 0.0
    scorable_weight = 0.0
    collected_stamps: list[str] = []

    for defn in metric_defs:
        row = values.get(defn["code"])
        value = row["value"] if row else None
        collected_at = row["collected_at"] if row else None
        age = _days_since(collected_at, today)
        stale = age is not None and age > STALE_AFTER_DAYS
        score = score_metric(defn, value)
        weight = float(defn["weight"] or 0)

        metrics.append(
            MetricScore(
                code=defn["code"],
                category=defn["category"],
                label=defn["label"],
                unit=defn["unit"],
                direction=defn["direction"],
                value=value,
                text=row["text_value"] if row else None,
                score=score,
                weight=weight,
                period=row["period"] if row else None,
                collected_at=collected_at,
                source=row["source"] if row else None,
                stale=stale,
            )
        )
        if collected_at:
            collected_stamps.append(collected_at)
        if weight > 0:
            scorable_weight += weight
            if score is not None:
                weighted_sum += score * weight
                weight_total += weight

    base = weighted_sum / weight_total if weight_total else None
    coverage = weight_total / scorable_weight if scorable_weight else 0.0

    penalties: list[str] = []
    last_collected_at = max(collected_stamps) if collected_stamps else None
    overall_age = _days_since(last_collected_at, today)
    overall_stale = overall_age is not None and overall_age > STALE_AFTER_DAYS

    recent_events = [
        event for event in events
        if (_days_since(event["occurred_on"], today) or 0) <= EVENT_WINDOW_DAYS
    ]

    if base is not None:
        if overall_stale:
            base -= STALE_PENALTY
            penalties.append(f"데이터 노후({overall_age}일 경과) −{STALE_PENALTY:g}")
        if recent_events:
            deduction = min(EVENT_PENALTY * len(recent_events), EVENT_PENALTY_CAP)
            base -= deduction
            penalties.append(f"최근 1년 리스크 이벤트 {len(recent_events)}건 −{deduction:g}")
        base = max(0.0, min(100.0, base))

    # 자료가 적게 모인 협력사는 등급 상한을 적용한다.
    grade_cap = None
    effective = base
    for threshold, cap in COVERAGE_CAPS:
        if coverage < threshold:
            grade_cap = cap
            break
    if grade_cap and base is not None:
        ceiling = GRADE_CEILING[grade_cap]
        if base > ceiling:
            effective = ceiling
            penalties.append(f"자료 충족률 {coverage * 100:.0f}% → 등급 상한 {grade_cap}")

    critical_reasons = []
    for code, comparator, threshold, reason in CRITICAL_RULES:
        row = values.get(code)
        value = row["value"] if row else None
        if value is not None and _COMPARATORS[comparator](value, threshold):
            critical_reasons.append(reason)
    for event in recent_events:
        if event["severity"] != "critical":
            continue
        # 같은 사유를 규칙과 이벤트에서 두 번 세지 않는다.
        if any(event["kind"] in reason for reason in critical_reasons):
            continue
        critical_reasons.append(f"{event['kind']}: {event['title']}")

    if critical_reasons:
        return PartnerScore(
            score=base,
            grade="E",
            signal="red",
            metrics=metrics,
            critical_reasons=critical_reasons,
            penalties=penalties,
            coverage=coverage,
            grade_cap=grade_cap,
            last_collected_at=last_collected_at,
            stale=overall_stale,
        )

    return PartnerScore(
        score=base,
        grade=grade_of(effective),
        signal=signal_of(effective),
        metrics=metrics,
        penalties=penalties,
        coverage=coverage,
        grade_cap=grade_cap,
        last_collected_at=last_collected_at,
        stale=overall_stale,
    )
