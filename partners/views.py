"""협력사 모니터링 대시보드 화면(Blueprint)."""

import math
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import (
    Blueprint, current_app, flash, g, jsonify, make_response, redirect,
    render_template, request, url_for,
)

from . import geo, models
from .collectors import collector_status, run_collection
from .scoring import PartnerScore, score_partner

bp = Blueprint("partners", __name__, url_prefix="/partners")

TREND_MONTHS = 6
TREND_WIDTH, TREND_HEIGHT = 72, 22
DETAIL_TREND_WIDTH, DETAIL_TREND_HEIGHT = 240, 48
METRIC_SPARK_WIDTH, METRIC_SPARK_HEIGHT = 60, 18
DEFAULT_DB_PATH = "instance/partners.db"

SIGNAL_LABELS = {"green": "정상", "amber": "주의", "red": "경고", "none": "자료없음"}

# 마지막으로 본 변경 번호를 담는 쿠키. 새 변경만 한 번 강조하는 데 쓴다.
SEEN_COOKIE = "pm_seen_change"
SEEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 90

# 지도 마커 모양(업종 식별). 색은 신호등이 쓰므로 모양이 업종을 구분한다.
MARKER_SHAPES = ("circle", "square", "triangle", "diamond", "hexagon", "pentagon")

# 카테고리·변경 종류 픽토그램(글을 줄이고 아이콘으로 읽게 한다)
CATEGORY_ICONS = {
    "기업상태": "building", "재무": "coins", "매출": "trend",
    "인원": "people", "거래": "handshake", "경영환경": "shield",
}
CHANGE_ICONS = {"상태": "building", "지표": "trend", "사건": "shield", "개요": "pencil", "신규": "plus"}
MAP_WIDTH, MAP_HEIGHT = 430, 570
TICKER_LIMIT = 24
CHART_YEARS = 3
CHART_WIDTH, CHART_PLOT_HEIGHT = 420, 150
# 리스트의 카테고리 컬럼 순서. 지표를 추가하면 해당 카테고리 컬럼에 자동으로 붙는다.
CATEGORY_ORDER = ("기업상태", "재무", "매출", "인원", "거래", "경영환경")


def db_path() -> str:
    return current_app.config.get("PARTNERS_DB") or os.environ.get("PARTNERS_DB") or DEFAULT_DB_PATH


def get_conn() -> sqlite3.Connection:
    if "partners_db" not in g:
        conn = models.connect(db_path())
        models.init_db(conn)
        g.partners_db = conn
    return g.partners_db


@bp.teardown_app_request
def _close_conn(_exception=None):
    conn = g.pop("partners_db", None)
    if conn is not None:
        conn.close()


def _trend_scores(conn, partner_id: int, defs, events, periods: list[str]) -> list[float]:
    """최근 N개월 종합점수(스파크라인 원본)."""
    scores = []
    for period in periods:
        values = models.values_as_of(conn, partner_id, period)
        if not values:
            continue
        result = score_partner(defs, values, events)
        if result.score is not None:
            scores.append(round(result.score, 1))
    return scores


def sparkline(values: list[float], width: int = 72, height: int = 22) -> str:
    """0~100 점수 시계열을 SVG polyline 좌표로."""
    if len(values) < 2:
        return ""
    step = width / (len(values) - 1)
    points = []
    for index, value in enumerate(values):
        y = height - (max(0.0, min(100.0, value)) / 100.0) * (height - 2) - 1
        points.append(f"{index * step:.1f},{y:.1f}")
    return " ".join(points)


def build_rows(conn, period: str) -> list[dict]:
    defs = models.list_metric_defs(conn)
    trend_periods = models.recent_periods(period, TREND_MONTHS)
    shape_by_category = {
        row["code"]: MARKER_SHAPES[index % len(MARKER_SHAPES)]
        for index, row in enumerate(models.list_categories(conn, active_only=False))
    }
    rows = []
    for partner in models.list_partners(conn):
        events = models.list_risk_events(conn, partner["id"])
        values = models.values_as_of(conn, partner["id"], period)
        result = score_partner(defs, values, events)
        trend = _trend_scores(conn, partner["id"], defs, events, trend_periods)
        profile = models.profile_snapshot(conn, partner["id"])
        rows.append(
            {
                "partner": partner,
                "score": result,
                "trend": trend,
                "trend_points": sparkline(trend, TREND_WIDTH, TREND_HEIGHT),
                "trend_delta": round(trend[-1] - trend[0], 1) if len(trend) >= 2 else None,
                "events": events,
                "profile": profile,
                "address": profile["address"]["value"] if "address" in profile else None,
                "shape": shape_by_category.get(partner["category_code"], "circle"),
            }
        )
    return rows


def _coordinates(row: dict) -> tuple[float, float] | None:
    """위·경도를 직접 넣었으면 그것을, 없으면 주소에서 찾는다."""
    profile = row["profile"]
    try:
        if "latitude" in profile and "longitude" in profile:
            return float(profile["latitude"]["value"]), float(profile["longitude"]["value"])
    except (TypeError, ValueError):
        pass
    return geo.locate(row["address"])


DETAIL_MAP_WIDTH, DETAIL_MAP_HEIGHT = 250, 330


def build_map(rows: list[dict], width: int = MAP_WIDTH, height: int = MAP_HEIGHT) -> dict:
    """지도 마커. 색=신호등, 모양=업종. 좌표가 겹치면 조금씩 흩어 놓는다."""
    markers = []
    used: dict[tuple[float, float], int] = {}
    missing = []
    for row in rows:
        point = _coordinates(row)
        if point is None:
            missing.append(row)
            continue
        x, y = geo.project(point[0], point[1], width, height)
        key = (x, y)
        seen = used.get(key, 0)
        used[key] = seen + 1
        if seen:  # 같은 지역이면 나선형으로 밀어내 겹치지 않게 한다
            angle = seen * 2.2
            radius = 13 + 4.5 * seen
            x += round(radius * math.cos(angle), 1)
            y += round(radius * math.sin(angle), 1)
        markers.append(
            {
                "id": row["partner"]["id"],
                "name": row["partner"]["name"],
                "x": x,
                "y": y,
                "shape": row["shape"],
                "signal": row["score"].signal,
                "grade": row["score"].grade,
                "category": row["partner"]["category_code"],
                "address": row["address"] or "주소 미확인",
            }
        )
    return {
        "width": width,
        "height": height,
        "outlines": geo.outline_paths(width, height),
        "markers": markers,
        "missing": [row["partner"]["name"] for row in missing],
    }


def _is_weak(row: dict) -> bool:
    """공시 재무가 없거나 자료가 덜 모여 등급 상한이 걸린 협력사.

    소규모(비외감) 협력사가 대부분 여기에 들어온다. 제출자료 징구·신용등급 등록
    대상 목록으로 쓴다.
    """
    score = row["score"]
    return bool(score.grade_cap) or "dart" not in score.sources


def _category_summary(rows: list[dict], categories) -> list[dict]:
    """업종별 협력사 수와 경고 건수. 리스트 상단 칩과 필터 링크로 쓴다."""
    summary = []
    for category in categories:
        members = [row for row in rows if (row["partner"]["category_code"] or "") == category["code"]]
        summary.append(
            {
                "code": category["code"],
                "label": category["label"],
                "total": len(members),
                "red": sum(1 for row in members if row["score"].signal == "red"),
            }
        )
    unassigned = [row for row in rows if not row["partner"]["category_code"]]
    if unassigned:
        summary.append(
            {
                "code": "none",
                "label": "미분류",
                "total": len(unassigned),
                "red": sum(1 for row in unassigned if row["score"].signal == "red"),
            }
        )
    return summary


def _sort_key(row: dict, order: str):
    result: PartnerScore = row["score"]
    if order == "name":
        return row["partner"]["name"]
    if order == "score_desc":
        return -(result.score if result.score is not None else -1)
    if order == "stale":
        return result.last_collected_at or ""
    # 기본: 위험 우선(즉시위험 → 낮은 점수)
    return (0 if result.critical_reasons else 1, result.score if result.score is not None else 101)


@bp.route("/")
def index():
    conn = get_conn()
    periods = models.known_periods(conn, limit=60)
    period = request.args.get("period") or (periods[-1] if periods else models.current_period())
    all_rows = build_rows(conn, period)
    rows = list(all_rows)

    query = (request.args.get("q") or "").strip()
    category = request.args.get("category") or ""
    coverage = request.args.get("coverage") or ""
    relation = request.args.get("relation") or ""
    group = request.args.get("group") or ""
    signal = request.args.get("signal") or ""
    grade = request.args.get("grade") or ""
    manager = request.args.get("manager") or ""
    order = request.args.get("order") or "risk"

    if query:
        needle = query.lower()
        rows = [
            row for row in rows
            if needle in row["partner"]["name"].lower()
            or needle in (row["partner"]["biz_no"] or "")
            or needle in (row["partner"]["industry"] or "").lower()
        ]
    if category:
        # 'none'은 미분류 협력사만 본다.
        wanted = "" if category == "none" else category
        rows = [row for row in rows if (row["partner"]["category_code"] or "") == wanted]
    if relation:
        rows = [row for row in rows if row["partner"]["relation"] == relation]
    if group:
        rows = [row for row in rows if (row["partner"]["group_name"] or "") == group]
    if coverage == "weak":
        rows = [row for row in rows if _is_weak(row)]
    if signal:
        rows = [row for row in rows if row["score"].signal == signal]
    if grade:
        rows = [row for row in rows if row["score"].grade == grade]
    if manager:
        rows = [row for row in rows if (row["partner"]["manager"] or "") == manager]

    rows.sort(key=lambda row: _sort_key(row, order))

    summary = {
        "total": len(all_rows),
        "red": sum(1 for row in all_rows if row["score"].signal == "red"),
        "amber": sum(1 for row in all_rows if row["score"].signal == "amber"),
        "green": sum(1 for row in all_rows if row["score"].signal == "green"),
        "critical": sum(1 for row in all_rows if row["score"].critical_reasons),
        "weak": sum(1 for row in all_rows if _is_weak(row)),
        "affiliate": sum(1 for row in all_rows if row["partner"]["relation"] == "affiliate"),
        "stale": sum(1 for row in all_rows if row["score"].stale),
    }
    managers = sorted({row["partner"]["manager"] for row in all_rows if row["partner"]["manager"]})
    categories_meta = models.list_categories(conn)
    category_summary = _category_summary(all_rows, categories_meta)

    seen_id = _seen_change_id()
    changed = models.changed_partner_ids(conn, seen_id)
    latest_change_id = models.max_change_id(conn)

    response = make_response(render_template(
        "partners/list.html",
        rows=rows,
        summary=summary,
        ticker=models.list_changes(conn, limit=TICKER_LIMIT),
        seen_change_id=seen_id,
        changed=changed,
        new_change_count=sum(row["n"] for row in changed.values()),
        map_data=build_map(rows),  # 지도도 현재 필터를 따른다
        marker_shapes=_shape_legend(conn),
        cat_class=_category_classes(conn),
        managers=managers,
        periods=periods,
        period=period,
        categories=CATEGORY_ORDER,
        signal_labels=SIGNAL_LABELS,
        filters={
            "q": query, "category": category, "coverage": coverage, "signal": signal,
            "grade": grade, "manager": manager, "order": order,
            "relation": relation, "group": group,
        },
        relation_labels=models.RELATION_LABELS,
        groups=models.list_groups(conn),
        category_icons=CATEGORY_ICONS,
        change_icons=CHANGE_ICONS,
        category_labels=models.category_labels(conn),
        category_options=categories_meta,
        category_summary=category_summary,
        collectors=collector_status(),
        runs=models.list_runs(conn, limit=5),
    ))
    # 새 변경을 '한 번' 강조한 뒤 본 것으로 표시한다.
    response.set_cookie(
        SEEN_COOKIE, str(latest_change_id), max_age=SEEN_COOKIE_MAX_AGE, samesite="Lax"
    )
    return response


def _seen_change_id() -> int:
    try:
        return int(request.cookies.get(SEEN_COOKIE, "0"))
    except ValueError:
        return 0


def _shape_legend(conn) -> list[dict]:
    return [
        {
            "code": row["code"],
            "label": row["label"],
            "shape": MARKER_SHAPES[index % len(MARKER_SHAPES)],
            "cls": f"c-{index % 6 + 1}",
        }
        for index, row in enumerate(models.list_categories(conn, active_only=False))
    ]


def _category_classes(conn) -> dict[str, str]:
    """업종 배지 색 슬롯. 검증된 카테고리 팔레트 순서를 그대로 쓴다."""
    return {row["code"]: row["cls"] for row in _shape_legend(conn)}


@bp.route("/map")
def map_view():
    """지도 전용 화면. 업종(모양) × 신호등(색)으로 배치를 본다."""
    conn = get_conn()
    periods = models.known_periods(conn, limit=60)
    period = request.args.get("period") or (periods[-1] if periods else models.current_period())
    rows = build_rows(conn, period)
    category = request.args.get("category") or ""
    if category:
        rows = [row for row in rows if (row["partner"]["category_code"] or "") == category]
    return render_template(
        "partners/map.html",
        map_data=build_map(rows),
        marker_shapes=_shape_legend(conn),
        category_options=models.list_categories(conn),
        category_labels=models.category_labels(conn),
        cat_class=_category_classes(conn),
        filters={"category": category},
        period=period,
        periods=periods,
        rows=sorted(rows, key=lambda row: _sort_key(row, "risk")),
        signal_labels=SIGNAL_LABELS,
    )


@bp.route("/<int:partner_id>", methods=["GET", "POST"])
def detail(partner_id: int):
    conn = get_conn()
    partner = models.get_partner(conn, partner_id)
    if partner is None:
        flash("해당 협력사를 찾을 수 없습니다.")
        return redirect(url_for("partners.index"))

    if request.method == "POST":
        action = request.form.get("action") or "update_partner"

        if action == "add_submission":
            submitted_on = (request.form.get("submitted_on") or "").strip()
            doc_type = (request.form.get("doc_type") or "").strip()
            if not submitted_on or not doc_type:
                flash("자료 종류와 제출일을 입력해 주세요.")
            else:
                models.add_submission(
                    conn,
                    partner_id,
                    doc_type=doc_type,
                    period=(request.form.get("period") or "").strip() or None,
                    submitted_on=submitted_on,
                    note=(request.form.get("note") or "").strip() or None,
                )
                # 제출 경과 지표를 바로 다시 계산한다.
                run_collection(conn, models.recent_periods(models.current_period(), 12), ["submission"])
                flash(f"{doc_type} 제출 기록을 등록했습니다.")

        elif action == "update_overview":
            today = date.today().isoformat()
            for field in models.list_profile_fields(conn):
                if f"profile__{field['code']}" not in request.form:
                    continue
                value = (request.form.get(f"profile__{field['code']}") or "").strip() or None
                previous = models.put_profile_value(
                    conn, partner_id, field["code"], value,
                    valid_from=(request.form.get("valid_from") or today), source="manual",
                )
                if previous:
                    models.add_change(
                        conn, partner_id, "개요", f"{field['label']} 변경",
                        f"{previous} → {value}", severity="warn", anchor="overview",
                    )
            conn.commit()
            flash("기업개요를 저장했습니다. 변경된 항목은 이력으로 남습니다.")

        elif action == "manual_value":
            code = (request.form.get("metric_code") or "").strip()
            period = (request.form.get("value_period") or "").strip()
            raw_value = _as_float(request.form.get("value"))
            if not code or not period or raw_value is None:
                flash("지표·기간·값을 모두 입력해 주세요.")
            else:
                models.put_metric_value(
                    conn, partner_id, code, period, raw_value,
                    text_value=(request.form.get("value_text") or "").strip() or None,
                    source="manual",
                )
                conn.commit()
                flash(f"{code} {period} 값을 직접 입력했습니다.")

        else:
            models.update_partner_fields(
                conn,
                partner_id,
                name=(request.form.get("name") or partner["name"]).strip(),
                category_code=(request.form.get("category_code") or "").strip() or None,
                relation=(request.form.get("relation") or partner["relation"]).strip(),
                group_name=(request.form.get("group_name") or "").strip() or None,
                industry=(request.form.get("industry") or "").strip() or None,
                manager=(request.form.get("manager") or "").strip() or None,
                tier=(request.form.get("tier") or "").strip() or None,
                dart_corp_code=(request.form.get("dart_corp_code") or "").strip() or None,
            )
            flash("협력사 정보를 저장했습니다.")
        return redirect(url_for("partners.detail", partner_id=partner_id))

    periods = models.known_periods(conn, limit=24)
    period = request.args.get("period") or (periods[-1] if periods else models.current_period())
    defs = models.list_metric_defs(conn)
    events = models.list_risk_events(conn, partner_id)
    values = models.values_as_of(conn, partner_id, period)
    result = score_partner(defs, values, events)

    trend_periods = models.recent_periods(period, 12)
    trend = _trend_scores(conn, partner_id, defs, events, trend_periods)

    histories = []
    for metric in result.metrics:
        history = [
            row["value"] for row in models.value_history(conn, partner_id, metric.code, limit=12)
            if row["value"] is not None
        ]
        histories.append(
            {
                "metric": metric,
                "history": history,
                "points": (
                    sparkline(_normalize(history), METRIC_SPARK_WIDTH, METRIC_SPARK_HEIGHT)
                    if len(history) >= 2 else ""
                ),
            }
        )

    return render_template(
        "partners/detail.html",
        partner=partner,
        score=result,
        histories=histories,
        events=events,
        trend=trend,
        trend_points=sparkline(trend, DETAIL_TREND_WIDTH, DETAIL_TREND_HEIGHT),
        periods=periods,
        period=period,
        categories=CATEGORY_ORDER,
        signal_labels=SIGNAL_LABELS,
        category_options=models.list_categories(conn),
        category_labels=models.category_labels(conn),
        cat_class=_category_classes(conn),
        siblings=(
            models.group_members(conn, partner["group_name"], partner_id)
            if partner["group_name"] else []
        ),
        relation_labels=models.RELATION_LABELS,
        category_icons=CATEGORY_ICONS,
        change_icons=CHANGE_ICONS,
        submissions=models.list_submissions(conn, partner_id),
        doc_types=models.REQUIRED_DOC_TYPES + models.OPTIONAL_DOC_TYPES,
        metric_defs=defs,
        recent_period_options=models.recent_periods(period, 12)[::-1],
        profile=models.profile_snapshot(conn, partner_id),
        profile_fields=models.list_profile_fields(conn),
        profile_changes=models.profile_history(conn, partner_id),
        yearly=yearly_chart(conn, partner_id, period),
        status_history=[
            {"code": code, "label": label, "timeline": models.status_timeline(conn, partner_id, code)}
            for code, label in (("biz_status", "국세청 사업자상태"), ("pension_status", "국민연금 사업장"))
        ],
        map_point=build_map(
            [_detail_row(conn, partner, period, defs)], DETAIL_MAP_WIDTH, DETAIL_MAP_HEIGHT
        ),
        marker_shapes=_shape_legend(conn),
        changes=models.list_changes(conn, limit=200),
        history_start=models.HISTORY_START,
    )


def _detail_row(conn, partner, period: str, defs) -> dict:
    """지도 마커 하나를 그리기 위한 최소 행."""
    events = models.list_risk_events(conn, partner["id"])
    values = models.values_as_of(conn, partner["id"], period)
    profile = models.profile_snapshot(conn, partner["id"])
    return {
        "partner": partner,
        "score": score_partner(defs, values, events),
        "profile": profile,
        "address": profile["address"]["value"] if "address" in profile else None,
        "shape": "circle",
    }


def yearly_chart(conn, partner_id: int, period: str, years: int = CHART_YEARS) -> dict:
    """최근 N년 매출 막대그래프 데이터.

    공시 재무가 없는 소규모 협력사는 매출 자료가 없으므로 인원(연금 가입자수)으로
    대체한다. 무엇을 그린 것인지 화면에 함께 표시한다.
    """
    end_year = int(period[:4])
    wanted = [str(year) for year in range(end_year - years + 1, end_year + 1)]

    # 대체 순서: 공시 매출 → 공공 수주(실측) → 연금 가입자수
    for code, label, unit, note in (
        ("revenue", "매출액", "억원", "DART 공시 또는 직접 입력"),
        ("public_award", "공공 수주", "백만원", "공시 매출이 없어 조달청 낙찰실적으로 대체"),
        ("headcount", "가입자수", "명", "매출·수주 자료가 없어 연금 인원으로 대체"),
    ):
        rows = conn.execute(
            "SELECT period, value FROM metric_values WHERE partner_id = ? AND metric_code = ? "
            "AND value IS NOT NULL ORDER BY period",
            (partner_id, code),
        ).fetchall()
        if not rows:
            continue
        # 연도별 마지막 관측치를 그 해의 값으로 본다(연간 공시는 12월에 들어온다).
        by_year: dict[str, float] = {}
        for row in rows:
            if row["period"] <= period:
                by_year[row["period"][:4]] = row["value"]
        bars = [{"year": year, "value": by_year.get(year)} for year in wanted]
        if not any(bar["value"] is not None for bar in bars):
            continue

        # 막대 기하는 서버에서 계산해 템플릿은 그리기만 한다.
        top = max(bar["value"] for bar in bars if bar["value"] is not None) or 1
        count = len(bars)
        slot = CHART_WIDTH / count
        bar_width = min(56.0, slot * 0.52)
        for index, bar in enumerate(bars):
            ratio = (bar["value"] / top) if bar["value"] is not None else 0.0
            height = max(3.0, ratio * CHART_PLOT_HEIGHT) if bar["value"] is not None else 6.0
            bar["ratio"] = ratio
            bar["w"] = round(bar_width, 1)
            bar["x"] = round(slot * (index + 0.5) - bar_width / 2, 1)
            bar["h"] = round(height, 1)
            bar["y"] = round(CHART_PLOT_HEIGHT - height, 1)
            bar["cx"] = round(slot * (index + 0.5), 1)
            bar["latest"] = index == count - 1
            previous = bars[index - 1]["value"] if index else None
            bar["yoy"] = (
                round((bar["value"] - previous) / abs(previous) * 100, 1)
                if index and previous and bar["value"] is not None else None
            )

        first, last = bars[0]["value"], bars[-1]["value"]
        span = count - 1
        cagr = None
        if first and last is not None and first > 0 and last > 0 and span:
            cagr = round(((last / first) ** (1 / span) - 1) * 100, 1)
        return {
            "code": code,
            "label": label,
            "unit": unit,
            "note": note,
            "bars": bars,
            "width": CHART_WIDTH,
            "plot_height": CHART_PLOT_HEIGHT,
            "change": (
                round((last - first) / abs(first) * 100, 1)
                if first and last is not None else None
            ),
            "cagr": cagr,
        }
    return {
        "code": None, "label": "매출액", "unit": "억원", "note": "자료 없음",
        "bars": [], "change": None, "cagr": None,
        "width": CHART_WIDTH, "plot_height": CHART_PLOT_HEIGHT,
    }


def _normalize(values: list[float]) -> list[float]:
    """지표 원값을 스파크라인용 0~100으로 정규화한다(형태만 보기 위한 변환)."""
    low, high = min(values), max(values)
    if high == low:
        return [50.0 for _ in values]
    return [(value - low) / (high - low) * 100 for value in values]


@bp.route("/changes")
def changes_view():
    """협력사 변동이력 전용 화면. 픽토그램·신호등으로 읽고, 줄을 누르면 상세로 간다."""
    conn = get_conn()
    partner_id = request.args.get("partner", type=int)
    kind = request.args.get("kind") or ""
    severity = request.args.get("severity") or ""
    days = request.args.get("days", type=int) or 0
    page = max(request.args.get("page", type=int) or 1, 1)
    per_page = 60

    since = None
    if days:
        since = (date.today() - timedelta(days=days)).isoformat()

    rows = models.search_changes(
        conn, partner_id=partner_id, kind=kind or None, severity=severity or None,
        since=since, limit=per_page + 1, offset=(page - 1) * per_page,
    )
    has_next = len(rows) > per_page
    seen_id = _seen_change_id()

    response = make_response(render_template(
        "partners/changes.html",
        rows=rows[:per_page],
        counts=models.count_changes(conn),
        partners=models.list_partners(conn),
        category_labels=models.category_labels(conn),
        cat_class=_category_classes(conn),
        change_icons=CHANGE_ICONS,
        relation_labels=models.RELATION_LABELS,
        filters={
            "partner": partner_id, "kind": kind, "severity": severity,
            "days": days, "page": page,
        },
        has_next=has_next,
        seen_change_id=seen_id,
        ticker=models.list_changes(conn, limit=TICKER_LIMIT),
        new_change_count=sum(row["n"] for row in models.changed_partner_ids(conn, seen_id).values()),
        history_start=models.HISTORY_START,
    ))
    response.set_cookie(
        SEEN_COOKIE, str(models.max_change_id(conn)), max_age=SEEN_COOKIE_MAX_AGE, samesite="Lax"
    )
    return response


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    conn = get_conn()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            code = (request.form.get("code") or "").strip()
            if not code:
                flash("지표 코드를 입력해 주세요.")
                return redirect(url_for("partners.settings"))
            models.upsert_metric_def(
                conn,
                code=code,
                category=(request.form.get("category") or "기타").strip(),
                label=(request.form.get("label") or code).strip(),
                unit=(request.form.get("unit") or "").strip() or None,
                direction=request.form.get("direction") or "info",
                good_value=_as_float(request.form.get("good_value")),
                bad_value=_as_float(request.form.get("bad_value")),
                weight=_as_float(request.form.get("weight")) or 0,
                source=(request.form.get("source") or "").strip() or None,
                active=1,
                sort_order=int(_as_float(request.form.get("sort_order")) or 100),
            )
            flash(f"지표 '{code}'를 추가했습니다.")
        elif action == "add_category":
            code = (request.form.get("code") or "").strip()
            label = (request.form.get("label") or "").strip()
            if not code or not label:
                flash("분류 코드와 표시명을 모두 입력해 주세요.")
                return redirect(url_for("partners.settings"))
            models.upsert_category(
                conn, code, label, int(_as_float(request.form.get("sort_order")) or 100)
            )
            flash(f"업종 분류 '{label}'을 추가했습니다.")
        elif action == "update_categories":
            for category in models.list_categories(conn, active_only=False):
                code = category["code"]
                models.upsert_category(
                    conn,
                    code,
                    (request.form.get(f"label__{code}") or category["label"]).strip(),
                    int(_as_float(request.form.get(f"order__{code}")) or category["sort_order"]),
                    bool(request.form.get(f"cat_active__{code}")),
                )
            flash("업종 분류를 저장했습니다.")
        elif action == "add_profile_field":
            code = (request.form.get("code") or "").strip()
            label = (request.form.get("label") or "").strip()
            if not code or not label:
                flash("개요 항목의 코드와 표시명을 입력해 주세요.")
                return redirect(url_for("partners.settings"))
            models.upsert_profile_field(
                conn, code=code, label=label,
                kind=request.form.get("kind") or "text",
                source=(request.form.get("source") or "manual").strip(),
                active=1,
                sort_order=int(_as_float(request.form.get("sort_order")) or 100),
            )
            flash(f"기업개요 항목 '{label}'을 추가했습니다.")
        elif action == "update_profile_fields":
            for field in models.list_profile_fields(conn, active_only=False):
                code = field["code"]
                models.upsert_profile_field(
                    conn, code=code,
                    label=(request.form.get(f"plabel__{code}") or field["label"]).strip(),
                    kind=field["kind"], source=field["source"],
                    active=1 if request.form.get(f"pactive__{code}") else 0,
                    sort_order=int(_as_float(request.form.get(f"porder__{code}")) or field["sort_order"]),
                )
            flash("기업개요 항목을 저장했습니다.")
        elif action == "add_partner":
            name = (request.form.get("name") or "").strip()
            biz_no = (request.form.get("biz_no") or "").strip().replace("-", "")
            if not name or not biz_no:
                flash("협력사명과 사업자등록번호를 입력해 주세요.")
                return redirect(url_for("partners.settings"))
            models.upsert_partner(
                conn,
                name=name,
                biz_no=biz_no,
                category_code=(request.form.get("category_code") or "").strip() or None,
                relation=(request.form.get("relation") or "external").strip(),
                group_name=(request.form.get("group_name") or "").strip() or None,
                industry=(request.form.get("industry") or "").strip() or None,
                manager=(request.form.get("manager") or "").strip() or None,
                tier=(request.form.get("tier") or "").strip() or None,
                dart_corp_code=(request.form.get("dart_corp_code") or "").strip() or None,
                profile="healthy",
            )
            flash(f"협력사 '{name}'을 등록했습니다. 수집을 실행하면 지표가 채워집니다.")
        elif action == "update":
            for defn in models.list_metric_defs(conn, active_only=False):
                code = defn["code"]
                models.upsert_metric_def(
                    conn,
                    code=code,
                    category=defn["category"],
                    label=defn["label"],
                    unit=defn["unit"],
                    direction=request.form.get(f"direction__{code}") or defn["direction"],
                    good_value=_as_float(request.form.get(f"good__{code}")),
                    bad_value=_as_float(request.form.get(f"bad__{code}")),
                    weight=_as_float(request.form.get(f"weight__{code}")) or 0,
                    source=defn["source"],
                    active=1 if request.form.get(f"active__{code}") else 0,
                    sort_order=defn["sort_order"],
                )
            flash("지표 설정을 저장했습니다.")
        return redirect(url_for("partners.settings"))

    defs = models.list_metric_defs(conn, active_only=False)
    total_weight = sum(float(defn["weight"] or 0) for defn in defs if defn["active"])
    return render_template(
        "partners/settings.html",
        defs=defs,
        total_weight=total_weight,
        collectors=collector_status(),
        runs=models.list_runs(conn, limit=10),
        categories=CATEGORY_ORDER,
        partner_categories=models.list_categories(conn, active_only=False),
        category_counts=models.count_partners_by_category(conn),
        profile_fields=models.list_profile_fields(conn, active_only=False),
        groups=models.list_groups(conn),
        relation_labels=models.RELATION_LABELS,
        history_start=models.HISTORY_START,
    )


@bp.route("/collect", methods=["POST"])
def collect():
    conn = get_conn()
    months = request.form.get("months")
    sources = request.form.getlist("sources") or None
    # 기본은 이력 관리 시작(2023-01)부터 현재까지 전 구간을 다시 맞춘다.
    periods = (
        models.recent_periods(models.current_period(), int(months))
        if months else models.history_periods()
    )
    summaries = run_collection(conn, periods, sources)
    for summary in summaries:
        mode = "실 API" if summary["mode"] == "live" else "목업"
        note = f" ({summary['message']})" if summary["message"] else ""
        flash(f"{summary['label']} [{mode}] {summary['records']}건 적재{note}")
    return redirect(request.form.get("next") or url_for("partners.index"))


@bp.route("/seed", methods=["POST"])
def seed():
    from .seed import seed_all

    conn = get_conn()
    months = request.form.get("months")
    seed_all(conn, months=int(months) if months else None)
    flash(f"샘플 협력사와 {models.HISTORY_START} 이후 이력을 채웠습니다.")
    return redirect(url_for("partners.index"))


@bp.route("/api/partners.json")
def api_partners():
    conn = get_conn()
    periods = models.known_periods(conn, limit=1)
    period = request.args.get("period") or (periods[-1] if periods else models.current_period())
    labels = models.category_labels(conn)
    payload = []
    for row in build_rows(conn, period):
        result = row["score"]
        code = row["partner"]["category_code"]
        payload.append(
            {
                "id": row["partner"]["id"],
                "name": row["partner"]["name"],
                "biz_no": row["partner"]["biz_no"],
                "relation": row["partner"]["relation"],
                "group": row["partner"]["group_name"],
                "category_code": code,
                "category": labels.get(code),
                "grade": result.grade,
                "signal": result.signal,
                "score": round(result.score, 1) if result.score is not None else None,
                "critical_reasons": result.critical_reasons,
                "coverage": round(result.coverage, 3),
                "grade_cap": result.grade_cap,
                "sources": result.sources,
                "metrics": {
                    metric.code: {"value": metric.value, "text": metric.text, "signal": metric.signal}
                    for metric in result.metrics
                },
                "address": row["address"],
                "last_collected_at": result.last_collected_at,
            }
        )
    return jsonify({"period": period, "partners": payload})


@bp.route("/api/changes.json")
def api_changes():
    """뉴스 티커용 최근 변경. 다른 시스템·알림봇에서도 그대로 쓸 수 있다."""
    conn = get_conn()
    since = request.args.get("since", type=int) or 0
    changes = models.list_changes(conn, limit=request.args.get("limit", type=int) or 40, since_id=since)
    return jsonify(
        {
            "latest_id": models.max_change_id(conn),
            "changes": [
                {
                    "id": row["id"],
                    "partner_id": row["partner_id"],
                    "partner": row["partner_name"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "detail": row["detail"],
                    "severity": row["severity"],
                    "created_at": row["created_at"],
                    "url": url_for("partners.detail", partner_id=row["partner_id"], _anchor=row["anchor"] or None),
                }
                for row in changes
            ],
        }
    )


def _as_float(raw) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def init_app(app) -> None:
    """변환기 앱에 대시보드를 붙인다."""
    app.config.setdefault("PARTNERS_DB", os.environ.get("PARTNERS_DB", DEFAULT_DB_PATH))
    Path(app.config["PARTNERS_DB"]).parent.mkdir(parents=True, exist_ok=True)
    app.register_blueprint(bp)
