"""협력사 모니터링 대시보드 화면(Blueprint)."""

import os
import sqlite3
from pathlib import Path

from flask import (
    Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for,
)

from . import models
from .collectors import collector_status, run_collection
from .scoring import PartnerScore, score_partner

bp = Blueprint("partners", __name__, url_prefix="/partners")

TREND_MONTHS = 6
TREND_WIDTH, TREND_HEIGHT = 72, 22
DETAIL_TREND_WIDTH, DETAIL_TREND_HEIGHT = 240, 48
METRIC_SPARK_WIDTH, METRIC_SPARK_HEIGHT = 60, 18
DEFAULT_DB_PATH = "instance/partners.db"

SIGNAL_LABELS = {"green": "정상", "amber": "주의", "red": "경고", "none": "자료없음"}
# 리스트의 카테고리 컬럼 순서. 지표를 추가하면 해당 카테고리 컬럼에 자동으로 붙는다.
CATEGORY_ORDER = ("기업상태", "재무", "매출", "인원", "경영환경")


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
    rows = []
    for partner in models.list_partners(conn):
        events = models.list_risk_events(conn, partner["id"])
        values = models.values_as_of(conn, partner["id"], period)
        result = score_partner(defs, values, events)
        trend = _trend_scores(conn, partner["id"], defs, events, trend_periods)
        rows.append(
            {
                "partner": partner,
                "score": result,
                "trend": trend,
                "trend_points": sparkline(trend, TREND_WIDTH, TREND_HEIGHT),
                "trend_delta": round(trend[-1] - trend[0], 1) if len(trend) >= 2 else None,
                "events": events,
            }
        )
    return rows


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
    periods = models.known_periods(conn, limit=24)
    period = request.args.get("period") or (periods[-1] if periods else models.current_period())
    rows = build_rows(conn, period)

    query = (request.args.get("q") or "").strip()
    category = request.args.get("category") or ""
    coverage = request.args.get("coverage") or ""
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
    if coverage == "weak":
        rows = [row for row in rows if _is_weak(row)]
    if signal:
        rows = [row for row in rows if row["score"].signal == signal]
    if grade:
        rows = [row for row in rows if row["score"].grade == grade]
    if manager:
        rows = [row for row in rows if (row["partner"]["manager"] or "") == manager]

    rows.sort(key=lambda row: _sort_key(row, order))

    all_rows = build_rows(conn, period)
    summary = {
        "total": len(all_rows),
        "red": sum(1 for row in all_rows if row["score"].signal == "red"),
        "amber": sum(1 for row in all_rows if row["score"].signal == "amber"),
        "green": sum(1 for row in all_rows if row["score"].signal == "green"),
        "critical": sum(1 for row in all_rows if row["score"].critical_reasons),
        "weak": sum(1 for row in all_rows if _is_weak(row)),
        "stale": sum(1 for row in all_rows if row["score"].stale),
    }
    managers = sorted({row["partner"]["manager"] for row in all_rows if row["partner"]["manager"]})
    categories_meta = models.list_categories(conn)
    category_summary = _category_summary(all_rows, categories_meta)

    return render_template(
        "partners/list.html",
        rows=rows,
        summary=summary,
        managers=managers,
        periods=periods,
        period=period,
        categories=CATEGORY_ORDER,
        signal_labels=SIGNAL_LABELS,
        filters={
            "q": query, "category": category, "coverage": coverage, "signal": signal,
            "grade": grade, "manager": manager, "order": order,
        },
        category_labels=models.category_labels(conn),
        category_options=categories_meta,
        category_summary=category_summary,
        collectors=collector_status(),
        runs=models.list_runs(conn, limit=5),
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
        submissions=models.list_submissions(conn, partner_id),
        doc_types=models.REQUIRED_DOC_TYPES + models.OPTIONAL_DOC_TYPES,
        metric_defs=defs,
        recent_period_options=models.recent_periods(period, 12)[::-1],
    )


def _normalize(values: list[float]) -> list[float]:
    """지표 원값을 스파크라인용 0~100으로 정규화한다(형태만 보기 위한 변환)."""
    low, high = min(values), max(values)
    if high == low:
        return [50.0 for _ in values]
    return [(value - low) / (high - low) * 100 for value in values]


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
    )


@bp.route("/collect", methods=["POST"])
def collect():
    conn = get_conn()
    months = int(request.form.get("months") or 12)
    sources = request.form.getlist("sources") or None
    periods = models.recent_periods(models.current_period(), months)
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
    seed_all(conn, months=int(request.form.get("months") or 12))
    flash("샘플 협력사와 최근 12개월 데이터를 채웠습니다.")
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
                "name": row["partner"]["name"],
                "biz_no": row["partner"]["biz_no"],
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
                "last_collected_at": result.last_collected_at,
            }
        )
    return jsonify({"period": period, "partners": payload})


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
