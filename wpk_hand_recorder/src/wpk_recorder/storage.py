from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .features import derive_decisions
from .formatting import render_hand_text
from .inference import rebuild_showdown_observations
from .models import Action, HandHistory, Player, RawEvent, RawFrame, SquidEvent
from .protocol import ProtocolMapper
from .quality import assess_hand


class RecorderStore:
    def __init__(self, data_dir: Path, retain_raw: bool = False):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self.retain_raw = retain_raw
        self.db_path = self.data_dir / "hands.sqlite3"
        self.frames_path = self.data_dir / "frames.jsonl"
        self.events_path = self.data_dir / "events.jsonl"
        self.hands_path = self.data_dir / "hands.jsonl"
        self.live_path = self.data_dir / "live.log"
        self.text_dir = self.data_dir / "text"
        self.text_dir.mkdir(exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        os.chmod(self.db_path, 0o600)
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL,
                request_id TEXT NOT NULL,
                opcode INTEGER NOT NULL,
                byte_length INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                websocket_url TEXT NOT NULL,
                encoding TEXT NOT NULL,
                decoded_json TEXT,
                payload_b64 TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_frames_sha ON frames(sha256);
            CREATE TABLE IF NOT EXISTS hands (
                hand_id TEXT PRIMARY KEY,
                table_id TEXT,
                started_at TEXT,
                ended_at TEXT,
                status TEXT NOT NULL,
                game_mode TEXT NOT NULL DEFAULT 'holdem',
                hand_number INTEGER,
                played_at TEXT,
                button_seat INTEGER,
                small_blind REAL,
                big_blind REAL,
                ante REAL,
                pot REAL,
                board_json TEXT NOT NULL DEFAULT '[]',
                quality_status TEXT NOT NULL DEFAULT 'unknown',
                quality_reasons_json TEXT NOT NULL DEFAULT '[]',
                excluded_from_stats INTEGER NOT NULL DEFAULT 1,
                hand_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS raw_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence INTEGER NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                event_name TEXT NOT NULL,
                table_id TEXT,
                hand_id TEXT,
                source TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_raw_events_hand ON raw_events(hand_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_raw_events_name ON raw_events(event_name, sequence);
            CREATE TABLE IF NOT EXISTS players (
                user_id TEXT PRIMARY KEY,
                latest_alias TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hand_players (
                hand_id TEXT NOT NULL,
                seat INTEGER NOT NULL,
                user_id TEXT,
                alias TEXT,
                position TEXT,
                stack_start REAL,
                stack_end REAL,
                hole_cards_json TEXT NOT NULL,
                is_hero INTEGER NOT NULL,
                net REAL,
                PRIMARY KEY(hand_id, seat)
            );
            CREATE INDEX IF NOT EXISTS idx_hand_players_user ON hand_players(user_id, hand_id);
            CREATE TABLE IF NOT EXISTS actions (
                hand_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_sequence INTEGER,
                action_id TEXT,
                street TEXT NOT NULL,
                seat INTEGER,
                user_id TEXT,
                alias TEXT,
                action TEXT NOT NULL,
                amount REAL,
                amount_to REAL,
                stack_after REAL,
                source_message TEXT,
                PRIMARY KEY(hand_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS idx_actions_user ON actions(user_id, hand_id, street);
            CREATE TABLE IF NOT EXISTS decision_snapshots (
                hand_id TEXT NOT NULL,
                action_sequence INTEGER NOT NULL,
                event_sequence INTEGER,
                user_id TEXT,
                alias TEXT,
                seat INTEGER,
                street TEXT NOT NULL,
                position TEXT,
                game_mode TEXT NOT NULL,
                chosen_action TEXT NOT NULL,
                pot_before REAL NOT NULL,
                stack_before REAL,
                effective_stack REAL,
                effective_stack_bb REAL,
                spr REAL,
                to_call REAL NOT NULL,
                facing_action TEXT NOT NULL,
                facing_amount REAL NOT NULL,
                bet_fraction REAL,
                raise_multiple REAL,
                is_ip INTEGER,
                is_preflop_aggressor INTEGER NOT NULL,
                players_in_hand INTEGER NOT NULL,
                board_texture_json TEXT NOT NULL,
                PRIMARY KEY(hand_id, action_sequence)
            );
            CREATE INDEX IF NOT EXISTS idx_decisions_user
                ON decision_snapshots(user_id, street, position, game_mode);
            CREATE TABLE IF NOT EXISTS opportunities (
                hand_id TEXT NOT NULL,
                action_sequence INTEGER NOT NULL,
                user_id TEXT,
                metric TEXT NOT NULL,
                success INTEGER NOT NULL,
                position TEXT,
                game_mode TEXT NOT NULL,
                effective_stack_bb REAL,
                PRIMARY KEY(hand_id, action_sequence, metric)
            );
            CREATE INDEX IF NOT EXISTS idx_opportunities_user
                ON opportunities(user_id, metric, game_mode, position);
            CREATE TABLE IF NOT EXISTS board_cards (
                hand_id TEXT NOT NULL,
                card_index INTEGER NOT NULL,
                street TEXT NOT NULL,
                card TEXT NOT NULL,
                PRIMARY KEY(hand_id, card_index)
            );
            CREATE TABLE IF NOT EXISTS results (
                hand_id TEXT NOT NULL,
                seat INTEGER NOT NULL,
                user_id TEXT,
                alias TEXT,
                net REAL,
                stack_end REAL,
                shown_cards_json TEXT NOT NULL,
                PRIMARY KEY(hand_id, seat)
            );
            CREATE TABLE IF NOT EXISTS squid_rounds (
                round_id TEXT PRIMARY KEY,
                table_id TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL,
                participant_count INTEGER NOT NULL DEFAULT 0,
                hand_refs_json TEXT NOT NULL DEFAULT '[]',
                latest_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS squid_events (
                round_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                scene INTEGER NOT NULL,
                users_json TEXT NOT NULL,
                ext_json TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY(round_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS squid_settlements (
                round_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                alias TEXT,
                add_score REAL,
                current_score REAL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(round_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS hand_squid_players (
                hand_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                alias TEXT,
                squid_start INTEGER NOT NULL,
                squid_end INTEGER NOT NULL,
                PRIMARY KEY(hand_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS squid_awards (
                hand_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                alias TEXT,
                award_count INTEGER NOT NULL,
                round_id TEXT,
                event_sequence INTEGER,
                PRIMARY KEY(hand_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS showdown_observations (
                hand_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                alias TEXT,
                street TEXT NOT NULL,
                hole_cards_json TEXT NOT NULL,
                board_json TEXT NOT NULL,
                preflop_class TEXT NOT NULL,
                strength_category TEXT NOT NULL,
                draws_json TEXT NOT NULL,
                equity_vs_random REAL NOT NULL,
                position TEXT,
                effective_stack_bb REAL,
                squid_count INTEGER NOT NULL DEFAULT 0,
                cumulative_net_before REAL NOT NULL DEFAULT 0,
                profit_state TEXT NOT NULL,
                action_line TEXT NOT NULL,
                feature_tokens_json TEXT NOT NULL,
                PRIMARY KEY(hand_id, user_id, street)
            );
            CREATE INDEX IF NOT EXISTS idx_showdown_observations_player
                ON showdown_observations(user_id, street, strength_category);
            """
        )
        for name, definition in (
            ("game_mode", "TEXT NOT NULL DEFAULT 'holdem'"),
            ("hand_number", "INTEGER"),
            ("played_at", "TEXT"),
            ("button_seat", "INTEGER"),
            ("small_blind", "REAL"),
            ("big_blind", "REAL"),
            ("ante", "REAL"),
            ("pot", "REAL"),
            ("board_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("quality_status", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("quality_reasons_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("excluded_from_stats", "INTEGER NOT NULL DEFAULT 1"),
        ):
            self._ensure_column("hands", name, definition)
        self.connection.commit()
        self._backfill_decisions()
        self._repair_boards_from_raw_events()
        self._backfill_hand_squid_data()
        self._audit_existing_hands()
        rebuild_showdown_observations(self.connection)

    def _ensure_column(self, table: str, name: str, definition: str) -> None:
        columns = {
            row[1] for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if name not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _backfill_decisions(self) -> None:
        rows = self.connection.execute(
            """
            SELECT hand_json FROM hands h
            WHERE NOT EXISTS (
                SELECT 1 FROM decision_snapshots d WHERE d.hand_id = h.hand_id
            )
            """
        ).fetchall()
        for row in rows:
            try:
                hand = _hand_from_dict(json.loads(row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if hand.actions:
                self.save_hand(hand, final=False)

    def _repair_boards_from_raw_events(self) -> None:
        mapper = ProtocolMapper()
        rows = self.connection.execute(
            "SELECT hand_id, hand_json FROM hands"
        ).fetchall()
        for hand_id, encoded_hand in rows:
            hand = _hand_from_dict(json.loads(encoded_hand))
            recovered = list(hand.board)
            recovered_pot = hand.pot
            events = self.connection.execute(
                """
                SELECT payload_json FROM raw_events
                WHERE hand_id = ? AND event_name = 'roundChangeNotify'
                ORDER BY sequence
                """,
                (hand_id,),
            ).fetchall()
            for (payload_json,) in events:
                payload = json.loads(payload_json)
                for event in mapper.canonical_events(payload):
                    if event.get("event") != "board":
                        continue
                    for card in event.get("append_cards") or []:
                        if card not in recovered:
                            recovered.append(card)
                    if event.get("pot") is not None:
                        recovered_pot = _number(event.get("pot"))
            if len(recovered) > len(hand.board):
                hand.board = recovered
                hand.pot = recovered_pot
                self.save_hand(hand, final=False)

    def _audit_existing_hands(self) -> None:
        rows = self.connection.execute(
            "SELECT hand_id, hand_json FROM hands"
        ).fetchall()
        with self.connection:
            for hand_id, encoded_hand in rows:
                hand = _hand_from_dict(json.loads(encoded_hand))
                quality, reasons, excluded = assess_hand(
                    hand, stale_in_progress=True
                )
                hand.quality_status = quality
                hand.quality_reasons = reasons
                self.connection.execute(
                    """
                    UPDATE hands
                    SET hand_number = ?, played_at = ?,
                        quality_status = ?, quality_reasons_json = ?,
                        excluded_from_stats = ?, hand_json = ?
                    WHERE hand_id = ?
                    """,
                    (
                        _hand_number(hand.hand_id),
                        _normalize_time(hand.started_at or hand.ended_at),
                        quality,
                        json.dumps(reasons, ensure_ascii=False),
                        int(excluded),
                        json.dumps(
                            hand.as_dict(), ensure_ascii=False, default=_json_default
                        ),
                        hand_id,
                    ),
                )

    def _backfill_hand_squid_data(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM hand_squid_players")
            self.connection.execute("DELETE FROM squid_awards")
            rows = self.connection.execute(
                """
                SELECT sequence, event_name, hand_id, payload_json
                FROM raw_events
                WHERE event_name IN ('upDateRoomNotify', 'squidGameNotify')
                ORDER BY sequence
                """
            ).fetchall()
            for sequence, event_name, hand_id, payload_json in rows:
                if hand_id:
                    self._save_hand_squid_payload(
                        event_name,
                        hand_id,
                        json.loads(payload_json),
                        sequence,
                    )

    def save_frame(self, frame: RawFrame) -> None:
        item = frame.as_dict()
        if not self.retain_raw:
            item["payload_b64"] = ""
        decoded_json = json.dumps(frame.decoded, ensure_ascii=False, default=_json_default)
        self.connection.execute(
            """
            INSERT INTO frames (
                timestamp, direction, request_id, opcode, byte_length, sha256,
                websocket_url, encoding, decoded_json, payload_b64
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                frame.timestamp,
                frame.direction,
                frame.request_id,
                frame.opcode,
                frame.byte_length,
                frame.sha256,
                frame.websocket_url,
                frame.encoding,
                decoded_json if frame.decoded is not None else None,
                item["payload_b64"] or None,
            ),
        )
        self.connection.commit()
        _append_json(self.frames_path, item)

    def save_raw_event(self, event: RawEvent) -> None:
        item = event.as_dict()
        encoded = json.dumps(event.payload, ensure_ascii=False, default=_json_default)
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO raw_events (
                sequence, timestamp, event_name, table_id, hand_id, source, sha256, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.sequence,
                event.timestamp,
                event.event_name,
                event.table_id,
                event.hand_id,
                event.source,
                event.sha256,
                encoded,
            ),
        )
        if cursor.rowcount and event.hand_id:
            self._save_hand_squid_payload(
                event.event_name,
                event.hand_id,
                event.payload,
                event.sequence,
            )
        self.connection.commit()
        if cursor.rowcount:
            _append_json(self.events_path, item)

    def _save_hand_squid_payload(
        self,
        event_name: str,
        hand_id: str,
        payload: Dict[str, Any],
        sequence: int,
    ) -> None:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if event_name == "upDateRoomNotify":
            for player in data.get("sitUserList") or []:
                if not isinstance(player, dict) or player.get("squidWin") is None:
                    continue
                user_id = str(player.get("userId") or "")
                if not user_id:
                    continue
                count = int(player.get("squidWin") or 0)
                self.connection.execute(
                    """
                    INSERT INTO hand_squid_players(
                        hand_id, user_id, alias, squid_start, squid_end
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(hand_id, user_id) DO UPDATE SET
                        alias=COALESCE(excluded.alias, hand_squid_players.alias),
                        squid_end=excluded.squid_end
                    """,
                    (hand_id, user_id, player.get("nickname"), count, count),
                )
        elif event_name == "squidGameNotify" and int(data.get("scene") or 0) == 3:
            ext = _json_object(data.get("ext"))
            award_count = int(ext.get("winSquidNum") or 1)
            for user in data.get("users") or []:
                user_id = str(user)
                row = self.connection.execute(
                    """
                    SELECT alias FROM hand_squid_players
                    WHERE hand_id = ? AND user_id = ?
                    """,
                    (hand_id, user_id),
                ).fetchone()
                alias = row[0] if row else self._alias_for_user(user_id)
                self.connection.execute(
                    """
                    INSERT INTO squid_awards(
                        hand_id, user_id, alias, award_count, event_sequence
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(hand_id, user_id) DO UPDATE SET
                        alias=COALESCE(excluded.alias, squid_awards.alias),
                        award_count=MAX(squid_awards.award_count, excluded.award_count),
                        event_sequence=MAX(squid_awards.event_sequence, excluded.event_sequence)
                    """,
                    (hand_id, user_id, alias, award_count, sequence),
                )

    def max_event_sequence(self) -> int:
        row = self.connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM raw_events").fetchone()
        return int(row[0])

    def load_hand(self, hand_id: str) -> Optional[HandHistory]:
        row = self.connection.execute(
            "SELECT hand_json FROM hands WHERE hand_id = ?", (hand_id,)
        ).fetchone()
        return _hand_from_dict(json.loads(row[0])) if row else None

    def save_hand(self, hand: HandHistory, final: bool = True) -> None:
        decisions = derive_decisions(hand)
        quality, reasons, excluded = assess_hand(hand)
        hand.quality_status = quality
        hand.quality_reasons = reasons
        item = hand.as_dict()
        encoded = json.dumps(item, ensure_ascii=False, default=_json_default)
        hand_number = _hand_number(hand.hand_id)
        played_at = _normalize_time(hand.started_at or hand.ended_at)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO hands (
                    hand_id, table_id, started_at, ended_at, status, game_mode,
                    hand_number, played_at,
                    button_seat, small_blind, big_blind, ante, pot, board_json,
                    quality_status, quality_reasons_json, excluded_from_stats, hand_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hand_id) DO UPDATE SET
                    table_id=excluded.table_id,
                    started_at=excluded.started_at,
                    ended_at=excluded.ended_at,
                    status=excluded.status,
                    game_mode=excluded.game_mode,
                    hand_number=excluded.hand_number,
                    played_at=excluded.played_at,
                    button_seat=excluded.button_seat,
                    small_blind=excluded.small_blind,
                    big_blind=excluded.big_blind,
                    ante=excluded.ante,
                    pot=excluded.pot,
                    board_json=excluded.board_json,
                    quality_status=excluded.quality_status,
                    quality_reasons_json=excluded.quality_reasons_json,
                    excluded_from_stats=excluded.excluded_from_stats,
                    hand_json=excluded.hand_json
                """,
                (
                    hand.hand_id,
                    hand.table_id,
                    hand.started_at,
                    hand.ended_at,
                    hand.status,
                    hand.game_mode,
                    hand_number,
                    played_at,
                    hand.button_seat,
                    hand.small_blind,
                    hand.big_blind,
                    hand.ante,
                    hand.pot,
                    json.dumps(hand.board),
                    quality,
                    json.dumps(reasons, ensure_ascii=False),
                    int(excluded),
                    encoded,
                ),
            )
            for table in (
                "hand_players",
                "actions",
                "decision_snapshots",
                "opportunities",
                "board_cards",
                "results",
            ):
                self.connection.execute(f"DELETE FROM {table} WHERE hand_id = ?", (hand.hand_id,))
            for player in hand.players.values():
                if player.user_id:
                    self.connection.execute(
                        """
                        INSERT INTO players(user_id, latest_alias, first_seen, last_seen)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            latest_alias=COALESCE(excluded.latest_alias, players.latest_alias),
                            last_seen=excluded.last_seen
                        """,
                        (player.user_id, player.alias, hand.started_at or "", hand.ended_at or ""),
                    )
                self.connection.execute(
                    """
                    INSERT INTO hand_players (
                        hand_id, seat, user_id, alias, position, stack_start, stack_end,
                        hole_cards_json, is_hero, net
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hand.hand_id,
                        player.seat,
                        player.user_id,
                        player.alias,
                        player.position,
                        player.stack_start,
                        player.stack_end,
                        json.dumps(player.hole_cards),
                        int(player.is_hero),
                        player.net,
                    ),
                )
                self.connection.execute(
                    """
                    INSERT INTO results(
                        hand_id, seat, user_id, alias, net, stack_end, shown_cards_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hand.hand_id,
                        player.seat,
                        player.user_id,
                        player.alias,
                        player.net,
                        player.stack_end,
                        json.dumps(player.hole_cards),
                    ),
                )
            for action in hand.actions:
                self.connection.execute(
                    """
                    INSERT INTO actions (
                        hand_id, sequence, event_sequence, action_id, street, seat, user_id,
                        alias, action, amount, amount_to, stack_after, source_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hand.hand_id,
                        action.sequence,
                        action.event_sequence,
                        action.action_id,
                        action.street,
                        action.seat,
                        action.user_id,
                        action.player,
                        action.action,
                        action.amount,
                        action.amount_to,
                        action.stack_after,
                        action.source_message,
                    ),
                )
            for decision in decisions:
                self.connection.execute(
                    """
                    INSERT INTO decision_snapshots(
                        hand_id, action_sequence, event_sequence, user_id, alias, seat,
                        street, position, game_mode, chosen_action, pot_before,
                        stack_before, effective_stack, effective_stack_bb, spr, to_call,
                        facing_action, facing_amount, bet_fraction, raise_multiple, is_ip,
                        is_preflop_aggressor, players_in_hand, board_texture_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision.hand_id,
                        decision.action_sequence,
                        decision.event_sequence,
                        decision.user_id,
                        decision.alias,
                        decision.seat,
                        decision.street,
                        decision.position,
                        decision.game_mode,
                        decision.chosen_action,
                        decision.pot_before,
                        decision.stack_before,
                        decision.effective_stack,
                        decision.effective_stack_bb,
                        decision.spr,
                        decision.to_call,
                        decision.facing_action,
                        decision.facing_amount,
                        decision.bet_fraction,
                        decision.raise_multiple,
                        None if decision.is_ip is None else int(decision.is_ip),
                        int(decision.is_preflop_aggressor),
                        decision.players_in_hand,
                        json.dumps(decision.board_texture),
                    ),
                )
                for opportunity in decision.opportunities:
                    self.connection.execute(
                        """
                        INSERT INTO opportunities(
                            hand_id, action_sequence, user_id, metric, success,
                            position, game_mode, effective_stack_bb
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            decision.hand_id,
                            decision.action_sequence,
                            decision.user_id,
                            opportunity.metric,
                            int(opportunity.success),
                            decision.position,
                            decision.game_mode,
                            decision.effective_stack_bb,
                        ),
                    )
            for index, card in enumerate(hand.board):
                street = "flop" if index < 3 else "turn" if index == 3 else "river"
                self.connection.execute(
                    "INSERT INTO board_cards(hand_id, card_index, street, card) VALUES (?, ?, ?, ?)",
                    (hand.hand_id, index, street, card),
                )
        if final:
            _append_json(self.hands_path, item)
            text_path = self.text_dir / f"{_safe_filename(hand.hand_id)}.txt"
            text_path.write_text(render_text(hand), encoding="utf-8")
            os.chmod(text_path, 0o600)
            rebuild_showdown_observations(self.connection, hand.hand_id)

    def save_squid_event(self, event: SquidEvent, table_id: Optional[str] = None) -> None:
        item = event.as_dict()
        status = "completed" if event.scene == 4 else "running"
        if event.scene in {5, 6}:
            status = "waiting" if event.scene == 6 else "restored"
        latest = json.dumps(item, ensure_ascii=False, default=_json_default)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO squid_rounds(
                    round_id, table_id, started_at, ended_at, status,
                    participant_count, hand_refs_json, latest_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(round_id) DO UPDATE SET
                    ended_at=excluded.ended_at,
                    status=excluded.status,
                    participant_count=MAX(squid_rounds.participant_count, excluded.participant_count),
                    hand_refs_json=excluded.hand_refs_json,
                    latest_json=excluded.latest_json
                """,
                (
                    event.round_id,
                    table_id,
                    event.timestamp,
                    event.timestamp if event.scene == 4 else None,
                    status,
                    len(event.users),
                    json.dumps(event.hand_refs),
                    latest,
                ),
            )
            self.connection.execute(
                """
                INSERT OR REPLACE INTO squid_events(
                    round_id, sequence, timestamp, scene, users_json, ext_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.round_id,
                    event.sequence,
                    event.timestamp,
                    event.scene,
                    json.dumps(event.users),
                    json.dumps(event.ext, ensure_ascii=False),
                    json.dumps(event.raw, ensure_ascii=False, default=_json_default),
                ),
            )
            for result in event.settlements:
                user_id = str(result.get("userId", ""))
                if not user_id:
                    continue
                alias = self._alias_for_user(user_id)
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO squid_settlements(
                        round_id, user_id, alias, add_score, current_score, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.round_id,
                        user_id,
                        alias,
                        _number(result.get("addScore")),
                        _number(result.get("curScore")),
                        json.dumps(result, ensure_ascii=False, default=_json_default),
                    ),
                )
            for hand_ref in event.hand_refs:
                self.connection.execute(
                    """
                    UPDATE hands
                    SET game_mode = 'squid'
                    WHERE hand_id = ? OR hand_id LIKE ?
                    """,
                    (hand_ref, f"%-{hand_ref}"),
                )
        _append_json(self.data_dir / "squid_events.jsonl", item)

    def _alias_for_user(self, user_id: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT latest_alias FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.connection.close()

    def append_live(self, line: str) -> None:
        with self.live_path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")
            handle.flush()
        os.chmod(self.live_path, 0o600)


def render_text(hand: HandHistory) -> str:
    return render_hand_text(hand)


def _append_json(path: Path, item: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, default=_json_default) + "\n")
    os.chmod(path, 0o600)


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)[:160]


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    return str(value)


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _hand_number(hand_id: str) -> Optional[int]:
    try:
        return int(hand_id.rsplit("-", 1)[-1])
    except (TypeError, ValueError):
        return None


def _normalize_time(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    try:
        if text.replace(".", "", 1).isdigit():
            numeric = float(text)
            if numeric > 100_000_000_000:
                numeric /= 1000
            parsed = datetime.fromtimestamp(numeric, timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _hand_from_dict(data: Dict[str, Any]) -> HandHistory:
    player_fields = {
        "seat",
        "user_id",
        "alias",
        "position",
        "stack_start",
        "stack_end",
        "hole_cards",
        "is_hero",
        "net",
    }
    action_fields = {
        "street",
        "seat",
        "player",
        "action",
        "amount",
        "amount_to",
        "user_id",
        "action_id",
        "stack_after",
        "event_sequence",
        "sequence",
        "source_message",
    }
    players = {
        int(item["seat"]): Player(
            **{key: value for key, value in item.items() if key in player_fields}
        )
        for item in data.get("players", [])
        if item.get("seat") is not None
    }
    actions = [
        Action(**{key: value for key, value in item.items() if key in action_fields})
        for item in data.get("actions", [])
    ]
    hand_fields = {
        "hand_id",
        "table_id",
        "started_at",
        "ended_at",
        "button_seat",
        "small_blind",
        "big_blind",
        "ante",
        "board",
        "pot",
        "status",
        "warnings",
        "raw_message_count",
        "game_mode",
        "squid_round_id",
        "quality_status",
        "quality_reasons",
    }
    hand = HandHistory(
        **{key: value for key, value in data.items() if key in hand_fields}
    )
    hand.players = players
    hand.actions = actions
    return hand
