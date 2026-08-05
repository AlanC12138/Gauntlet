"""The auth gate.

Two properties matter and they pull in opposite directions: starting a run must
require sign-in because it spends real money, and everything else must stay open
because a sign-up wall in front of the demo defeats the point of having one.
"""

import pytest
from fastapi.testclient import TestClient

from gauntlet.api import app
from gauntlet.config import settings


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "clerk_secret_key", "sk_test_not_a_real_key")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_off(monkeypatch):
    monkeypatch.setattr(settings, "clerk_secret_key", None)
    return TestClient(app, raise_server_exceptions=False)


def test_auth_config_reports_enabled(auth_on):
    """The frontend asks rather than assuming, so a deployment with no key
    configured renders no sign-in button instead of one that cannot work."""
    assert auth_on.get("/api/auth").json() == {"enabled": True}


def test_an_empty_secret_counts_as_disabled(monkeypatch):
    """`CLERK_SECRET_KEY=` with no value is "unset", not "enabled with a blank
    key" — the latter 401s every request with no visible cause."""
    monkeypatch.setattr(settings, "clerk_secret_key", "")
    assert settings.auth_enabled is False


def test_auth_config_reports_disabled(auth_off):
    # Separate test, not a second assertion above: both fixtures patch the same
    # setting, so requesting them together would leave only the last one applied.
    assert auth_off.get("/api/auth").json() == {"enabled": False}


def test_starting_a_run_without_a_token_is_401(auth_on):
    response = auth_on.post("/api/runs", json={"mandate_name": "balanced"})

    assert response.status_code == 401
    # A challenge header, so a client can tell "sign in" from "you cannot".
    assert response.headers["www-authenticate"] == "Bearer"


def test_a_garbage_token_is_401_not_500(auth_on):
    response = auth_on.post(
        "/api/runs",
        json={"mandate_name": "balanced"},
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert response.status_code == 401


def test_the_rejection_does_not_leak_why(auth_on):
    """The reason distinguishes expired from malformed from wrong-audience.
    Useful in a log, an oracle for anyone probing the endpoint."""
    body = auth_on.post(
        "/api/runs",
        json={"mandate_name": "balanced"},
        headers={"Authorization": "Bearer not-a-real-jwt"},
    ).json()

    assert body["detail"] == "Sign in to start a run."
    assert "reason" not in body


@pytest.mark.parametrize("path", ["/health", "/api/universe", "/api/mandates"])
def test_read_routes_stay_public_with_auth_on(auth_on, path):
    """The demo has to be visible without an account, or it is not a demo."""
    assert auth_on.get(path).status_code == 200


def test_feasibility_stays_public_with_auth_on(auth_on):
    """Pure arithmetic, no model, no backtest — it costs nothing to serve and
    the mandate builder calls it on every keystroke."""
    constraints = {
        "max_position_weight": 0.1,
        "max_sector_weight": 0.25,
        "min_holdings": 12,
        "max_holdings": 20,
        # A dict keyed by bucket, not a list — the key is the bucket name.
        "asset_class_bands": {"fixed_income": {"min_weight": 0.2, "max_weight": 0.5}},
    }
    assert auth_on.post("/api/constraints/feasibility", json=constraints).status_code == 200


def test_disabling_auth_makes_runs_public(auth_off, clean_db, monkeypatch):
    """The mode local development and the public demo run in. It must be a real
    path, not a theoretical one — so it is tested like any other."""
    import uuid

    from gauntlet.db import get_session
    from gauntlet.db import runs as repo

    def fake_enqueue(mandate):
        run_id = uuid.uuid4().hex
        with get_session() as session:
            repo.create_run(session, run_id, mandate)
        return run_id

    monkeypatch.setattr("gauntlet.jobs.enqueue_run", fake_enqueue)

    response = auth_off.post("/api/runs", json={"mandate_name": "balanced"})

    assert response.status_code == 202
