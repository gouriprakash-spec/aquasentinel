"""Per-row staleness for GET /api/readings (what the dashboard banner's stale note is built from).

The server decides what is stale, using config.FRESHNESS_LIMIT_HOURS - the page never hard-codes
the 2-hour limit. The newest row is judged against NOW, which is exactly the rule /api/status
already applies (so the banner note and the status can never disagree). Older rows are judged
against when they were CHECKED; otherwise every historical row would read "stale" just because
time has passed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import config, db, server, status
from app.fhir import store as fhir_store
from app.tests.test_server import _fake_reading

NOW = datetime(2026, 10, 3, 14, 0, 0, tzinfo=timezone.utc)


def _row(reading_time: datetime, checked: datetime | None = None, row_id: int = 1) -> dict:
    checked = checked or reading_time + timedelta(minutes=20)
    return {
        "id": row_id,
        "reading_time": reading_time.isoformat(),
        "retrieved_at": checked.isoformat(),
        "risk_tier": "Safe",
    }


def test_a_fresh_newest_row_is_not_stale():
    rows = status.annotate_freshness([_row(NOW - timedelta(minutes=30))], now=NOW)

    assert rows[0]["stale"] is False
    assert rows[0]["gauge_age_hours"] == 0.5


def test_a_newest_row_older_than_the_limit_is_stale_and_reports_its_age():
    rows = status.annotate_freshness([_row(NOW - timedelta(hours=6, minutes=30))], now=NOW)

    assert rows[0]["stale"] is True
    assert rows[0]["gauge_age_hours"] == 6.5


def test_exactly_at_the_limit_is_not_stale_matching_current_status():
    # current_status uses a strict ">" - pin the same boundary here.
    rows = status.annotate_freshness(
        [_row(NOW - timedelta(hours=config.FRESHNESS_LIMIT_HOURS))], now=NOW
    )

    assert rows[0]["stale"] is False


def test_the_newest_row_is_judged_against_now_even_if_it_was_fresh_when_checked():
    # Checked 20 minutes after the gauge measured, but 3 hours have passed with no newer pull:
    # the current status is stale, so the newest row must say so.
    reading_time = NOW - timedelta(hours=3)
    rows = status.annotate_freshness([_row(reading_time, checked=reading_time + timedelta(minutes=20))], now=NOW)

    assert rows[0]["stale"] is True
    assert rows[0]["gauge_age_hours"] == 3.0


def test_an_older_row_is_judged_at_the_time_it_was_checked_not_against_now():
    newest = _row(NOW - timedelta(minutes=10), row_id=2)
    old_reading = NOW - timedelta(hours=20)
    older = _row(old_reading, checked=old_reading + timedelta(minutes=20), row_id=1)

    rows = status.annotate_freshness([newest, older], now=NOW)

    assert rows[1]["stale"] is False  # fresh when it was checked
    assert rows[1]["gauge_age_hours"] == 0.3


def test_an_older_row_that_was_already_stale_when_checked_says_so():
    newest = _row(NOW - timedelta(minutes=10), row_id=2)
    old_reading = NOW - timedelta(hours=20)
    older = _row(old_reading, checked=old_reading + timedelta(hours=5), row_id=1)

    rows = status.annotate_freshness([newest, older], now=NOW)

    assert rows[1]["stale"] is True
    assert rows[1]["gauge_age_hours"] == 5.0


def test_a_timestamp_that_cannot_be_read_fails_closed_as_stale():
    bad = {"id": 1, "reading_time": "not a time", "retrieved_at": NOW.isoformat()}

    rows = status.annotate_freshness([bad], now=NOW)

    assert rows[0]["stale"] is True
    assert rows[0]["gauge_age_hours"] is None


def test_naive_timestamps_are_treated_as_utc_like_the_status_rule():
    naive = _row(NOW - timedelta(hours=4)).copy()
    naive["reading_time"] = (NOW - timedelta(hours=4)).replace(tzinfo=None).isoformat()

    rows = status.annotate_freshness([naive], now=NOW)

    assert rows[0]["gauge_age_hours"] == 4.0


def test_the_input_rows_are_not_modified():
    original = _row(NOW - timedelta(hours=1))

    status.annotate_freshness([original], now=NOW)

    assert "stale" not in original


def test_an_empty_list_stays_empty():
    assert status.annotate_freshness([], now=NOW) == []


def test_the_newest_rows_stale_flag_agrees_with_current_status():
    stale_row = {
        "reading_time": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }

    annotated = status.annotate_freshness([stale_row])[0]
    published = status.current_status({**stale_row, **_status_row_fields()})

    assert annotated["stale"] is True
    assert published["status"] == "unavailable"


def _status_row_fields() -> dict:
    """The extra columns build_status_contract would read if the row were fresh (unused when stale)."""
    return {}


# --- the endpoint ---

def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    fhir_store.init_db()
    return TestClient(server.app)


def test_readings_endpoint_carries_the_stale_fields(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    old = _fake_reading("Safe")  # the shared fixture's gauge time is long past the limit
    db.save_reading(old)

    body = client.get("/api/readings").json()

    assert body[0]["stale"] is True
    assert isinstance(body[0]["gauge_age_hours"], float)


def test_readings_endpoint_marks_a_fresh_newest_row_as_not_stale(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    fresh = _fake_reading("Safe")
    fresh["time"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    db.save_reading(fresh)

    body = client.get("/api/readings").json()

    assert body[0]["stale"] is False


# --- the page ---

def test_the_dashboard_has_a_date_column_and_no_comment_column(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "<th>POLL DATE</th>" in page
    assert "<th>COMMENT" not in page
    assert 'id="dataNote"' in page  # the note under the table exists


def test_the_note_names_the_silent_sensors_and_sits_under_the_table_not_in_the_banner(monkeypatch, tmp_path):
    """Gouri, 2026-10-03: the flow/stage sensors at the station keep reporting while its
    WATER-QUALITY probe is silent, so "the gauge is unavailable" was misleading. The note says
    which sensors, when they last reported and when AquaSentinel last checked, in plain words (no
    "stale"), and lives in its own note below the table - not inside the banner."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "The water-quality sensors at the Penn's Landing gauge" in page
    assert "last checked on" in page
    assert "Check back in an hour for a more recent status." in page
    # the old wording is gone, and nothing in the banner code adds a note any more
    assert "The gauge is currently unavailable." not in page
    assert "Stale: the gauge" not in page
    assert page.index('id="dataNote"') > page.index('id="tbody"')  # under the table


def test_the_time_column_is_headed_poll_time_because_it_is_when_we_checked(monkeypatch, tmp_path):
    """Gouri, 2026-10-03: the column shows when AquaSentinel last POLLED, not when the sensors
    measured, and a bare "TIME" was read as the reading's time. The header says what it is."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "<th>POLL TIME</th>" in page
    assert "<th>TIME</th>" not in page


def test_the_date_column_is_headed_poll_date_to_match_poll_time(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "<th>POLL DATE</th>" in page
    assert "<th>DATE</th>" not in page
