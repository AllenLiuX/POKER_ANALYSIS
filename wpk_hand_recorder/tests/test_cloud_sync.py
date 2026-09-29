import os

from wpk_recorder.cloud_sync import _sql_statements, schema_paths
from wpk_recorder.models import HandHistory, Player
from wpk_recorder.storage import RecorderStore


def _save(store: RecorderStore) -> None:
    hand = HandHistory(
        hand_id="sync-1",
        started_at="2026-01-01T00:00:00+00:00",
        status="completed",
        big_blind=2,
    )
    hand.players = {
        1: Player(1, user_id="hero", alias="Hero", is_hero=True, stack_start=100),
    }
    store.save_hand(hand)


def test_subscription_migration_keeps_function_bodies():
    path = next(item for item in schema_paths() if item.name == "subscriptions.sql")
    statements = _sql_statements(path.read_text(encoding="utf-8"))
    functions = [
        statement
        for statement in statements
        if "create or replace function" in statement.lower()
    ]
    names = " ".join(statement.lower() for statement in functions)
    assert "admin_dashboard" in names
    assert "admin_set_plan" in names
    assert "effective_plan" in names
    for statement in functions:
        assert statement.count("$$") == 2


def test_set_plan_rejects_unknown_tier_without_connecting(monkeypatch):
    from wpk_recorder.admin_subscriptions import SubscriptionError, set_plan

    def fail_connect():
        raise AssertionError("should not connect")

    monkeypatch.setattr("wpk_recorder.admin_subscriptions._connect", fail_connect)
    try:
        set_plan("00000000-0000-0000-0000-000000000000", "gold", None, None, "actor", "a@b.c")
    except SubscriptionError as exc:
        assert "free" in str(exc)
    else:
        raise AssertionError("expected SubscriptionError")


def test_signed_in_account_owns_new_hands(tmp_path, monkeypatch):
    from wpk_recorder.cloud_sync import owner_email

    monkeypatch.setenv("WPK_OWNER_EMAIL", "default@example.com")
    assert owner_email(tmp_path) == "default@example.com"
    account = tmp_path / "account.json"
    account.write_text('{"email": "Player@Example.com", "user_id": "abc"}', encoding="utf-8")
    assert owner_email(tmp_path) == "player@example.com"


def test_signup_rejects_short_password_before_network(tmp_path, monkeypatch):
    from wpk_recorder.account_auth import AuthError, sign_up

    def fail_request(*_args, **_kwargs):
        raise AssertionError("should not call the network")

    monkeypatch.setattr("wpk_recorder.account_auth._auth_request", fail_request)
    try:
        sign_up("player@example.com", "123", tmp_path)
    except AuthError as exc:
        assert "6" in str(exc)
    else:
        raise AssertionError("expected AuthError")


def test_signup_existing_email_does_not_pretend_mail_was_sent(tmp_path, monkeypatch):
    from wpk_recorder.account_auth import AuthError, sign_up

    def fake_request(*_args, **_kwargs):
        return {"user": {"id": "user-1", "identities": []}, "access_token": None}

    monkeypatch.setattr("wpk_recorder.account_auth._auth_request", fake_request)
    try:
        sign_up("player@example.com", "secret1", tmp_path)
    except AuthError as exc:
        assert "已经注册" in str(exc)
    else:
        raise AssertionError("expected AuthError")


def test_signup_without_session_asks_for_the_confirmation_mail(tmp_path, monkeypatch):
    from wpk_recorder.account_auth import sign_up

    def fake_request(*_args, **_kwargs):
        return {
            "user": {
                "id": "user-1",
                "email": "player@example.com",
                "identities": [{"provider": "email"}],
            }
        }

    monkeypatch.setattr("wpk_recorder.account_auth._auth_request", fake_request)
    result = sign_up("player@example.com", "secret1", tmp_path)
    assert result["needs_confirm"] is True
    assert "确认信" in result["info"]
    assert not (tmp_path / "account.json").exists()


def test_sync_stays_off_without_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("WPK_CLOUD_SYNC", raising=False)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example")
    store = RecorderStore(tmp_path)
    _save(store)
    row = store.connection.execute("SELECT COUNT(*) FROM sync_state").fetchone()
    assert row[0] == 0
    store.close()


def test_sync_marks_hand_dirty_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("WPK_CLOUD_SYNC", "1")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example")
    store = RecorderStore(tmp_path)
    _save(store)
    row = store.connection.execute(
        "SELECT hand_id, dirty FROM sync_state"
    ).fetchone()
    assert row == ("sync-1", 1)
    _save(store)
    count = store.connection.execute("SELECT COUNT(*) FROM sync_state").fetchone()
    assert count[0] == 1
    assert os.environ["WPK_CLOUD_SYNC"] == "1"
    store.close()
