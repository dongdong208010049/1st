"""수집기 레지스트리와 수집 실행기."""

from .. import models
from .base import Collector, CollectorError, CollectResult, Event, Reading
from .dart import DartCollector
from .insurance import InsuranceCollector, fill_headcount_change
from .nts import NtsCollector
from .risk_list import RiskListCollector

COLLECTORS: tuple[Collector, ...] = (
    DartCollector(),
    InsuranceCollector(),
    NtsCollector(),
    RiskListCollector(),
)

__all__ = [
    "COLLECTORS",
    "Collector",
    "CollectorError",
    "CollectResult",
    "Event",
    "Reading",
    "collector_status",
    "run_collection",
]


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
    """선택한 소스를 전 협력사에 대해 수집·저장하고 실행 요약을 돌려준다."""
    partners = models.list_partners(conn)
    summaries: list[dict] = []

    for collector in COLLECTORS:
        if sources and collector.source not in sources:
            continue
        run_id = models.start_run(conn, collector.source, collector.mode())
        records = 0
        failures: list[str] = []

        for partner in partners:
            try:
                result = collector.collect(partner, periods)
            except CollectorError as exc:
                failures.append(str(exc))
                continue

            for reading in result.readings:
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

            # 실 API가 당월 인원만 주는 경우, 저장된 시계열로 3개월 증감률을 보완한다.
            if collector.source == "insurance":
                history = [
                    (row["period"], row["value"])
                    for row in models.value_history(conn, partner["id"], "headcount")
                    if row["value"] is not None
                ]
                for reading in fill_headcount_change(history):
                    models.put_metric_value(
                        conn, partner["id"], reading.code, reading.period,
                        reading.value, reading.text, collector.source,
                    )

            for event in result.events:
                models.add_risk_event(
                    conn,
                    partner["id"],
                    kind=event.kind,
                    title=event.title,
                    occurred_on=event.occurred_on,
                    severity=event.severity,
                    url=event.url,
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
