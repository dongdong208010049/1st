"""협력사 모니터링 데이터 저장소(SQLite).

지표는 코드에 고정하지 않고 metric_defs 테이블에 '정의'로 저장한다.
항목을 추가할 때 스키마 변경 없이 행만 추가하면 리스트/상세/점수에 반영된다.
"""

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS partner_categories (
    code        TEXT PRIMARY KEY,          -- 업종 분류 코드 (mold, production, jig ...)
    label       TEXT NOT NULL,             -- 화면 표시명 (금형, 양산처, 지그 ...)
    sort_order  INTEGER NOT NULL DEFAULT 100,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS partners (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    biz_no      TEXT NOT NULL UNIQUE,      -- 사업자등록번호(하이픈 없음)
    corp_no     TEXT,                      -- 법인등록번호
    dart_corp_code TEXT,                   -- DART 고유번호(8자리)
    category_code TEXT REFERENCES partner_categories(code),  -- 업(業) 분류
    industry    TEXT,                      -- 세부 업종 메모
    manager     TEXT,                      -- 내부 담당자
    tier        TEXT,                       -- 공급 등급/구분
    profile     TEXT DEFAULT 'healthy',    -- 목업 수집기용 시나리오 라벨(실 API 연동 시 무시)
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metric_defs (
    code        TEXT PRIMARY KEY,
    category    TEXT NOT NULL,             -- 재무 / 매출 / 인원 / 경영환경 ...
    label       TEXT NOT NULL,
    unit        TEXT,
    direction   TEXT NOT NULL,             -- higher_better | lower_better | info
    good_value  REAL,                      -- 100점에 해당하는 값
    bad_value   REAL,                      -- 0점에 해당하는 값
    weight      REAL NOT NULL DEFAULT 0,
    source      TEXT,                      -- 수집 소스 키
    active      INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS metric_values (
    partner_id   INTEGER NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    metric_code  TEXT NOT NULL REFERENCES metric_defs(code) ON DELETE CASCADE,
    period       TEXT NOT NULL,            -- 'YYYY-MM'
    value        REAL,
    text_value   TEXT,
    source       TEXT,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (partner_id, metric_code, period)
);

CREATE TABLE IF NOT EXISTS risk_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id  INTEGER NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,             -- 임금체불 / 산재 / 제재 / 공시 ...
    title       TEXT NOT NULL,
    occurred_on TEXT NOT NULL,             -- 'YYYY-MM-DD'
    severity    TEXT NOT NULL DEFAULT 'info',   -- info | warn | critical
    source      TEXT,
    url         TEXT,
    UNIQUE (partner_id, kind, title, occurred_on)
);

CREATE TABLE IF NOT EXISTS submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id   INTEGER NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    doc_type     TEXT NOT NULL,            -- 표준재무제표증명 / 납세증명서 / 4대보험 완납증명 ...
    period       TEXT,                     -- 대상 기간 메모 (예: 2025 회계연도)
    submitted_on TEXT NOT NULL,            -- 'YYYY-MM-DD'
    note         TEXT,
    recorded_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    mode        TEXT NOT NULL,             -- live | mock
    status      TEXT NOT NULL,             -- ok | error
    records     INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_values_partner ON metric_values(partner_id, period);
CREATE INDEX IF NOT EXISTS idx_events_partner ON risk_events(partner_id, occurred_on);
CREATE INDEX IF NOT EXISTS idx_submissions_partner ON submissions(partner_id, submitted_on);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """이미 만들어진 DB에 뒤늦게 추가된 컬럼을 채운다."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(partners)")}
    if "category_code" not in columns:
        conn.execute("ALTER TABLE partners ADD COLUMN category_code TEXT")


# --- 협력사 -----------------------------------------------------------------


def upsert_partner(conn: sqlite3.Connection, **fields) -> int:
    biz_no = fields["biz_no"]
    row = conn.execute("SELECT id FROM partners WHERE biz_no = ?", (biz_no,)).fetchone()
    columns = (
        "name", "biz_no", "corp_no", "dart_corp_code",
        "category_code", "industry", "manager", "tier", "profile",
    )
    values = {key: fields.get(key) for key in columns}
    if row:
        assignments = ", ".join(f"{key} = :{key}" for key in columns)
        conn.execute(f"UPDATE partners SET {assignments} WHERE id = :id", {**values, "id": row["id"]})
        conn.commit()
        return int(row["id"])
    placeholders = ", ".join(f":{key}" for key in columns)
    cursor = conn.execute(
        f"INSERT INTO partners ({', '.join(columns)}, created_at) VALUES ({placeholders}, :created_at)",
        {**values, "created_at": now_iso()},
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_partners(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM partners ORDER BY name").fetchall()


def get_partner(conn: sqlite3.Connection, partner_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()


def update_partner_fields(conn: sqlite3.Connection, partner_id: int, **fields) -> None:
    """상세 화면에서 고칠 수 있는 항목만 갱신한다."""
    editable = ("name", "category_code", "industry", "manager", "tier", "corp_no", "dart_corp_code")
    updates = {key: fields[key] for key in editable if key in fields}
    if not updates:
        return
    assignments = ", ".join(f"{key} = :{key}" for key in updates)
    conn.execute(f"UPDATE partners SET {assignments} WHERE id = :id", {**updates, "id": partner_id})
    conn.commit()


# --- 업종 카테고리 ----------------------------------------------------------


def upsert_category(conn: sqlite3.Connection, code: str, label: str, sort_order: int = 100, active: bool = True) -> None:
    conn.execute(
        "INSERT INTO partner_categories (code, label, sort_order, active) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(code) DO UPDATE SET label = excluded.label, "
        "sort_order = excluded.sort_order, active = excluded.active",
        (code, label, int(sort_order), 1 if active else 0),
    )
    conn.commit()


def list_categories(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    query = "SELECT * FROM partner_categories"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY sort_order, label"
    return conn.execute(query).fetchall()


def category_labels(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["code"]: row["label"] for row in list_categories(conn, active_only=False)}


def count_partners_by_category(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT COALESCE(category_code, '') AS code, COUNT(*) AS n FROM partners GROUP BY code"
    ).fetchall()
    return {row["code"]: row["n"] for row in rows}


# --- 지표 정의 --------------------------------------------------------------


def upsert_metric_def(conn: sqlite3.Connection, **fields) -> None:
    columns = (
        "code", "category", "label", "unit", "direction",
        "good_value", "bad_value", "weight", "source", "active", "sort_order",
    )
    values = {key: fields.get(key) for key in columns}
    # active를 생략하면 활성으로 본다.
    raw_active = fields.get("active", 1)
    values["active"] = 0 if raw_active in (0, False, "0", "off", "false") else 1
    values["weight"] = float(values.get("weight") or 0)
    values["sort_order"] = int(values.get("sort_order") or 100)
    conn.execute(
        f"INSERT INTO metric_defs ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)}) "
        "ON CONFLICT(code) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in columns if c != "code"),
        values,
    )
    conn.commit()


def list_metric_defs(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    query = "SELECT * FROM metric_defs"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY sort_order, code"
    return conn.execute(query).fetchall()


def set_metric_active(conn: sqlite3.Connection, code: str, active: bool) -> None:
    conn.execute("UPDATE metric_defs SET active = ? WHERE code = ?", (1 if active else 0, code))
    conn.commit()


# --- 측정값 -----------------------------------------------------------------


def put_metric_value(
    conn: sqlite3.Connection,
    partner_id: int,
    metric_code: str,
    period: str,
    value: float | None,
    text_value: str | None = None,
    source: str | None = None,
    collected_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO metric_values (partner_id, metric_code, period, value, text_value, source, collected_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(partner_id, metric_code, period) DO UPDATE SET "
        "value = excluded.value, text_value = excluded.text_value, "
        "source = excluded.source, collected_at = excluded.collected_at",
        (partner_id, metric_code, period, value, text_value, source, collected_at or now_iso()),
    )


def values_as_of(conn: sqlite3.Connection, partner_id: int, period: str) -> dict[str, sqlite3.Row]:
    """기준 기간(period) 이하에서 지표별 가장 최근 값을 돌려준다."""
    rows = conn.execute(
        "SELECT v.* FROM metric_values v "
        "JOIN (SELECT metric_code, MAX(period) AS period FROM metric_values "
        "      WHERE partner_id = ? AND period <= ? GROUP BY metric_code) latest "
        "  ON v.metric_code = latest.metric_code AND v.period = latest.period "
        "WHERE v.partner_id = ?",
        (partner_id, period, partner_id),
    ).fetchall()
    return {row["metric_code"]: row for row in rows}


def value_history(conn: sqlite3.Connection, partner_id: int, metric_code: str, limit: int = 24) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM metric_values WHERE partner_id = ? AND metric_code = ? "
        "ORDER BY period DESC LIMIT ?",
        (partner_id, metric_code, limit),
    ).fetchall()
    return list(reversed(rows))


def known_periods(conn: sqlite3.Connection, limit: int = 12) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT period FROM metric_values ORDER BY period DESC LIMIT ?", (limit,)
    ).fetchall()
    return [row["period"] for row in reversed(rows)]


# --- 리스크 이벤트 / 수집 로그 ----------------------------------------------


def add_risk_event(conn: sqlite3.Connection, partner_id: int, **fields) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO risk_events (partner_id, kind, title, occurred_on, severity, source, url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            partner_id,
            fields["kind"],
            fields["title"],
            fields["occurred_on"],
            fields.get("severity", "info"),
            fields.get("source"),
            fields.get("url"),
        ),
    )


def list_risk_events(conn: sqlite3.Connection, partner_id: int | None = None, limit: int = 50) -> list[sqlite3.Row]:
    if partner_id is None:
        return conn.execute(
            "SELECT e.*, p.name AS partner_name FROM risk_events e JOIN partners p ON p.id = e.partner_id "
            "ORDER BY e.occurred_on DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM risk_events WHERE partner_id = ? ORDER BY occurred_on DESC LIMIT ?",
        (partner_id, limit),
    ).fetchall()


# 소규모(비외감) 협력사에서 재무를 확인할 수 있는 정기 징구 자료.
# 표준재무제표증명·부가세 과세표준증명은 국세청 발급본이라 협력사가 꾸며낼 수 없다.
REQUIRED_DOC_TYPES = (
    "표준재무제표증명",
    "부가세 과세표준증명",
    "납세증명서",
    "4대보험 완납증명",
)
OPTIONAL_DOC_TYPES = ("신용평가서", "기타")


def add_submission(conn: sqlite3.Connection, partner_id: int, **fields) -> int:
    cursor = conn.execute(
        "INSERT INTO submissions (partner_id, doc_type, period, submitted_on, note, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            partner_id,
            fields["doc_type"],
            fields.get("period"),
            fields["submitted_on"],
            fields.get("note"),
            now_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_submissions(conn: sqlite3.Connection, partner_id: int, limit: int = 30) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM submissions WHERE partner_id = ? ORDER BY submitted_on DESC LIMIT ?",
        (partner_id, limit),
    ).fetchall()


def latest_submission(conn: sqlite3.Connection, partner_id: int, doc_types=REQUIRED_DOC_TYPES) -> sqlite3.Row | None:
    placeholders = ", ".join("?" for _ in doc_types)
    return conn.execute(
        f"SELECT * FROM submissions WHERE partner_id = ? AND doc_type IN ({placeholders}) "
        "ORDER BY submitted_on DESC LIMIT 1",
        (partner_id, *doc_types),
    ).fetchone()


def start_run(conn: sqlite3.Connection, source: str, mode: str) -> int:
    cursor = conn.execute(
        "INSERT INTO collection_runs (source, mode, status, started_at) VALUES (?, ?, 'running', ?)",
        (source, mode, now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, records: int, message: str | None = None) -> None:
    conn.execute(
        "UPDATE collection_runs SET status = ?, records = ?, message = ?, finished_at = ? WHERE id = ?",
        (status, records, message, now_iso(), run_id),
    )
    conn.commit()


def list_runs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM collection_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def current_period(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.year:04d}-{today.month:02d}"


def shift_period(period: str, months: int) -> str:
    year, month = (int(part) for part in period.split("-"))
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def recent_periods(period: str, count: int) -> list[str]:
    return [shift_period(period, -offset) for offset in range(count - 1, -1, -1)]
