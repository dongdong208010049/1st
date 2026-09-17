"""수집기 레지스트리와 수집 실행기."""

from .. import models
from ..scoring import score_metric, signal_of
from .base import Collector, CollectorError, CollectResult, Event, ProfileFact, Reading
from .credit import CreditCollector
from .dart import DartCollector
from .factory import FactoryCollector, fill_worker_gap
from .health import HealthInsuranceCollector, fill_insured_gap
from .internal import InternalTradeCollector
from .ipr import IprCollector
from .insurance import InsuranceCollector, fill_changes
from .nts import NtsCollector
from .procurement import ProcurementCollector
from .risk_list import RiskListCollector
from .submission import SubmissionCollector

# 실행 순서가 의미를 갖는다: 연금(인원) → 공장·건보(교차검증) 순으로 둔다.
COLLECTORS: tuple[Collector, ...] = (
    DartCollector(),
    CreditCollector(),
    InsuranceCollector(),
    NtsCollector(),
    FactoryCollector(),
    HealthInsuranceCollector(),
    ProcurementCollector(),
    IprCollector(),
    InternalTradeCollector(),
    RiskListCollector(),
    SubmissionCollector(),
)

__all__ = [
    "COLLECTORS",
    "Collector",
    "CollectorError",
    "CollectResult",
    "Event",
    "ProfileFact",
    "Reading",
    "collector_status",
    "run_collection",
]

# 지표 신호가 이 방향으로 바뀌면 티커에 올린다.
_SIGNAL_RANK = {"green": 0, "amber": 1, "red": 2, "none": -1}


def collector_status() -> list[dict]:
    return [
        {
            "source": collector.source,
            "label": collector.label,
            "mode": collector.mode(),
            "env_keys": collector.env_keys,
            "metric_codes": collector.metric_codes,
        }
        for collector in COLLECTORS
    ]


def run_collection(conn, periods: list[str], sources: list[str] | None = None) -> list[dict]:
    """선택한 소스를 전 협력사에 대해 수집·저장하고 실행 요약을 돌려준다.

    값이 실제로 바뀐 것만 change_log에 남긴다(뉴스 티커와 '변경 강조'의 원천).
    첫 적재는 변경으로 보지 않는다.
    """
    partners = models.list_partners(conn)
    metric_defs = {row["code"]: row for row in models.list_metric_defs(conn, active_only=False)}
    latest_period = periods[-1]
    summaries: list[dict] = []

    for collector in COLLECTORS:
        if sources and collector.source not in sources:
            continue
        run_id = models.start_run(conn, collector.source, collector.mode())
        records = 0
        failures: list[str] = []

        for partner in partners:
            try:
                result = collector.collect(partner, periods, conn)
            except CollectorError as exc:
                failures.append(str(exc))
                continue

            for reading in result.readings:
                if reading.period == latest_period:
                    _log_metric_change(conn, partner, reading, metric_defs.get(reading.code))
                models.put_metric_value(
                    conn,
                    partner["id"],
                    reading.code,
                    reading.period,
                    reading.value,
                    reading.text,
                    collector.source,
                )
                records += 1

            # 실 API가 당월 값만 주는 경우, 저장된 시계열로 증감 지표를 보완한다.
            if collector.source == "insurance":
                history = {
                    code: [
                        (row["period"], row["value"])
                        for row in models.value_history(conn, partner["id"], code, limit=36)
                        if row["value"] is not None
                    ]
                    for code in ("headcount", "avg_pay")
                }
                for reading in fill_changes(history):
                    models.put_metric_value(
                        conn, partner["id"], reading.code, reading.period,
                        reading.value, reading.text, collector.source,
                    )

            # 교차검증 지표는 두 소스가 다 모인 뒤 계산한다.
            if collector.source in ("factory", "health"):
                derive = fill_worker_gap if collector.source == "factory" else fill_insured_gap
                for reading in derive(conn, models, partner["id"], periods):
                    models.put_metric_value(
                        conn, partner["id"], reading.code, reading.period,
                        reading.value, reading.text, collector.source,
                    )

            for fact in result.profiles:
                previous = models.put_profile_value(
                    conn, partner["id"], fact.code, fact.value, fact.valid_from, collector.source
                )
                if previous:  # 빈 문자열(최초 등록)은 변경으로 보지 않는다
                    label = (metric_or_field_label(conn, fact.code))
                    models.add_change(
                        conn, partner["id"], "개요",
                        f"{label} 변경",
                        f"{previous} → {fact.value}",
                        severity="warn",
                        anchor="overview",
                    )

            for event in result.events:
                before = conn.total_changes
                models.add_risk_event(
                    conn,
                    partner["id"],
                    kind=event.kind,
                    title=event.title,
                    occurred_on=event.occurred_on,
                    severity=event.severity,
                    url=event.url,
                )
                # INSERT OR IGNORE라 새로 들어온 사건만 티커에 올린다.
                if conn.total_changes > before:
                    models.add_change(
                        conn, partner["id"], "사건", f"{event.kind}: {event.title}",
                        event.occurred_on, severity=event.severity, anchor="events",
                    )
            conn.commit()

        status = "error" if failures and records == 0 else "ok"
        message = None
        if failures:
            message = f"{len(failures)}건 실패: " + "; ".join(failures[:3])
        models.finish_run(conn, run_id, status, records, message)
        summaries.append(
            {
                "source": collector.source,
                "label": collector.label,
                "mode": collector.mode(),
                "records": records,
                "status": status,
                "message": message,
            }
        )

    return summaries


def metric_or_field_label(conn, code: str) -> str:
    row = conn.execute("SELECT label FROM profile_fields WHERE code = ?", (code,)).fetchone()
    if row:
        return row["label"]
    row = conn.execute("SELECT label FROM metric_defs WHERE code = ?", (code,)).fetchone()
    return row["label"] if row else code


def _log_metric_change(conn, partner, reading: Reading, defn) -> None:
    """지표의 신호등이 바뀌었을 때만 기록한다(값 미세 변동은 무시)."""
    if defn is None or reading.value is None:
        return
    row = conn.execute(
        "SELECT value, text_value FROM metric_values "
        "WHERE partner_id = ? AND metric_code = ? AND period = ?",
        (partner["id"], reading.code, reading.period),
    ).fetchone()
    if row is None or row["value"] is None:
        return  # 최초 적재

    before, after = row["value"], reading.value
    if before == after:
        return

    before_signal = signal_of(score_metric(defn, before))
    after_signal = signal_of(score_metric(defn, after))
    text_changed = (row["text_value"] or "") != (reading.text or "")

    if before_signal == after_signal and not (text_changed and defn["direction"] != "info"):
        return

    worsened = _SIGNAL_RANK.get(after_signal, -1) > _SIGNAL_RANK.get(before_signal, -1)
    unit = defn["unit"] or ""
    detail = f"{_fmt(before)}{unit} → {_fmt(after)}{unit}"
    if reading.text:
        detail = f"{reading.text} ({detail})"

    kind = "상태" if defn["weight"] == 0 else "지표"
    severity = "critical" if (worsened and after_signal == "red") else ("warn" if worsened else "good")
    models.add_change(
        conn, partner["id"], kind, f"{defn['label']} {'악화' if worsened else '개선'}",
        detail, severity=severity, anchor=f"metric-{reading.code}",
    )


def _fmt(value: float) -> str:
    return f"{value:,.1f}".rstrip("0").rstrip(".")
