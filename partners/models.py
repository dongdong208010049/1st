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
    relation    TEXT NOT NULL DEFAULT 'external',  -- external(외부 협력사) | affiliate(계열사)
    group_name  TEXT,                      -- 기업집단·계열 그룹명 (계열사 묶음)
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

CREATE TABLE IF NOT EXISTS profile_fields (
    code        TEXT PRIMARY KEY,          -- address, ceo_name, established_on ...
    label       TEXT NOT NULL,             -- 주소, 대표자, 설립일 ...
    kind        TEXT NOT NULL DEFAULT 'text',   -- text | date | number
    source      TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 100
);

-- 개요는 덮어쓰지 않고 쌓는다. 대표자 변경·본점 이전 이력이 그대로 남는다.
CREATE TABLE IF NOT EXISTS profile_values (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id  INTEGER NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    field_code  TEXT NOT NULL REFERENCES profile_fields(code) ON DELETE CASCADE,
    value       TEXT,
    valid_from  TEXT NOT NULL,             -- 'YYYY-MM-DD' 확인일
    source      TEXT,
    recorded_at TEXT NOT NULL
);

-- 뉴스 티커와 '변경 강조'의 원천. 수집에서 값이 실제로 바뀔 때만 쌓인다.
CREATE TABLE IF NOT EXISTS change_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id  INTEGER NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,             -- 상태 | 사건 | 지표 | 개요 | 신규
    title       TEXT NOT NULL,
    detail      TEXT,
    severity    TEXT NOT NULL DEFAULT 'info',   -- info | warn | critical | good
    anchor      TEXT,                      -- 상세 화면 앵커(#finance 등)
    created_at  TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_profile_values ON profile_values(partner_id, field_code, valid_from);
CREATE INDEX IF NOT EXISTS idx_change_log ON change_log(id DESC);
"""


# 현황 이력은 2023년 1월부터 관리한다(폐업·휴업 이력 포함).
HISTORY_START = "2023-01"


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
    if "relation" not in columns:
        conn.execute("ALTER TABLE partners ADD COLUMN relation TEXT NOT NULL DEFAULT 'external'")
    if "group_name" not in columns:
        conn.execute("ALTER TABLE partners ADD COLUMN group_name TEXT")


# --- 협력사 -----------------------------------------------------------------


def upsert_partner(conn: sqlite3.Connection, **fields) -> int:
    biz_no = fields["biz_no"]
    row = conn.execute("SELECT id FROM partners WHERE biz_no = ?", (biz_no,)).fetchone()
    columns = (
        "name", "biz_no", "corp_no", "dart_corp_code",
        "category_code", "relation", "group_name", "industry", "manager", "tier", "profile",
    )
    values = {key: fields.get(key) for key in columns}
    values["relation"] = values.get("relation") or "external"
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
    editable = (
        "name", "category_code", "relation", "group_name",
        "industry", "manager", "tier", "corp_no", "dart_corp_code",
    )
    updates = {key: fields[key] for key in editable if key in fields}
    if not updates:
        return
    assignments = ", ".join(f"{key} = :{key}" for key in updates)
    conn.execute(f"UPDATE partners SET {assignments} WHERE id = :id", {**updates, "id": partner_id})
    conn.commit()


RELATION_LABELS = {"external": "외부 협력사", "affiliate": "계열사"}


def list_groups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """기업집단(계열 그룹)별 협력사 수."""
    return conn.execute(
        "SELECT group_name, COUNT(*) AS n FROM partners "
        "WHERE group_name IS NOT NULL AND group_name != '' "
        "GROUP BY group_name ORDER BY n DESC, group_name"
    ).fetchall()


def group_members(conn: sqlite3.Connection, group_name: str, exclude_id: int | None = None) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM partners WHERE group_name = ? ORDER BY name", (group_name,)
    ).fetchall()
    return [row for row in rows if row["id"] != exclude_id]


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


def months_between(from_period: str, to_period: str) -> int:
    from_year, from_month = (int(part) for part in from_period.split("-"))
    to_year, to_month = (int(part) for part in to_period.split("-"))
    return (to_year * 12 + to_month) - (from_year * 12 + from_month)


def history_periods(until: str | None = None, start: str = HISTORY_START) -> list[str]:
    """이력 관리 시작(2023-01)부터 기준월까지의 모든 기간."""
    until = until or current_period()
    span = months_between(start, until) + 1
    return [shift_period(start, offset) for offset in range(max(span, 1))]


# --- 기업개요(이력형) ----------------------------------------------------------


# 개요 항목의 기본 표시명. 수집기가 새 코드를 들고 오면 이 이름으로 자동 등록된다.
DEFAULT_PROFILE_LABELS = {
    "address": ("주소", "text", 10),
    "ceo_name": ("대표자", "text", 20),
    "established_on": ("설립일", "date", 30),
    "main_product": ("주요 생산품", "text", 40),
    "phone": ("전화", "text", 50),
    "homepage": ("홈페이지", "text", 60),
    "latitude": ("위도", "number", 90),
    "longitude": ("경도", "number", 91),
}


def ensure_profile_field(conn: sqlite3.Connection, code: str) -> None:
    row = conn.execute("SELECT 1 FROM profile_fields WHERE code = ?", (code,)).fetchone()
    if row:
        return
    label, kind, order = DEFAULT_PROFILE_LABELS.get(code, (code, "text", 100))
    conn.execute(
        "INSERT INTO profile_fields (code, label, kind, sort_order) VALUES (?, ?, ?, ?)",
        (code, label, kind, order),
    )


def upsert_profile_field(conn: sqlite3.Connection, **fields) -> None:
    columns = ("code", "label", "kind", "source", "active", "sort_order")
    values = {key: fields.get(key) for key in columns}
    values["kind"] = values.get("kind") or "text"
    raw_active = fields.get("active", 1)
    values["active"] = 0 if raw_active in (0, False, "0", "off", "false") else 1
    values["sort_order"] = int(values.get("sort_order") or 100)
    conn.execute(
        f"INSERT INTO profile_fields ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) "
        "ON CONFLICT(code) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in columns if c != "code"),
        values,
    )
    conn.commit()


def list_profile_fields(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    query = "SELECT * FROM profile_fields"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY sort_order, code"
    return conn.execute(query).fetchall()


def put_profile_value(
    conn: sqlite3.Connection,
    partner_id: int,
    field_code: str,
    value: str | None,
    valid_from: str | None = None,
    source: str | None = None,
) -> str | None:
    """값이 실제로 바뀔 때만 이력을 추가하고, 바뀌었으면 '이전 값'을 돌려준다."""
    ensure_profile_field(conn, field_code)
    current = latest_profile_value(conn, partner_id, field_code)
    if current is not None and (current["value"] or "") == (value or ""):
        return None
    conn.execute(
        "INSERT INTO profile_values (partner_id, field_code, value, valid_from, source, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (partner_id, field_code, value, valid_from or date.today().isoformat(), source, now_iso()),
    )
    return (current["value"] if current else None) or ""


def latest_profile_value(conn: sqlite3.Connection, partner_id: int, field_code: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM profile_values WHERE partner_id = ? AND field_code = ? "
        "ORDER BY valid_from DESC, id DESC LIMIT 1",
        (partner_id, field_code),
    ).fetchone()


def profile_snapshot(conn: sqlite3.Connection, partner_id: int) -> dict[str, sqlite3.Row]:
    """항목별 최신값. 확인일이 같으면 나중에 기록된 행(id가 큰 쪽)이 최신이다."""
    rows = conn.execute(
        "SELECT * FROM ("
        "  SELECT v.*, ROW_NUMBER() OVER ("
        "    PARTITION BY field_code ORDER BY valid_from DESC, id DESC"
        "  ) AS rn FROM profile_values v WHERE partner_id = ?"
        ") WHERE rn = 1",
        (partner_id,),
    ).fetchall()
    return {row["field_code"]: row for row in rows}


def profile_history(conn: sqlite3.Connection, partner_id: int, field_code: str | None = None) -> list[sqlite3.Row]:
    if field_code:
        return conn.execute(
            "SELECT p.*, f.label FROM profile_values p JOIN profile_fields f ON f.code = p.field_code "
            "WHERE p.partner_id = ? AND p.field_code = ? ORDER BY p.valid_from DESC, p.id DESC",
            (partner_id, field_code),
        ).fetchall()
    return conn.execute(
        "SELECT p.*, f.label FROM profile_values p JOIN profile_fields f ON f.code = p.field_code "
        "WHERE p.partner_id = ? ORDER BY p.valid_from DESC, p.id DESC LIMIT 50",
        (partner_id,),
    ).fetchall()


# --- 변경 로그(뉴스 티커 / 변경 강조) -----------------------------------------


def add_change(
    conn: sqlite3.Connection,
    partner_id: int,
    kind: str,
    title: str,
    detail: str | None = None,
    severity: str = "info",
    anchor: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO change_log (partner_id, kind, title, detail, severity, anchor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (partner_id, kind, title, detail, severity, anchor, now_iso()),
    )
    return int(cursor.lastrowid)


def list_changes(conn: sqlite3.Connection, limit: int = 40, since_id: int = 0) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT c.*, p.name AS partner_name, p.category_code FROM change_log c "
        "JOIN partners p ON p.id = c.partner_id "
        "WHERE c.id > ? ORDER BY c.id DESC LIMIT ?",
        (since_id, limit),
    ).fetchall()


def search_changes(
    conn: sqlite3.Connection,
    partner_id: int | None = None,
    kind: str | None = None,
    severity: str | None = None,
    since: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """변동이력 화면용 조회. 협력사·종류·심각도·기간으로 좁힌다."""
    clauses, params = ["1 = 1"], []
    if partner_id:
        clauses.append("c.partner_id = ?")
        params.append(partner_id)
    if kind:
        clauses.append("c.kind = ?")
        params.append(kind)
    if severity:
        clauses.append("c.severity = ?")
        params.append(severity)
    if since:
        clauses.append("c.created_at >= ?")
        params.append(since)
    params.extend([limit, offset])
    return conn.execute(
        "SELECT c.*, p.name AS partner_name, p.category_code, p.relation, p.group_name "
        "FROM change_log c JOIN partners p ON p.id = c.partner_id "
        f"WHERE {' AND '.join(clauses)} ORDER BY c.id DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()


def count_changes(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT kind, COUNT(*) AS n FROM change_log GROUP BY kind").fetchall()
    counts = {row["kind"]: row["n"] for row in rows}
    counts["전체"] = sum(counts.values())
    return counts


def max_change_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS n FROM change_log").fetchone()
    return int(row["n"])


def changed_partner_ids(conn: sqlite3.Connection, since_id: int) -> dict[int, sqlite3.Row]:
    """마지막으로 본 시점 이후 바뀐 협력사. 리스트에서 한 번 강조하는 데 쓴다."""
    rows = conn.execute(
        "SELECT partner_id, COUNT(*) AS n, MAX(severity) AS severity, MAX(title) AS title "
        "FROM change_log WHERE id > ? GROUP BY partner_id",
        (since_id,),
    ).fetchall()
    return {int(row["partner_id"]): row for row in rows}


# --- 사업자상태 이력(폐업 이력 확인) ------------------------------------------


def status_timeline(conn: sqlite3.Connection, partner_id: int, metric_code: str = "biz_status") -> list[dict]:
    """상태가 바뀐 구간만 뽑는다. 폐업→재개업 같은 이력도 그대로 보인다."""
    rows = conn.execute(
        "SELECT period, value, text_value FROM metric_values "
        "WHERE partner_id = ? AND metric_code = ? ORDER BY period",
        (partner_id, metric_code),
    ).fetchall()
    timeline: list[dict] = []
    for row in rows:
        label = row["text_value"] or str(row["value"])
        if timeline and timeline[-1]["label"] == label:
            timeline[-1]["until"] = row["period"]
            continue
        timeline.append({"since": row["period"], "until": row["period"], "label": label, "value": row["value"]})
    return timeline
