from datetime import date

import pytest

import app as app_module
from partners import models, seed
from partners.collectors import run_collection
from partners.scoring import grade_of, score_metric, score_partner, signal_of


@pytest.fixture
def conn(tmp_path):
    connection = models.connect(tmp_path / "partners.db")
    models.init_db(connection)
    seed.seed_categories(connection)
    seed.seed_metric_defs(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded(conn):
    seed.seed_sample_partners(conn)
    run_collection(conn, models.recent_periods(models.current_period(), 12))
    return conn


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "web.db"
    app_module.app.config.update(TESTING=True, PARTNERS_DB=str(db))
    with app_module.app.test_client() as test_client:
        yield test_client


# --- 기간 계산 ---------------------------------------------------------------


def test_shift_period_crosses_year():
    assert models.shift_period("2026-01", -1) == "2025-12"
    assert models.shift_period("2025-12", 2) == "2026-02"


def test_recent_periods_is_ascending():
    assert models.recent_periods("2026-03", 3) == ["2026-01", "2026-02", "2026-03"]


# --- 채점 -------------------------------------------------------------------


def test_score_metric_interpolates_for_lower_better():
    defn = {"direction": "lower_better", "good_value": 100, "bad_value": 400}
    assert score_metric(defn, 100) == 100.0
    assert score_metric(defn, 400) == 0.0
    assert score_metric(defn, 250) == pytest.approx(50.0)


def test_score_metric_clamps_and_skips_info():
    defn = {"direction": "higher_better", "good_value": 10, "bad_value": -30}
    assert score_metric(defn, 50) == 100.0
    assert score_metric(defn, -90) == 0.0
    assert score_metric({"direction": "info", "good_value": 1, "bad_value": 0}, 1) is None


def test_signal_and_grade_boundaries():
    assert signal_of(70) == "green"
    assert signal_of(69.9) == "amber"
    assert signal_of(39.9) == "red"
    assert grade_of(85) == "A"
    assert grade_of(39) == "E"


def _values(**codes):
    today = models.now_iso()
    return {
        code: {"value": value, "text_value": None, "period": "2026-09", "collected_at": today}
        for code, value in codes.items()
    }


def test_weighted_average_ignores_missing_metrics(conn):
    defs = models.list_metric_defs(conn)
    result = score_partner(defs, _values(debt_ratio=100, revenue_yoy=10))
    assert result.score == pytest.approx(100.0)
    assert result.grade == "A"
    # 두 지표만 채워졌으므로 충족률은 1보다 작다.
    assert 0 < result.coverage < 1


def test_closed_business_forces_grade_e(conn):
    defs = models.list_metric_defs(conn)
    result = score_partner(defs, _values(biz_status=2, debt_ratio=100, revenue_yoy=10))
    assert result.grade == "E"
    assert result.signal == "red"
    assert any("폐업" in reason for reason in result.critical_reasons)


def test_wage_arrears_and_headcount_drop_are_critical(conn):
    defs = models.list_metric_defs(conn)
    arrears = score_partner(defs, _values(wage_arrears_count=1, debt_ratio=100))
    assert arrears.grade == "E"
    drop = score_partner(defs, _values(headcount_change_3m=-35, debt_ratio=100))
    assert drop.grade == "E"


def test_recent_events_deduct_points(conn):
    defs = models.list_metric_defs(conn)
    events = [{"occurred_on": date.today().isoformat(), "severity": "warn", "kind": "산재", "title": "x"}]
    clean = score_partner(defs, _values(debt_ratio=100, revenue_yoy=10))
    penalized = score_partner(defs, _values(debt_ratio=100, revenue_yoy=10), events)
    assert penalized.score < clean.score
    assert penalized.penalties


def test_stale_data_is_flagged(conn):
    defs = models.list_metric_defs(conn)
    values = {
        "debt_ratio": {
            "value": 100, "text_value": None, "period": "2025-01", "collected_at": "2025-01-05T00:00:00+00:00",
        }
    }
    result = score_partner(defs, values, today=date(2026, 9, 17))
    assert result.stale
    assert any("노후" in penalty for penalty in result.penalties)


# --- 수집 -------------------------------------------------------------------


def test_mock_collection_fills_all_sources(seeded):
    periods = models.known_periods(seeded, limit=12)
    assert len(periods) == 12
    partner = models.list_partners(seeded)[0]
    values = models.values_as_of(seeded, partner["id"], periods[-1])
    for code in ("debt_ratio", "revenue", "headcount", "biz_status"):
        assert code in values
    runs = models.list_runs(seeded)
    assert {run["source"] for run in runs} == {"dart", "insurance", "nts", "risk_list"}
    assert all(run["status"] == "ok" for run in runs)


def test_collection_is_idempotent(seeded):
    periods = models.recent_periods(models.current_period(), 12)
    before = seeded.execute("SELECT COUNT(*) AS n FROM metric_values").fetchone()["n"]
    run_collection(seeded, periods)
    after = seeded.execute("SELECT COUNT(*) AS n FROM metric_values").fetchone()["n"]
    assert before == after


def test_distress_profile_scores_worse_than_healthy(seeded):
    defs = models.list_metric_defs(seeded)
    period = models.known_periods(seeded, limit=1)[-1]
    scores = {}
    for partner in models.list_partners(seeded):
        events = models.list_risk_events(seeded, partner["id"])
        values = models.values_as_of(seeded, partner["id"], period)
        scores[partner["profile"]] = scores.get(partner["profile"]) or score_partner(defs, values, events)
    assert scores["healthy"].signal == "green"
    assert scores["distress"].grade == "E"
    assert scores["closed"].grade == "E"


def test_added_metric_appears_without_schema_change(conn):
    models.upsert_metric_def(
        conn, code="esg_score", category="기타", label="ESG 평가점수", unit="점",
        direction="higher_better", good_value=90, bad_value=40, weight=10, source="manual",
    )
    partner_id = models.upsert_partner(conn, name="테스트사", biz_no="9999999999")
    models.put_metric_value(conn, partner_id, "esg_score", "2026-09", 90.0)
    conn.commit()
    defs = models.list_metric_defs(conn)
    result = score_partner(defs, models.values_as_of(conn, partner_id, "2026-09"))
    assert any(metric.code == "esg_score" and metric.score == 100.0 for metric in result.metrics)


# --- 업종 카테고리 ----------------------------------------------------------


def test_migration_adds_category_column_to_old_db(tmp_path):
    """category_code 컬럼이 없던 기존 DB도 그대로 열려야 한다."""
    connection = models.connect(tmp_path / "old.db")
    connection.executescript(
        "CREATE TABLE partners (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
        "biz_no TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)"
    )
    connection.commit()
    models.init_db(connection)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(partners)")}
    assert "category_code" in columns
    connection.close()


def test_category_can_be_added_and_deactivated(conn):
    models.upsert_category(conn, "gauge", "게이지", 50)
    assert "게이지" in [row["label"] for row in models.list_categories(conn)]

    models.upsert_category(conn, "gauge", "게이지", 50, active=False)
    assert "게이지" not in [row["label"] for row in models.list_categories(conn)]
    # 비활성 분류도 이름은 남아서 이미 지정된 협력사 배지를 계속 표시할 수 있다.
    assert models.category_labels(conn)["gauge"] == "게이지"


def test_partner_category_can_be_changed(conn):
    partner_id = models.upsert_partner(conn, name="신규사", biz_no="1112223334", category_code="mold")
    assert models.get_partner(conn, partner_id)["category_code"] == "mold"
    models.update_partner_fields(conn, partner_id, category_code="jig", manager="김철수")
    updated = models.get_partner(conn, partner_id)
    assert updated["category_code"] == "jig"
    assert updated["manager"] == "김철수"


def test_partner_counts_by_category(seeded):
    counts = models.count_partners_by_category(seeded)
    assert counts["mold"] == 2
    assert counts["production"] == 4


def test_category_filter_and_badges(client):
    client.post("/partners/seed", data={"months": "3"})
    listing = client.get("/partners/")
    assert "금형".encode() in listing.data

    molds = client.get("/partners/?category=mold")
    assert "우진몰드".encode() in molds.data
    assert "동성테크".encode() not in molds.data

    unassigned = client.get("/partners/?category=none")
    assert "우진몰드".encode() not in unassigned.data


def test_settings_manages_categories_and_partners(client):
    client.post("/partners/seed", data={"months": "3"})
    assert client.post(
        "/partners/settings",
        data={"action": "add_category", "code": "gauge", "label": "게이지", "sort_order": "50"},
    ).status_code == 302

    assert client.post(
        "/partners/settings",
        data={
            "action": "add_partner", "name": "정우게이지", "biz_no": "123-45-67890",
            "category_code": "gauge", "manager": "김철수",
        },
    ).status_code == 302

    page = client.get("/partners/?category=gauge")
    assert "정우게이지".encode() in page.data
    # 하이픈은 제거되어 저장된다.
    assert "1234567890".encode() in page.data


def test_detail_edit_changes_category(client):
    client.post("/partners/seed", data={"months": "3"})
    response = client.post(
        "/partners/1",
        data={"name": "대한정밀공업", "category_code": "jig", "manager": "이영희", "tier": "1차"},
    )
    assert response.status_code == 302
    payload = client.get("/partners/api/partners.json").get_json()
    changed = next(item for item in payload["partners"] if item["name"] == "대한정밀공업")
    assert changed["category_code"] == "jig"
    assert changed["category"] == "지그"


# --- 화면 -------------------------------------------------------------------


def test_dashboard_pages_render(client):
    assert client.post("/partners/seed", data={"months": "6"}).status_code == 302
    listing = client.get("/partners/")
    assert listing.status_code == 200
    assert "협력사".encode() in listing.data
    assert "대한정밀공업".encode() in listing.data

    assert client.get("/partners/settings").status_code == 200
    detail = client.get("/partners/1")
    assert detail.status_code == 200

    payload = client.get("/partners/api/partners.json").get_json()
    assert len(payload["partners"]) == 8
    assert {p["grade"] for p in payload["partners"]} & {"A", "B", "C", "D", "E"}


def test_filters_narrow_the_list(client):
    client.post("/partners/seed", data={"months": "6"})
    filtered = client.get("/partners/?signal=red")
    assert filtered.status_code == 200
    assert "동성테크".encode() not in filtered.data

    searched = client.get("/partners/?q=동성")
    assert "동성테크".encode() in searched.data
    assert "대한정밀공업".encode() not in searched.data


def test_settings_can_add_metric(client):
    client.post("/partners/seed", data={"months": "3"})
    response = client.post(
        "/partners/settings",
        data={
            "action": "add", "code": "esg_score", "label": "ESG 평가점수", "category": "기타",
            "direction": "higher_better", "good_value": "90", "bad_value": "40", "weight": "10",
        },
    )
    assert response.status_code == 302
    page = client.get("/partners/settings")
    assert "ESG 평가점수".encode() in page.data


def test_collect_endpoint_runs_selected_source(client):
    client.post("/partners/seed", data={"months": "3"})
    response = client.post(
        "/partners/collect",
        data={"months": "3", "sources": "nts", "next": "/partners/"},
    )
    assert response.status_code == 302
    page = client.get("/partners/settings")
    assert "nts".encode() in page.data


def test_converter_page_still_works(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "HWP".encode() in response.data
