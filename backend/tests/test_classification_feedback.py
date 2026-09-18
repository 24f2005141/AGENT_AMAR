"""Phase 18 — user classification feedback / manual primary-category correction."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db import session as db_session
from app.main import app
from app.services.persistence_service import PersistenceService
from tests.auth_helpers import auth_headers, issue_token, seed_user
from tests.persistence_helpers import (
    decision_for,
    default_user_pk,
    internship_email,
    promo_email,
)

client = TestClient(app)

FB = "/api/v1/emails/{}/classification-feedback"


def _persist(email, user_pk) -> str:
    with db_session.db_session() as db:
        return PersistenceService(db, user_pk=user_pk).persist_decision(
            email, decision_for(email)
        ).email_id


@pytest.fixture
def action_email():
    """An internship email → auto primary_category ACTION_REQUIRED."""
    with db_session.db_session() as db:
        upk = default_user_pk(db)
    return _persist(internship_email("gmail_fb_action"), upk)


def _row(email_id: str):
    return client.get(f"/api/v1/emails/{email_id}").json()


# --- happy path -----------------------------------------------------

def test_owner_can_correct_classification(action_email):
    before = _row(action_email)
    assert before["primary_category"] == "ACTION_REQUIRED"
    assert before["primary_category_source"] == "auto"

    r = client.post(FB.format(action_email), json={"category": "IMPORTANT"})
    assert r.status_code == 200
    body = r.json()
    assert body["primary_category"] == "IMPORTANT"          # current updated
    assert body["auto_primary_category"] == "ACTION_REQUIRED"  # original preserved
    assert body["primary_category_source"] == "user"


def test_feedback_record_is_created_with_provenance(action_email):
    client.post(FB.format(action_email), json={"category": "IMPORTANT"})
    hist = client.get(FB.format(action_email)).json()
    assert len(hist) == 1
    fb = hist[0]
    assert fb["original_primary_category"] == "ACTION_REQUIRED"
    assert fb["corrected_primary_category"] == "IMPORTANT"
    assert fb["original_final_category"]  # the 15-value triage label snapshot
    assert "classifier_source" in fb and "ml_confidence" in fb and "llm_used" in fb


def test_email_leaves_old_section_and_enters_new(action_email):
    client.post(FB.format(action_email), json={"category": "IMPORTANT"})

    action_ids = {e["email_id"] for e in client.get(
        "/api/v1/emails", params={"primary_category": "ACTION_REQUIRED"}).json()}
    important_ids = {e["email_id"] for e in client.get(
        "/api/v1/emails", params={"primary_category": "IMPORTANT"}).json()}

    assert action_email not in action_ids   # gone from the old section
    assert action_email in important_ids    # only in the new one


def test_latest_correction_wins(action_email):
    client.post(FB.format(action_email), json={"category": "IMPORTANT"})
    client.post(FB.format(action_email), json={"category": "LOW_PRIORITY"})

    assert _row(action_email)["primary_category"] == "LOW_PRIORITY"
    hist = client.get(FB.format(action_email)).json()
    assert len(hist) == 2
    assert hist[0]["corrected_primary_category"] == "LOW_PRIORITY"  # newest first


def test_repeated_identical_submission_is_a_noop(action_email):
    client.post(FB.format(action_email), json={"category": "IMPORTANT"})
    r = client.post(FB.format(action_email), json={"category": "IMPORTANT"})
    assert r.status_code == 200
    assert len(client.get(FB.format(action_email)).json()) == 1  # no duplicate row


def test_correction_survives_reprocessing(action_email):
    client.post(FB.format(action_email), json={"category": "IMPORTANT"})
    # the scheduler / a manual sync re-runs the pipeline on this email
    with db_session.db_session() as db:
        upk = default_user_pk(db)
    _persist(internship_email("gmail_fb_action"), upk)  # same email_id → reprocess

    row = _row(action_email)
    assert row["primary_category"] == "IMPORTANT"            # user correction kept
    assert row["auto_primary_category"] == "ACTION_REQUIRED"  # auto derivation refreshed


# --- validation / security ---------------------------------------

def test_invalid_category_is_422(action_email):
    r = client.post(FB.format(action_email), json={"category": "SUPER_IMPORTANT"})
    assert r.status_code == 422


def test_unknown_email_is_404():
    r = client.post(FB.format("gmail_does_not_exist"), json={"category": "IMPORTANT"})
    assert r.status_code == 404


def test_unauthenticated_is_401(action_email):
    saved = app.dependency_overrides.pop(get_current_user, None)
    try:
        r = client.post(FB.format(action_email), json={"category": "IMPORTANT"})
        assert r.status_code == 401
    finally:
        if saved is not None:
            app.dependency_overrides[get_current_user] = saved


def test_another_user_cannot_correct_your_email(action_email):
    other = seed_user(sub="fb-other")
    tok = issue_token(other)
    saved = app.dependency_overrides.pop(get_current_user, None)
    try:
        r = client.post(FB.format(action_email), json={"category": "IMPORTANT"},
                        headers=auth_headers(tok))
        assert r.status_code == 404
        # and the email is untouched for its real owner
        if saved is not None:
            app.dependency_overrides[get_current_user] = saved
        assert _row(action_email)["primary_category"] == "ACTION_REQUIRED"
        assert client.get(FB.format(action_email)).json() == []
    finally:
        if saved is not None:
            app.dependency_overrides[get_current_user] = saved


def test_feedback_is_scoped_per_user(action_email):
    # owner corrects
    client.post(FB.format(action_email), json={"category": "IMPORTANT"})
    other = seed_user(sub="fb-scope-other")
    tok = issue_token(other)
    saved = app.dependency_overrides.pop(get_current_user, None)
    try:
        # the other user sees neither the email nor its feedback
        assert client.get(f"/api/v1/emails/{action_email}",
                          headers=auth_headers(tok)).status_code == 404
    finally:
        if saved is not None:
            app.dependency_overrides[get_current_user] = saved


def test_every_email_still_has_exactly_one_primary_category(action_email):
    client.post(FB.format(action_email), json={"category": "IMPORTANT"})
    _persist(promo_email("gmail_fb_low"), default_user_pk_now())

    buckets = ("REPLY_REQUIRED", "ACTION_REQUIRED", "IMPORTANT", "LOW_PRIORITY")
    all_ids = {e["email_id"] for e in client.get("/api/v1/emails").json()}
    seen: dict[str, set] = {b: set() for b in buckets}
    for b in buckets:
        for e in client.get("/api/v1/emails", params={"primary_category": b}).json():
            seen[b].add(e["email_id"])
    union = set().union(*seen.values())
    assert union == all_ids
    for a in buckets:
        for c in buckets:
            if a != c:
                assert not (seen[a] & seen[c])


def default_user_pk_now() -> int:
    with db_session.db_session() as db:
        return default_user_pk(db)
