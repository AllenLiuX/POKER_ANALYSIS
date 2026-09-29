"""Async dual-write from the local SQLite recorder database to Supabase.

Local SQLite stays the source of truth for reads and for the live recorder.
When WPK_CLOUD_SYNC=1, each saved hand is marked dirty and a background
worker upserts that hand to Postgres. The recorder thread only does a local
insert, so the Singapore round trip never blocks capture.

frames and raw_events are not synced: they are the bulk of the local file
and are only needed by the machine that is attached to the table.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

CHILD_TABLES: Dict[str, Tuple[str, ...]] = {
    "hand_players": (
        "hand_id", "seat", "user_id", "alias", "position", "stack_start",
        "stack_end", "hole_cards_json", "is_hero", "net", "insurance_result",
        "fund",
    ),
    "actions": (
        "hand_id", "sequence", "event_sequence", "action_id", "street", "seat",
        "user_id", "alias", "action", "amount", "amount_to", "stack_after",
        "source_message",
    ),
    "decision_snapshots": (
        "hand_id", "action_sequence", "event_sequence", "user_id", "alias",
        "seat", "street", "position", "game_mode", "chosen_action", "pot_before",
        "stack_before", "effective_stack", "effective_stack_bb", "spr", "to_call",
        "facing_action", "facing_amount", "bet_fraction", "raise_multiple",
        "is_ip", "is_preflop_aggressor", "players_in_hand", "board_texture_json",
    ),
    "opportunities": (
        "hand_id", "action_sequence", "user_id", "metric", "success",
        "position", "game_mode", "effective_stack_bb",
    ),
    "board_cards": ("hand_id", "card_index", "street", "card"),
    "results": (
        "hand_id", "seat", "user_id", "alias", "net", "stack_end",
        "insurance_result", "fund", "shown_cards_json",
    ),
    "hand_squid_players": (
        "hand_id", "user_id", "alias", "squid_start", "squid_end",
    ),
    "squid_awards": (
        "hand_id", "user_id", "alias", "award_count", "round_id", "event_sequence",
    ),
    "showdown_observations": (
        "hand_id", "user_id", "alias", "street", "hole_cards_json", "board_json",
        "preflop_class", "strength_category", "draws_json", "equity_vs_random",
        "position", "effective_stack_bb", "squid_count", "cumulative_net_before",
        "profit_state", "action_line", "feature_tokens_json",
    ),
    "decision_states": (
        "decision_id", "state_hash", "sequence", "hand_id", "captured_at",
        "source", "quality_status", "payload_json",
    ),
    "strategy_evaluations": (
        "decision_id", "state_hash", "sequence", "hand_id", "created_at",
        "engine_version", "status", "recommended_action", "raise_to",
        "confidence", "payload_json",
    ),
    "inference_contexts": (
        "context_hash", "decision_id", "state_hash", "sequence", "hand_id",
        "subject", "context_version", "temporal_quality", "created_at",
        "payload_json",
    ),
    "inference_runs": (
        "run_id", "decision_id", "state_hash", "sequence", "hand_id",
        "context_hash", "template_id", "template_hash", "prompt_hash", "model",
        "reasoning_depth", "analysis_mode", "status", "validation_status",
        "latency_ms", "error_reason", "created_at", "payload_json",
    ),
}

HAND_COLUMNS = (
    "hand_id", "table_id", "started_at", "ended_at", "status", "game_mode",
    "hand_number", "played_at", "button_seat", "small_blind", "big_blind",
    "ante", "pot", "board_json", "quality_status", "quality_reasons_json",
    "excluded_from_stats", "hand_json", "owner_email",
)

DEFAULT_OWNER_EMAIL = "allenliux01@gmail.com"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def schema_paths() -> List[Path]:
    migration_dir = repo_root() / "supabase" / "migrations"
    return [
        migration_dir / "0007_wpk_recorder.sql",
        migration_dir / "0008_wpk_owners.sql",
        Path(__file__).resolve().parent / "sql" / "subscriptions.sql",
    ]


def owner_email(data_dir: Optional[Path] = None) -> str:
    signed_in = _signed_in_email(data_dir)
    if signed_in:
        return signed_in
    value = os.environ.get("WPK_OWNER_EMAIL", DEFAULT_OWNER_EMAIL).strip().lower()
    return value or DEFAULT_OWNER_EMAIL


def _signed_in_email(data_dir: Optional[Path]) -> str:
    if data_dir is None:
        return ""
    path = Path(data_dir) / "account.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    email = str(payload.get("email") or "").strip().lower()
    if "@" not in email:
        return ""
    return email


def apply_default_cloud_sync() -> None:
    """Load the repo env files and turn dual-write on when a URL is configured.

    Called from the CLI only. Tests and library imports stay off unless
    WPK_CLOUD_SYNC is set explicitly, so a developer shell that can see
    backend/.env does not push fixture databases to Supabase.
    """

    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None
    if load_dotenv is not None:
        root = repo_root()
        load_dotenv(root / "backend" / ".env", override=False)
        load_dotenv(root / "wpk_hand_recorder" / ".env", override=False)
    if "WPK_CLOUD_SYNC" not in os.environ and os.environ.get("SUPABASE_DB_URL", "").strip():
        os.environ["WPK_CLOUD_SYNC"] = "1"


def cloud_sync_enabled() -> bool:
    flag = os.environ.get("WPK_CLOUD_SYNC", "").strip().lower()
    if flag not in {"1", "true", "on", "yes"}:
        return False
    return bool(os.environ.get("SUPABASE_DB_URL", "").strip())


def read_backend() -> str:
    value = os.environ.get("WPK_READ_BACKEND", "local").strip().lower()
    return value or "local"


def _db_url() -> str:
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url or "YOUR-PASSWORD" in url:
        raise RuntimeError("SUPABASE_DB_URL is not set to a real Postgres URL")
    return url


def connect_postgres():
    import psycopg

    connection = psycopg.connect(
        _db_url(),
        connect_timeout=20,
        application_name="wpk-recorder",
        autocommit=False,
    )
    connection.execute("SET statement_timeout = '120s'")
    ensure_schema(connection)
    connection.commit()
    return connection


def ensure_schema(connection) -> None:
    paths = schema_paths()
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise RuntimeError(f"missing schema file: {missing[0]}")
    with connection.cursor() as cursor:
        for path in paths:
            for statement in _sql_statements(path.read_text(encoding="utf-8")):
                cursor.execute(statement)


def _sql_statements(script: str) -> List[str]:
    """Split a SQL script on semicolons, keeping dollar-quoted function bodies intact."""

    statements: List[str] = []
    current: List[str] = []
    in_dollar = False
    index = 0
    while index < len(script):
        if script.startswith("$$", index):
            in_dollar = not in_dollar
            current.append("$$")
            index += 2
            continue
        if script[index] == ";" and not in_dollar:
            statement = "".join(current).strip()
            if _sql_is_meaningful(statement):
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(script[index])
        index += 1
    tail = "".join(current).strip()
    if _sql_is_meaningful(tail):
        statements.append(tail)
    return statements


def _sql_is_meaningful(statement: str) -> bool:
    return any(
        line.strip() and not line.strip().startswith("--")
        for line in statement.splitlines()
    )


def open_local(data_dir: Path) -> sqlite3.Connection:
    path = Path(data_dir) / "hands.sqlite3"
    if not path.exists():
        raise RuntimeError(f"local database not found: {path}")
    connection = sqlite3.connect(path, timeout=5)
    connection.execute("PRAGMA busy_timeout=5000")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_state (
            hand_id TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            dirty INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(hands)").fetchall()
    }
    if "owner_email" not in columns:
        connection.execute(
            "ALTER TABLE hands ADD COLUMN owner_email TEXT NOT NULL DEFAULT 'allenliux01@gmail.com'"
        )
    connection.commit()
    return connection


def _fetch(connection: sqlite3.Connection, table: str, columns: Sequence[str], hand_id: str) -> List[tuple]:
    sql = "SELECT {cols} FROM {table} WHERE hand_id = ?".format(
        cols=", ".join(columns),
        table=table,
    )
    try:
        return [tuple(row) for row in connection.execute(sql, (hand_id,))]
    except sqlite3.OperationalError:
        return []


def sync_hand(pg, local: sqlite3.Connection, hand_id: str) -> bool:
    """Replace one hand's cloud rows with the current local rows. One network flush."""

    hand = local.execute(
        "SELECT {cols} FROM hands WHERE hand_id = ?".format(cols=", ".join(HAND_COLUMNS)),
        (hand_id,),
    ).fetchone()
    if hand is None:
        return False
    children = {
        table: _fetch(local, table, columns, hand_id)
        for table, columns in CHILD_TABLES.items()
    }
    players = [
        tuple(row)
        for row in local.execute(
            """
            SELECT user_id, latest_alias, first_seen, last_seen
            FROM players
            WHERE user_id IN (
                SELECT user_id FROM hand_players
                WHERE hand_id = ? AND user_id IS NOT NULL
            )
            """,
            (hand_id,),
        )
    ]
    with pg.cursor() as cursor:
        with pg.pipeline():
            _upsert_hand(cursor, tuple(hand))
            if players:
                cursor.executemany(_PLAYER_UPSERT, players)
            for table, columns in CHILD_TABLES.items():
                cursor.execute(f"DELETE FROM wpk.{table} WHERE hand_id = %s", (hand_id,))
                rows = children[table]
                if rows:
                    cursor.executemany(_insert_sql(table, columns), rows)
    pg.commit()
    return True


def _upsert_hand(cursor, row: tuple) -> None:
    assignments = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in HAND_COLUMNS if column != "hand_id"
    )
    placeholders = ", ".join(["%s"] * len(HAND_COLUMNS))
    cursor.execute(
        f"""
        INSERT INTO wpk.hands ({", ".join(HAND_COLUMNS)})
        VALUES ({placeholders})
        ON CONFLICT (hand_id) DO UPDATE SET {assignments}
        """,
        row,
    )


_PLAYER_UPSERT = """
INSERT INTO wpk.players (user_id, latest_alias, first_seen, last_seen)
VALUES (%s, %s, %s, %s)
ON CONFLICT (user_id) DO UPDATE SET
    latest_alias = COALESCE(EXCLUDED.latest_alias, wpk.players.latest_alias),
    first_seen = LEAST(wpk.players.first_seen, EXCLUDED.first_seen),
    last_seen = GREATEST(wpk.players.last_seen, EXCLUDED.last_seen)
"""


def _insert_sql(table: str, columns: Sequence[str]) -> str:
    placeholders = ", ".join(["%s"] * len(columns))
    return "INSERT INTO wpk.{table} ({cols}) VALUES ({values})".format(
        table=table,
        cols=", ".join(columns),
        values=placeholders,
    )


def sync_pending(data_dir: Path, pg, limit: int = 5) -> int:
    """Sync up to `limit` dirty hands. A newer local write keeps the hand dirty."""

    local = open_local(data_dir)
    synced = 0
    try:
        pending = local.execute(
            """
            SELECT hand_id, updated_at FROM sync_state
            WHERE dirty = 1
            ORDER BY updated_at
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        for row in pending:
            hand_id = str(row["hand_id"])
            updated_at = str(row["updated_at"])
            try:
                sync_hand(pg, local, hand_id)
            except Exception as error:
                pg.rollback()
                local.execute(
                    """
                    UPDATE sync_state SET dirty = -1
                    WHERE hand_id = ? AND updated_at = ?
                    """,
                    (hand_id, updated_at),
                )
                local.commit()
                print(f"cloud sync: skip {hand_id}: {error}", file=sys.stderr, flush=True)
                continue
            cleared = local.execute(
                """
                UPDATE sync_state SET dirty = 0
                WHERE hand_id = ? AND updated_at = ? AND dirty = 1
                """,
                (hand_id, updated_at),
            )
            local.commit()
            if cleared.rowcount:
                synced += 1
    finally:
        local.close()
    return synced


def mark_all_dirty(data_dir: Path, limit: Optional[int] = None) -> int:
    local = open_local(data_dir)
    try:
        if limit is None:
            cursor = local.execute(
                """
                INSERT INTO sync_state(hand_id, updated_at, dirty)
                SELECT hand_id, COALESCE(played_at, started_at, hand_id), 1
                FROM hands
                WHERE true
                ON CONFLICT(hand_id) DO UPDATE SET
                    dirty = 1,
                    updated_at = excluded.updated_at
                """
            )
        else:
            cursor = local.execute(
                """
                INSERT INTO sync_state(hand_id, updated_at, dirty)
                SELECT hand_id, COALESCE(played_at, started_at, hand_id), 1
                FROM hands
                WHERE true
                ORDER BY played_at DESC
                LIMIT ?
                ON CONFLICT(hand_id) DO UPDATE SET
                    dirty = 1,
                    updated_at = excluded.updated_at
                """,
                (int(limit),),
            )
        local.commit()
        return int(cursor.rowcount)
    finally:
        local.close()


def pending_count(data_dir: Path) -> int:
    local = open_local(data_dir)
    try:
        row = local.execute(
            "SELECT COUNT(*) FROM sync_state WHERE dirty = 1"
        ).fetchone()
        return int(row[0])
    finally:
        local.close()


def status(data_dir: Path) -> dict:
    local = open_local(data_dir)
    try:
        hands = int(local.execute("SELECT COUNT(*) FROM hands").fetchone()[0])
        pending = int(
            local.execute("SELECT COUNT(*) FROM sync_state WHERE dirty = 1").fetchone()[0]
        )
        failed = int(
            local.execute("SELECT COUNT(*) FROM sync_state WHERE dirty = -1").fetchone()[0]
        )
    finally:
        local.close()
    cloud_hands = None
    cloud_error = None
    if cloud_sync_enabled() or os.environ.get("SUPABASE_DB_URL", "").strip():
        try:
            pg = connect_postgres()
            try:
                cloud_hands = int(pg.execute("SELECT COUNT(*) FROM wpk.hands").fetchone()[0])
            finally:
                pg.close()
        except Exception as error:
            cloud_error = str(error)
    return {
        "read_backend": read_backend(),
        "dual_write": cloud_sync_enabled(),
        "local_hands": hands,
        "pending_hands": pending,
        "failed_hands": failed,
        "cloud_hands": cloud_hands,
        "cloud_error": cloud_error,
        "skipped": ["frames", "raw_events"],
    }


def probe(data_dir: Path, limit: int = 5) -> dict:
    local = open_local(data_dir)
    pg = connect_postgres()
    try:
        started = time.perf_counter()
        pg.execute("SELECT 1").fetchone()
        ping_ms = round((time.perf_counter() - started) * 1000, 1)
        rows = local.execute(
            """
            SELECT hand_id FROM hands
            ORDER BY played_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        samples = []
        for row in rows:
            hand_id = str(row["hand_id"])
            started = time.perf_counter()
            sync_hand(pg, local, hand_id)
            samples.append(
                {
                    "hand_id": hand_id,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )
        return {"ping_ms": ping_ms, "samples": samples}
    finally:
        local.close()
        pg.close()


def backfill(data_dir: Path, limit: Optional[int] = None, batch: int = 5) -> int:
    marked = mark_all_dirty(data_dir, limit=limit)
    total = pending_count(data_dir)
    print(f"cloud sync: marked {marked} hands dirty; {total} pending", flush=True)
    pg = connect_postgres()
    done = 0
    started = time.perf_counter()
    try:
        while True:
            count = sync_pending(data_dir, pg, limit=batch)
            if count == 0:
                break
            done += count
            elapsed = time.perf_counter() - started
            print(
                f"cloud sync: {done} hands in {elapsed:.1f}s "
                f"({elapsed / done:.2f}s/hand)",
                flush=True,
            )
    finally:
        pg.close()
    return done


class CloudSyncWorker:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="wpk-cloud-sync",
            daemon=True,
        )
        self.synced = 0
        self.last_error: Optional[str] = None

    def start(self) -> "CloudSyncWorker":
        self._thread.start()
        print(
            "cloud sync: dual-write on, reads stay local "
            f"(WPK_READ_BACKEND={read_backend()})",
            flush=True,
        )
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def _run(self) -> None:
        pg = None
        while not self._stop.is_set():
            try:
                if pg is None or pg.closed:
                    pg = connect_postgres()
                count = sync_pending(self.data_dir, pg, limit=5)
                self.synced += count
                self.last_error = None
                self._stop.wait(2 if count else 5)
            except Exception as error:
                self.last_error = str(error)
                print(f"cloud sync: {error}", file=sys.stderr, flush=True)
                if pg is not None:
                    try:
                        pg.close()
                    except Exception:
                        pass
                    pg = None
                self._stop.wait(15)
        if pg is not None:
            try:
                pg.close()
            except Exception:
                pass


def start_worker(data_dir: Path) -> Optional[CloudSyncWorker]:
    if not cloud_sync_enabled():
        print("cloud sync: off (set WPK_CLOUD_SYNC=1 and SUPABASE_DB_URL)", flush=True)
        return None
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print(
            "cloud sync: skipped, install with pip install 'psycopg[binary]'",
            file=sys.stderr,
            flush=True,
        )
        return None
    return CloudSyncWorker(data_dir).start()
