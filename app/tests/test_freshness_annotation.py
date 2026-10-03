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

    assert "<th>PULL DATE</th>" in page
    assert "<th>COMMENT" not in page
    assert 'id="dataNote"' in page  # the note under the table exists


def test_the_note_names_the_silent_sensors_and_sits_under_the_table_not_in_the_banner(monkeypatch, tmp_path):
    """Gouri, 2026-10-03: the flow/stage sensors at the station keep reporting while its
    WATER-QUALITY probe is silent, so "the gauge is unavailable" was misleading. The note says
    which sensors, when they last reported and when AquaSentinel last pulled, in plain words (no
    "stale"), and lives in its own note below the table - not inside the banner."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "The water-quality sensors at the Penn's Landing gauge" in page
    assert "last pulled on" in page
    assert "Check back in an hour for a more recent status." in page
    # the old wording is gone, and nothing in the banner code adds a note any more
    assert "The gauge is currently unavailable." not in page
    assert "Stale: the gauge" not in page
    assert page.index('id="dataNote"') > page.index('id="tbody"')  # under the table


def test_the_time_column_is_headed_pull_time_because_it_is_when_we_pulled(monkeypatch, tmp_path):
    """Gouri, 2026-10-03: the column shows when AquaSentinel last PULLED, not when the sensors
    measured, and a bare "TIME" was read as the reading's time. The header says what it is."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "<th>PULL TIME</th>" in page
    assert "<th>TIME</th>" not in page


def test_the_date_column_is_headed_pull_date_to_match_pull_time(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "<th>PULL DATE</th>" in page
    assert "<th>DATE</th>" not in page


def test_the_note_is_plain_italic_text_not_a_highlighted_strip(monkeypatch, tmp_path):
    """Gouri, 2026-10-03: show the note under the table as plain italic text - no yellow
    background, border or box."""
    import re

    client = _client(monkeypatch, tmp_path)
    page = client.get("/").text

    rule = re.search(r"\.data-note\{([^}]*)\}", page).group(1)

    assert "font-style:italic" in rule
    assert "background" not in rule
    assert "border" not in rule
    assert "amber" not in rule


def test_the_most_recent_row_is_highlighted_light_blue_not_green(monkeypatch, tmp_path):
    """Gouri, 2026-10-03: the newest table row's highlight changes from green to light blue."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "background:#EAF3FB;" in page  # light blue
    assert "#F3FAF7" not in page  # the old green


def test_the_map_gets_real_height_so_the_legends_do_not_dominate_the_card(monkeypatch, tmp_path):
    """Gouri, 2026-10-03: the two legends took about a third of the map card because the map was
    pinned at its 220px minimum. The map area must be tall (the table may move down)."""
    import re

    client = _client(monkeypatch, tmp_path)
    page = client.get("/").text

    desktop = int(re.search(r"\.mapbox\{position:relative;flex:1;min-height:(\d+)px;\}", page).group(1))
    phone = int(re.search(r"@media \(max-width:860px\)\{.*?\.mapbox\{min-height:(\d+)px;\}", page, re.S).group(1))

    assert 'class="mapbox"' in page
    assert desktop >= 480
    assert phone >= 360


def test_the_map_fills_its_frame_in_every_layout_not_just_when_the_parent_has_a_fixed_height(monkeypatch, tmp_path):
    """On a phone the cards stack and the frame only has a MIN height, so a percentage height on
    the map resolved to 0 and the map vanished. The map is positioned to fill its frame instead."""
    import re

    client = _client(monkeypatch, tmp_path)
    page = client.get("/").text

    rule = re.search(r"\.map #reachMap\{([^}]*)\}", page).group(1)

    assert "position:absolute" in rule
    assert "inset:0" in rule


def test_the_map_framing_does_not_reserve_label_space_that_a_phone_does_not_have(monkeypatch, tmp_path):
    """The framing reserves 170px left + 140px right for the station labels; on a 325px-wide phone
    map that left ~15px for the map itself, so it zoomed out across half the Northeast."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    # Desktop maps are ~530px wide (the card shares its row), phone maps ~325px. The phone padding must
    # apply only below the width where the desktop label room (310px) no longer fits.
    assert "map.getSize().x < 420" in page
    assert "map.getSize().x < 600" not in page
    assert "padding: [30, 30]" in page


def test_the_live_readings_explanation_lives_in_the_intro_card(monkeypatch, tmp_path):
    """Gouri, 2026-10-03: the intro card had a lot of empty space, so the two explanatory paragraphs
    that used to sit under the 'Live readings' title now sit in the intro card, ahead of the map
    card. The page's JavaScript still finds the threshold span by its id."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    intro_start = page.index('<div class="left card">')
    map_card = page.index('<div class="map card">')
    title = page.index(">Live readings<")
    first_paragraph = page.index("Each pull reads live data from the")
    method_paragraph = page.index("How the status is decided:")

    assert intro_start < first_paragraph < map_card
    assert intro_start < method_paragraph < map_card
    assert title > map_card  # the section title stays under the map, with no paragraphs after it
    assert page.index('id="ruleThreshold"') < map_card
    assert 'id="methodology"' in page


def test_the_map_reframes_itself_when_its_frame_changes_size(monkeypatch, tmp_path):
    """The map frame stretches with the intro card, which settles in height after fonts load. Leaflet
    was framed for the early size (544px) while the frame ended at 520px, cutting a station label off
    at the left edge. The map re-measures and re-frames whenever its frame is resized."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "new ResizeObserver" in page
    assert "invalidateSize()" in page


def test_the_map_is_framed_without_animation_so_a_second_framing_is_never_dropped(monkeypatch, tmp_path):
    """Leaflet ignores a new fitBounds while a zoom animation is running. The first framing animated,
    the resize-triggered second one arrived mid-animation and was dropped, so the map stayed at a
    stale zoom and a station label was cut off at the edge. Framing is applied instantly."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "padding: [30, 30], animate: false" in page
    assert "paddingBottomRight: [140, 20], animate: false" in page


def test_the_map_is_cropped_to_run_from_ardmore_to_woodbury(monkeypatch, tmp_path):
    """Gouri, 2026-10-03: the map showed a lot of land north of Ardmore and south of Woodbury that
    is not relevant. The page sizes the map frame so its top edge is Ardmore (plus a small margin)
    and its bottom edge Woodbury, and centres the view between them. The desktop cards then align to
    the top so the shorter map card is not stretched back to the intro card's height."""
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "ARDMORE_LAT" in page and "WOODBURY_LAT" in page
    assert "40.0068" in page and "39.8384" in page
    assert "map.project(" in page  # the frame height comes from the actual zoom, not a fixed number
    assert "@media (min-width:861px){ .hero{align-items:flex-start;} }" in page
