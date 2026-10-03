"""The SQLite file's location comes from AQUASENTINEL_DB_PATH when set (a deploy points it at a
persistent disk, e.g. /var/data/aquasentinel.db, so readings survive restarts and redeploys),
and falls back to the repo-root default otherwise - so local dev and every other test behave
exactly as before.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app import config, db
from app.fhir import store as fhir_store

REPO_ROOT = Path(config.__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "aquasentinel.db"


def test_no_env_value_means_the_repo_root_default():
    assert config.resolve_db_path(None) == DEFAULT_DB_PATH


def test_a_blank_env_value_means_the_default_too():
    assert config.resolve_db_path("") == DEFAULT_DB_PATH
    assert config.resolve_db_path("   ") == DEFAULT_DB_PATH


def test_an_env_value_is_used_as_the_database_path():
    assert config.resolve_db_path("/var/data/aquasentinel.db") == Path("/var/data/aquasentinel.db")


def test_surrounding_spaces_in_the_env_value_are_ignored():
    # A value pasted into a dashboard field easily picks up a stray space or newline.
    assert config.resolve_db_path("  /var/data/aquasentinel.db \n") == Path("/var/data/aquasentinel.db")


def test_readings_and_fhir_store_share_one_database_file_by_default():
    # They live in the same file (see app/fhir/store.py) - they must never drift apart.
    assert db.DB_PATH == fhir_store.DB_PATH


def test_both_modules_pick_up_the_env_variable_at_startup():
    """Run in a fresh interpreter: the path is read at import time, and reloading modules
    inside the test process would disturb every other test."""
    code = (
        "from app import db; from app.fhir import store; "
        "print(db.DB_PATH); print(store.DB_PATH)"
    )
    env = {**os.environ, "AQUASENTINEL_DB_PATH": "/var/data/aquasentinel.db"}

    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=True
    )

    assert result.stdout.split() == ["/var/data/aquasentinel.db", "/var/data/aquasentinel.db"]


def test_init_db_creates_a_missing_folder_for_the_database(tmp_path):
    """A freshly attached disk's mount folder exists, but a custom sub-folder may not; the app
    must not crash on first start because of that."""
    target = tmp_path / "not_yet_created" / "nested" / "aquasentinel.db"

    db.init_db(target)
    fhir_store.init_db(target)

    assert target.exists()
