from datetime import date

import pytest

import app as app_module
from partners import geo, models, seed
from partners.collectors import run_collection
from partners.scoring import grade_of, score_metric, score_partner, signal_of


@pytest.fixture
def conn(tmp_path):
    connection = models.connect(tmp_path / "partners.db")
    models.init_db(connection)
    seed.seed_categories(connection)
    seed.seed_profile_fields(connection)
    seed.seed_metric_defs(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded(conn):
    """최근 12개월만 채운 빠른 픽스처(대부분의 테스트에 충분하다)."""
    seed.seed_sample_partners(conn)
    seed.seed_profile_samples(conn)
    run_collection(conn, models.recent_periods(models.current_period(), 12))
    return conn


@pytest.fixture
def full_history(tmp_path):
    """2023-01 이후 전 구간을 채운 픽스처(이력·폐업 이력 확인용)."""
    connection = models.connect(tmp_path / "history.db")
    seed.seed_all(connection)
    yield connection
    connection.close()


def table_rows(response) -> str:
    """리스트 표의 본문만 잘라낸다.

    뉴스 티커는 필터와 무관한 전역 피드라 페이지 전체를 보면 필터링된 협력사
    이름도 나온다. 필터 검증은 표 본문으로 좁혀서 한다.
    """
    html = response.data.decode()
    start = html.find("<tbody>")
    return html[start:html.find("</tbody>", start)] if start >= 0 else ""


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
        code: {
            "value": value, "text_value": None, "period": "2026-09",
            "collected_at": today, "source": "test",
        }
        for code, value in codes.items()
    }


def test_weighted_average_ignores_missing_metrics(conn):
    defs = models.list_metric_defs(conn)
    result = score_partner(defs, _values(debt_ratio=100, revenue_yoy=10))
    assert result.score == pytest.approx(100.0)
    # 두 지표만 채워졌으므로 충족률은 1보다 작다.
    assert 0 < result.coverage < 1
    # 만점이어도 자료가 이만큼 비어 있으면 등급 상한이 걸린다.
    assert result.grade_cap == "C"
    assert result.grade == "C"


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
            "value": 100, "text_value": None, "period": "2025-01",
            "collected_at": "2025-01-05T00:00:00+00:00", "source": "dart",
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
    from partners.collectors import COLLECTORS

    assert {run["source"] for run in runs} == {c.source for c in COLLECTORS}
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
    assert counts["mold"] == sum(
        1 for row in seed.SAMPLE_PARTNERS if row[2] == "mold"
    )
    assert counts["production"] == sum(
        1 for row in seed.SAMPLE_PARTNERS if row[2] == "production"
    )


def test_category_filter_and_badges(client):
    client.post("/partners/seed", data={"months": "3"})
    listing = client.get("/partners/")
    assert "금형".encode() in listing.data

    molds = client.get("/partners/?category=mold")
    assert "우진몰드" in table_rows(molds)
    assert "동성테크" not in table_rows(molds)

    unassigned = client.get("/partners/?category=none")
    assert "우진몰드" not in table_rows(unassigned)


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
    assert "정우게이지" in table_rows(page)
    # 하이픈은 제거되어 저장된다.
    assert "1234567890" in table_rows(page)


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


# --- 소규모(비외감) 협력사 모니터링 -----------------------------------------


def test_small_partner_has_no_dart_data_but_is_still_scored(seeded):
    """DART에 재무가 없어도 연금·신용등급·제출자료로 점수가 나와야 한다."""
    defs = models.list_metric_defs(seeded)
    period = models.known_periods(seeded, limit=1)[-1]
    partner = next(p for p in models.list_partners(seeded) if p["name"] == "성진지그")
    values = models.values_as_of(seeded, partner["id"], period)

    assert "debt_ratio" not in values  # 외부감사 대상이 아니라 공시가 없다
    for code in ("headcount", "avg_pay", "credit_score", "doc_freshness", "biz_status"):
        assert code in values, code

    result = score_partner(defs, values, models.list_risk_events(seeded, partner["id"]))
    assert result.score is not None
    assert "dart" not in result.sources
    assert {"insurance", "credit", "submission", "nts"} <= set(result.sources)


def test_coverage_cap_prevents_false_clean_grade(conn):
    """자료가 거의 없는데 만점이면 등급에 상한이 걸려야 한다."""
    defs = models.list_metric_defs(conn)
    sparse = score_partner(defs, _values(headcount_change_3m=0))
    assert sparse.score == pytest.approx(100.0)
    assert sparse.grade_cap is not None
    assert sparse.grade != "A"
    assert any("상한" in penalty for penalty in sparse.penalties)


def test_avg_pay_is_derived_from_pension_notice():
    from partners.collectors.insurance import _avg_pay

    # 가입자 10명, 당월고지금액 360만원 → 1인당 보수월액 400만원 (요율 9%)
    assert _avg_pay(3_600_000, 10) == 400.0
    assert _avg_pay(None, 10) is None
    assert _avg_pay(3_600_000, 0) is None


def test_pension_withdrawal_is_critical(conn):
    defs = models.list_metric_defs(conn)
    result = score_partner(defs, _values(pension_status=2, headcount_change_3m=0))
    assert result.grade == "E"
    assert any("연금 사업장 탈퇴" in reason for reason in result.critical_reasons)


def test_legal_event_is_critical(conn):
    """부도·회생·경매는 소규모 부실의 결정적 신호라서 즉시위험이다."""
    defs = models.list_metric_defs(conn)
    result = score_partner(defs, _values(legal_count=1, debt_ratio=100))
    assert result.grade == "E"
    assert any("회생" in reason or "부도" in reason for reason in result.critical_reasons)


def test_small_distress_partner_is_caught_without_financials(seeded):
    """공시가 없는 소규모 부실 업체도 잡혀야 한다 — 이 대시보드의 존재 이유."""
    defs = models.list_metric_defs(seeded)
    period = models.known_periods(seeded, limit=1)[-1]
    partner = next(p for p in models.list_partners(seeded) if p["name"] == "명진검사구")
    values = models.values_as_of(seeded, partner["id"], period)
    events = models.list_risk_events(seeded, partner["id"])

    assert "debt_ratio" not in values
    result = score_partner(defs, values, events)
    assert result.grade == "E"
    assert result.signal == "red"


def test_doc_freshness_tracks_submission(conn):
    partner_id = models.upsert_partner(conn, name="성진지그", biz_no="9218609012", profile="small_healthy")
    periods = models.recent_periods(models.current_period(), 6)

    # 제출 기록이 없으면 '제출 없음'으로 최저점이 된다.
    run_collection(conn, periods, ["submission"])
    empty = models.values_as_of(conn, partner_id, periods[-1])["doc_freshness"]
    assert empty["text_value"] == "제출 없음"
    defn = next(d for d in models.list_metric_defs(conn) if d["code"] == "doc_freshness")
    assert score_metric(defn, empty["value"]) == 0.0

    # 이번 달 제출하면 경과 0개월이 되어 만점이다.
    models.add_submission(
        conn, partner_id,
        doc_type="표준재무제표증명",
        submitted_on=f"{models.current_period()}-01",
        period="2025 회계연도",
    )
    run_collection(conn, periods, ["submission"])
    fresh = models.values_as_of(conn, partner_id, periods[-1])["doc_freshness"]
    assert fresh["value"] == 0.0
    assert score_metric(defn, fresh["value"]) == 100.0


def test_credit_grades_load_from_csv(conn, tmp_path, monkeypatch):
    csv_path = tmp_path / "credit.csv"
    csv_path.write_text(
        "biz_no,grade,evaluated_on\n9218609012,BBB,2026-06-30\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PARTNERS_CREDIT_CSV", str(csv_path))

    partner_id = models.upsert_partner(conn, name="성진지그", biz_no="9218609012", profile="small_healthy")
    run_collection(conn, models.recent_periods(models.current_period(), 12), ["credit"])
    value = models.values_as_of(conn, partner_id, models.current_period())["credit_score"]
    assert value["text_value"] == "신용 BBB"
    assert value["value"] == 78.0
    assert value["source"] == "credit"
    run = next(row for row in models.list_runs(conn) if row["source"] == "credit")
    assert run["mode"] == "live"


def test_risk_csv_picks_up_legal_events(conn, tmp_path, monkeypatch):
    csv_path = tmp_path / "risk.csv"
    csv_path.write_text(
        "biz_no,kind,title,occurred_on,severity,source,url\n"
        "9218609012,부도,당좌거래정지,2026-08-14,,은행연합회,\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PARTNERS_RISK_CSV", str(csv_path))

    partner_id = models.upsert_partner(conn, name="성진지그", biz_no="9218609012", profile="small_healthy")
    run_collection(conn, models.recent_periods("2026-09", 12), ["risk_list"])
    events = models.list_risk_events(conn, partner_id)
    assert events[0]["kind"] == "부도"
    assert events[0]["severity"] == "critical"
    assert models.values_as_of(conn, partner_id, "2026-09")["legal_count"]["value"] == 1.0


def test_weak_coverage_filter_lists_small_partners(client):
    client.post("/partners/seed", data={"months": "6"})
    weak = client.get("/partners/?coverage=weak")
    rows = table_rows(weak)
    assert "성진지그" in rows
    assert "명진검사구" in rows
    # 공시가 있는 협력사는 빠진다.
    assert "동성테크" not in rows


def test_detail_can_register_submission_and_manual_value(client):
    client.post("/partners/seed", data={"months": "6"})
    listing = client.get("/partners/api/partners.json").get_json()
    target = next(item for item in listing["partners"] if item["name"] == "명진검사구")
    assert "dart" not in target["sources"]
    assert target["id"]

    partner_id = target["id"]
    response = client.post(
        f"/partners/{partner_id}",
        data={
            "action": "add_submission", "doc_type": "표준재무제표증명",
            "submitted_on": "2026-09-10", "period": "2025 회계연도",
        },
    )
    assert response.status_code == 302
    page = client.get(f"/partners/{partner_id}")
    assert "2026-09-10".encode() in page.data

    response = client.post(
        f"/partners/{partner_id}",
        data={
            "action": "manual_value", "metric_code": "debt_ratio",
            "value_period": "2026-09", "value": "180",
        },
    )
    assert response.status_code == 302
    payload = client.get("/partners/api/partners.json").get_json()
    updated = next(item for item in payload["partners"] if item["name"] == "명진검사구")
    assert updated["metrics"]["debt_ratio"]["value"] == 180.0
    assert "manual" in updated["sources"]


def test_small_company_headcount_series_actually_moves(seeded):
    """소규모 인원 시계열이 반올림에 갇혀 멈추지 않아야 한다."""
    partner = next(p for p in models.list_partners(seeded) if p["name"] == "명진검사구")
    history = [
        row["value"] for row in models.value_history(seeded, partner["id"], "headcount", limit=60)
        if row["value"] is not None
    ]
    assert len(history) >= 12
    assert min(history) < max(history)  # 값이 실제로 변한다


def test_no_future_dated_events(seeded):
    rows = seeded.execute(
        "SELECT COUNT(*) AS n FROM risk_events WHERE occurred_on > date('now')"
    ).fetchone()
    assert rows["n"] == 0


def test_yearly_chart_fallback_chain(seeded):
    """공시 매출 → 공공 수주 → 연금 인원 순으로 대체한다."""
    from partners.views import yearly_chart

    period = models.known_periods(seeded, limit=1)[-1]
    small = next(p for p in models.list_partners(seeded) if p["name"] == "성진지그")
    chart = yearly_chart(seeded, small["id"], period)
    assert chart["code"] == "public_award"  # 공시가 없으면 실측 수주가 먼저다
    assert len(chart["bars"]) == 3
    assert "조달청" in chart["note"]

    # 수주 자료까지 없으면 연금 인원으로 내려간다.
    seeded.execute(
        "DELETE FROM metric_values WHERE partner_id = ? AND metric_code LIKE 'public_award%'",
        (small["id"],),
    )
    seeded.commit()
    assert yearly_chart(seeded, small["id"], period)["code"] == "headcount"

    listed = next(p for p in models.list_partners(seeded) if p["name"] == "동성테크")
    revenue_chart = yearly_chart(seeded, listed["id"], period)
    assert revenue_chart["code"] == "revenue"
    assert revenue_chart["bars"][-1]["value"] is not None


def test_profile_history_is_kept(seeded):
    """대표자 변경·본점 이전이 덮어쓰이지 않고 이력으로 남아야 한다."""
    partner = next(p for p in models.list_partners(seeded) if p["name"] == "대한정밀공업")
    history = models.profile_history(seeded, partner["id"], "ceo_name")
    values = [row["value"] for row in history]

    # 시드로 넣은 과거 대표자가 이력에 남아 있다(수집이 최신값을 덮어써도).
    assert "김성곤" in values
    assert len(values) >= 2
    # 이력은 확인일 역순이고, 스냅샷은 그 첫 줄과 같다.
    assert [row["valid_from"] for row in history] == sorted(
        (row["valid_from"] for row in history), reverse=True
    )
    assert models.profile_snapshot(seeded, partner["id"])["ceo_name"]["value"] == values[0]

    # 주소 이전 이력도 남는다.
    address_history = models.profile_history(seeded, partner["id"] + 2, "address")
    assert len(address_history) >= 1


def test_status_timeline_shows_closure(full_history):
    """폐업 이력이 구간으로 보여야 한다."""
    partner = next(p for p in models.list_partners(full_history) if p["name"] == "태광하이텍")
    timeline = models.status_timeline(full_history, partner["id"])
    labels = [entry["label"] for entry in timeline]
    assert labels[0] == "계속사업자"
    assert "폐업" in labels
    assert timeline[0]["since"] == models.HISTORY_START


def test_history_starts_at_2023(full_history):
    earliest = full_history.execute("SELECT MIN(period) AS p FROM metric_values").fetchone()["p"]
    assert earliest == models.HISTORY_START


def test_change_log_feeds_ticker_and_api(client):
    client.post("/partners/seed", data={})
    payload = client.get("/partners/api/changes.json").get_json()
    assert payload["latest_id"] > 0
    assert payload["changes"]
    first = payload["changes"][0]
    assert first["partner"]
    assert first["url"].startswith("/partners/")

    # 티커 항목은 해당 협력사 상세로 연결된다.
    listing = client.get("/partners/")
    assert 'class="ticker"' in listing.data.decode()
    assert f'href="{first["url"]}"' in listing.data.decode()


def test_changes_are_highlighted_once(client):
    client.post("/partners/seed", data={})
    first = client.get("/partners/")
    assert 'class="row-new"' in table_rows(first)
    # 첫 응답이 '본 시점'을 쿠키에 남기므로 다음 조회에는 강조가 사라진다.
    second = client.get("/partners/")
    assert 'class="row-new"' not in table_rows(second)


def test_collection_logs_only_real_changes(conn):
    partner_id = models.upsert_partner(conn, name="변화사", biz_no="5556667778", profile="healthy")
    periods = models.recent_periods(models.current_period(), 6)
    run_collection(conn, periods, ["nts"])
    baseline = models.max_change_id(conn)

    # 같은 값을 다시 수집하면 변경이 생기지 않는다.
    run_collection(conn, periods, ["nts"])
    assert models.max_change_id(conn) == baseline

    # 상태가 실제로 바뀌면 티커에 올라간다.
    models.put_metric_value(conn, partner_id, "biz_status", periods[-1], 0.0, "계속사업자", "nts")
    conn.commit()
    conn.execute(
        "UPDATE partners SET profile = 'closed' WHERE id = ?", (partner_id,)
    )
    conn.commit()
    run_collection(conn, periods, ["nts"])
    assert models.max_change_id(conn) > baseline
    latest = models.list_changes(conn, limit=1)[0]
    assert latest["partner_name"] == "변화사"


def test_map_places_partners_by_address(seeded):
    from partners.views import build_map, build_rows

    period = models.known_periods(seeded, limit=1)[-1]
    data = build_map(build_rows(seeded, period))
    assert data["markers"]
    assert not data["missing"]  # 목업 주소는 모두 좌표로 변환된다
    # 같은 지역이 겹쳐도 좌표가 분리된다.
    positions = {(m["x"], m["y"]) for m in data["markers"]}
    assert len(positions) == len(data["markers"])
    # 모양은 업종, 색은 신호등을 따른다.
    assert {m["shape"] for m in data["markers"]} - set(
        ["circle", "square", "triangle", "diamond", "hexagon", "pentagon"]
    ) == set()


def test_geo_locate_and_projection():
    assert geo.locate("경기도 안산시 단원구 번영2로 100") == geo.CITIES[("경기", "안산")]
    assert geo.locate("서울특별시 금천구 가산디지털1로") == geo.PROVINCES["서울"]
    assert geo.locate("") is None
    assert geo.locate("없는주소") is None
    x, y = geo.project(37.5, 127.0, 350, 460)
    assert 0 < x < 350 and 0 < y < 460


def test_same_city_name_in_different_provinces():
    """'광주광역시 광산구'와 '경기도 광주시'를 섞지 않아야 한다."""
    gwangju_metro = geo.locate("광주광역시 광산구 하남산단로 168")
    gyeonggi_gwangju = geo.locate("경기도 광주시 초월읍 산이리 12")
    assert gwangju_metro == geo.CITIES[("광주", "광산")]
    assert gyeonggi_gwangju == geo.CITIES[("경기", "광주")]
    assert gwangju_metro != gyeonggi_gwangju
    # 광역시는 도(경기)보다 훨씬 남서쪽에 있다.
    assert gwangju_metro[0] < gyeonggi_gwangju[0]
    assert gwangju_metro[1] < gyeonggi_gwangju[1]


# --- 내부 거래 / 교차검증 / 특허 -------------------------------------------


def test_internal_trade_metrics_are_collected(seeded):
    """공공자료보다 빠른 신호(납기·불량·선급금·의존도)가 채워져야 한다."""
    period = models.known_periods(seeded, limit=1)[-1]
    partner = next(p for p in models.list_partners(seeded) if p["name"] == "대한정밀공업")
    values = models.values_as_of(seeded, partner["id"], period)
    for code in ("otd_rate", "reject_rate", "prepay_requests", "dependency_ratio"):
        assert code in values, code
    # 부실 협력사는 납기가 무너지고 선급금 요청이 나온다.
    assert values["otd_rate"]["value"] < 95
    assert values["prepay_requests"]["value"] >= 1
    # 값은 현실적인 범위 안에 있어야 한다.
    assert 0 <= values["otd_rate"]["value"] <= 100
    assert 0 < values["reject_rate"]["value"] <= 10
    assert 0 <= values["dependency_ratio"]["value"] <= 100
    events = [e["kind"] for e in models.list_risk_events(seeded, partner["id"])]
    assert "선급금 요청" in events


def test_internal_trade_reads_csv(conn, tmp_path, monkeypatch):
    csv_path = tmp_path / "internal.csv"
    csv_path.write_text(
        "biz_no,period,order_amount,otd_rate,reject_rate,prepay_requests,dependency_ratio\n"
        f"5556667778,{models.current_period()},120000000,92.5,3.1,2,64.0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PARTNERS_INTERNAL_CSV", str(csv_path))

    partner_id = models.upsert_partner(conn, name="시험사", biz_no="5556667778", profile="watch")
    run_collection(conn, models.recent_periods(models.current_period(), 6), ["internal"])
    values = models.values_as_of(conn, partner_id, models.current_period())
    assert values["otd_rate"]["value"] == 92.5
    assert values["dependency_ratio"]["value"] == 64.0
    assert values["otd_rate"]["source"] == "internal"
    run = next(row for row in models.list_runs(conn) if row["source"] == "internal")
    assert run["mode"] == "live"


def test_delivery_failure_lowers_score(conn):
    defs = models.list_metric_defs(conn)
    good = score_partner(defs, _values(otd_rate=99, reject_rate=0.4, prepay_requests=0))
    bad = score_partner(defs, _values(otd_rate=84, reject_rate=5.5, prepay_requests=3))
    assert good.score > bad.score
    assert good.category_signal("거래") == "green"
    assert bad.category_signal("거래") == "red"


def test_health_insurance_cross_check(seeded):
    """연금과 건보 가입자 괴리가 지표로 남는다."""
    period = models.known_periods(seeded, limit=1)[-1]
    gaps = {}
    for partner in models.list_partners(seeded):
        values = models.values_as_of(seeded, partner["id"], period)
        if "insured_gap" in values:
            gaps[partner["name"]] = values["insured_gap"]["value"]
    assert gaps
    # 부실 협력사는 건보 인원이 연금보다 많이 빠져 있다(일용·외주 전환).
    assert gaps["대한정밀공업"] < gaps["동성테크"]


def test_patent_count_is_reference_only(conn):
    defn = next(d for d in models.list_metric_defs(conn) if d["code"] == "patent_count")
    assert defn["direction"] == "info"
    assert defn["weight"] == 0
    # 참고용이라 점수에 영향을 주지 않는다.
    with_patents = score_partner(conn and models.list_metric_defs(conn), _values(patent_count=30, otd_rate=99))
    without = score_partner(models.list_metric_defs(conn), _values(otd_rate=99))
    assert with_patents.score == without.score


def test_risk_csv_supports_new_kinds(conn, tmp_path, monkeypatch):
    csv_path = tmp_path / "risk.csv"
    csv_path.write_text(
        "biz_no,kind,title,occurred_on,severity,source,url\n"
        "7778889990,해산,법인 해산등기 확인,2026-08-02,,인터넷등기소,\n"
        "7778889990,환경위반,폐수 기준 초과 과징금,2026-06-11,,환경부,\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PARTNERS_RISK_CSV", str(csv_path))

    partner_id = models.upsert_partner(conn, name="해산사", biz_no="7778889990", profile="watch")
    run_collection(conn, models.recent_periods("2026-09", 12), ["risk_list"])
    values = models.values_as_of(conn, partner_id, "2026-09")
    assert values["legal_count"]["value"] == 1      # 해산
    assert values["sanction_count"]["value"] == 1   # 환경위반
    events = {e["kind"]: e["severity"] for e in models.list_risk_events(conn, partner_id)}
    assert events["해산"] == "critical"
    assert events["환경위반"] == "warn"


def test_credit_api_hook_is_used_when_configured(conn, monkeypatch):
    """구독 API가 설정되면 CSV 대신 그쪽을 쓴다(필드명은 환경변수로 맞춘다)."""
    from partners.collectors import credit

    monkeypatch.setenv("CREDIT_API_URL", "https://example.test/credit")
    monkeypatch.setenv("CREDIT_API_KEY", "dummy")
    monkeypatch.setenv("CREDIT_FIELD_GRADE", "cbGrade")
    monkeypatch.setenv("CREDIT_FIELD_DATE", "evalDt")
    monkeypatch.setattr(
        credit, "get_json",
        lambda url, params=None: {"data": [{"cbGrade": "BB", "evalDt": "2026-05-20"}]},
    )

    partner_id = models.upsert_partner(conn, name="등급사", biz_no="4445556667", profile="watch")
    run_collection(conn, models.recent_periods("2026-09", 12), ["credit"])
    value = models.values_as_of(conn, partner_id, "2026-09")["credit_score"]
    assert value["text_value"] == "신용 BB"
    assert value["value"] == 68.0


def test_optional_doc_types_do_not_affect_freshness(conn):
    """수출실적증명 같은 선택 자료는 '제출 경과' 지표를 대신하지 않는다."""
    partner_id = models.upsert_partner(conn, name="수출사", biz_no="3334445556", profile="small_healthy")
    models.add_submission(
        conn, partner_id, doc_type="수출실적증명",
        submitted_on=f"{models.current_period()}-01", period="2026 상반기",
    )
    run_collection(conn, models.recent_periods(models.current_period(), 6), ["submission"])
    value = models.values_as_of(conn, partner_id, models.current_period())["doc_freshness"]
    assert value["text_value"] == "제출 없음"

    models.add_submission(
        conn, partner_id, doc_type="표준재무제표증명",
        submitted_on=f"{models.current_period()}-01", period="2025 회계연도",
    )
    run_collection(conn, models.recent_periods(models.current_period(), 6), ["submission"])
    value = models.values_as_of(conn, partner_id, models.current_period())["doc_freshness"]
    assert value["value"] == 0.0


# --- 계열사 / 변동이력 화면 / 픽토그램 ---------------------------------------


def test_affiliates_are_monitored_like_others(seeded):
    """계열사도 같은 지표·같은 가중치로 채점되어야 한다."""
    defs = models.list_metric_defs(seeded)
    period = models.known_periods(seeded, limit=1)[-1]
    affiliate = next(p for p in models.list_partners(seeded) if p["relation"] == "affiliate")
    result = score_partner(
        defs,
        models.values_as_of(seeded, affiliate["id"], period),
        models.list_risk_events(seeded, affiliate["id"]),
    )
    assert result.score is not None
    assert affiliate["group_name"]


def test_group_members_and_listing(seeded):
    groups = {row["group_name"]: row["n"] for row in models.list_groups(seeded)}
    assert groups["한빛그룹"] == 2
    partner = next(p for p in models.list_partners(seeded) if p["name"] == "한빛모터스")
    siblings = models.group_members(seeded, "한빛그룹", exclude_id=partner["id"])
    assert [row["name"] for row in siblings] == ["한빛정공"]


def test_relation_filter_on_list(client):
    client.post("/partners/seed", data={"months": "6"})
    affiliates = table_rows(client.get("/partners/?relation=affiliate"))
    assert "한빛모터스" in affiliates
    assert "동성테크" not in affiliates


def test_relation_can_be_changed_in_detail(client):
    client.post("/partners/seed", data={"months": "6"})
    payload = client.get("/partners/api/partners.json").get_json()
    target = next(item for item in payload["partners"] if item["name"] == "동성테크")
    assert target["relation"] == "external"

    client.post(
        f"/partners/{target['id']}",
        data={"action": "update_partner", "name": "동성테크",
              "relation": "affiliate", "group_name": "한빛그룹"},
    )
    updated = next(
        item for item in client.get("/partners/api/partners.json").get_json()["partners"]
        if item["name"] == "동성테크"
    )
    assert updated["relation"] == "affiliate"
    assert updated["group"] == "한빛그룹"


def test_changes_page_filters_and_links(client):
    client.post("/partners/seed", data={})
    page = client.get("/partners/changes")
    assert page.status_code == 200
    body = page.data.decode()
    assert "협력사 변동이력" in body
    assert "/partners/" in body

    # 종류 필터가 목록을 좁힌다.
    events_only = client.get("/partners/changes?kind=사건")
    rows = table_rows(events_only)
    assert rows
    assert "개요" not in rows.split("</tr>")[0]

    # 없는 조건이면 빈 목록을 알려준다.
    empty = client.get("/partners/changes?kind=지표&days=7")
    assert "변동이력이 없습니다" in empty.data.decode() or table_rows(empty)


def test_changes_are_not_duplicated_by_seed(conn, tmp_path):
    """시드가 수집이 남긴 사건을 다시 기록하지 않아야 하고, 두 번 돌려도 멱등해야 한다."""
    connection = models.connect(tmp_path / "dup.db")
    seed.seed_all(connection, months=12)
    seed.seed_all(connection, months=12)   # 두 번 눌러도 티커가 두 줄이 되지 않는다
    rows = connection.execute(
        "SELECT partner_id, kind, title, COALESCE(detail, '') AS detail, COUNT(*) AS n "
        "FROM change_log GROUP BY partner_id, kind, title, detail HAVING n > 1"
    ).fetchall()
    assert rows == []
    connection.close()


def _status_values(status_value, status_text, **codes):
    """수집기처럼 표시 문구까지 담은 값 묶음."""
    values = _values(**codes)
    values["biz_status"] = {
        "value": status_value, "text_value": status_text,
        "period": "2026-09", "collected_at": models.now_iso(), "source": "nts",
    }
    return values


def test_category_headline_prefers_problem_then_representative(conn):
    defs = models.list_metric_defs(conn)
    # 문제가 있으면 가장 나쁜 지표를 보여준다.
    bad = score_partner(defs, _status_values(2, "폐업", plant_area=14000))
    assert bad.category_headline("기업상태") == "폐업"
    # 다 정상이면 참고용 면적이 아니라 대표 지표(사업자상태)를 보여준다.
    good = score_partner(defs, _status_values(0, "계속사업자", plant_area=14000))
    assert good.category_headline("기업상태") == "계속사업자"


def test_category_tip_lists_every_metric(conn):
    defs = models.list_metric_defs(conn)
    result = score_partner(defs, _values(debt_ratio=443, current_ratio=57, credit_score=35))
    tip = result.category_tip("재무")
    assert "재무 · 경고" in tip
    assert "부채비율" in tip and "유동비율" in tip and "신용평가 등급" in tip
    assert "가중 20" in tip  # 가중치도 함께 보여준다


def test_worker_gap_is_measured_against_pension(seeded):
    """공장등록 종업원수와 연금 가입자수의 괴리가 지표로 남는다."""
    period = models.known_periods(seeded, limit=1)[-1]
    found = False
    for partner in models.list_partners(seeded):
        values = models.values_as_of(seeded, partner["id"], period)
        if "worker_gap" in values and "plant_workers" in values:
            found = True
            assert values["worker_gap"]["value"] is not None
    assert found


def test_factory_cancellation_is_critical(conn):
    defs = models.list_metric_defs(conn)
    result = score_partner(defs, _values(factory_status=2, debt_ratio=100))
    assert result.grade == "E"
    assert any("공장등록" in reason for reason in result.critical_reasons)


def test_public_award_fills_sales_gap_for_small_partner(seeded):
    """소규모 협력사는 공공 수주가 매출 자리를 메운다."""
    period = models.known_periods(seeded, limit=1)[-1]
    partner = next(p for p in models.list_partners(seeded) if p["name"] == "명진검사구")
    values = models.values_as_of(seeded, partner["id"], period)
    assert "revenue" not in values
    assert "public_award" in values


def test_detail_has_no_duplicate_anchor_ids(client):
    client.post("/partners/seed", data={"months": "6"})
    body = client.get("/partners/2").data.decode()
    import re

    ids = re.findall(r'id="([^"]+)"', body)
    assert len(ids) == len(set(ids)), [x for x in ids if ids.count(x) > 1]


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
    assert len(payload["partners"]) == len(seed.SAMPLE_PARTNERS)
    assert {p["grade"] for p in payload["partners"]} & {"A", "B", "C", "D", "E"}


def test_filters_narrow_the_list(client):
    client.post("/partners/seed", data={"months": "6"})
    filtered = client.get("/partners/?signal=red")
    assert filtered.status_code == 200
    assert "동성테크" not in table_rows(filtered)

    searched = client.get("/partners/?q=동성")
    assert "동성테크" in table_rows(searched)
    assert "대한정밀공업" not in table_rows(searched)


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
