from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sqlite3
import subprocess
import sys
import webbrowser
from collections import Counter
from pathlib import Path
from typing import Any, Optional, Sequence

from .assistance_policy import ASSISTANCE_MODES
from .cdp import CDPRecorder, page_target
from .decoder import decode_payload, summarize_shape
from .decision_state import decision_state_from_hand
from .inference_eval import evaluate_prompt_templates
from .inference_templates import template_ids
from .protocol import ProtocolMapper
from .reasoning import LLMReasoner
from .state import HandStateMachine
from .storage import RecorderStore


DEFAULT_URL = "https://h5.sxkxys.com/"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="wpk-recorder")
    sub = root.add_subparsers(dest="command", required=True)

    browser = sub.add_parser("browser", help="launch an isolated debug Chrome")
    browser.add_argument("--port", type=int, default=9223)
    browser.add_argument("--profile", type=Path, default=Path.home() / ".wpk-recorder" / "chrome-profile")
    browser.add_argument("--url", default=DEFAULT_URL)

    capture = sub.add_parser("capture", help="record WebSocket frames from the WPK tab")
    capture.add_argument("--port", type=int, default=9223)
    capture.add_argument("--data-dir", type=Path, default=Path("data"))
    capture.add_argument("--protocol", type=Path)
    capture.add_argument("--duration", type=float, help="stop after N seconds; Ctrl-C also stops")
    capture.add_argument("--include-sent", action="store_true", help="also capture client messages")
    capture.add_argument(
        "--retain-raw", "--retain-wire",
        dest="retain_raw",
        action="store_true",
        help="retain base64 frame bytes (sensitive; needed only during protocol discovery)",
    )

    inspect = sub.add_parser("inspect", help="summarize captured frame encodings and shapes")
    inspect.add_argument("--data-dir", type=Path, default=Path("data"))

    replay = sub.add_parser("replay", help="re-decode retained frames using a protocol map")
    replay.add_argument("--data-dir", type=Path, default=Path("data"))
    replay.add_argument("--protocol", type=Path, required=True)

    purge = sub.add_parser("purge-raw", help="remove retained raw payload bytes")
    purge.add_argument("--data-dir", type=Path, default=Path("data"))

    run = sub.add_parser("run", help="start recorder and local live dashboard")
    run.add_argument("--port", type=int, default=9223, help="Chrome CDP port")
    run.add_argument("--dashboard-port", type=int, default=8765)
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    run.add_argument("--protocol", type=Path)
    run.add_argument("--retain-wire", action="store_true")
    run.add_argument("--no-browser", action="store_true", help="do not open browser windows")
    run.add_argument(
        "--assistance-mode",
        choices=ASSISTANCE_MODES,
        default=None,
        help="server-enforced range/advice capability policy",
    )

    record = sub.add_parser("record", help="start the recorder without the dashboard")
    record.add_argument("--port", type=int, default=9223, help="Chrome CDP port")
    record.add_argument("--data-dir", type=Path, default=Path("data"))
    record.add_argument("--protocol", type=Path)
    record.add_argument("--retain-wire", action="store_true")

    dashboard = sub.add_parser("dashboard", help="start only the local live dashboard")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--data-dir", type=Path, default=Path("data"))
    dashboard.add_argument(
        "--assistance-mode",
        choices=ASSISTANCE_MODES,
        default=None,
        help="server-enforced range/advice capability policy",
    )
    evaluate = sub.add_parser(
        "eval-templates",
        help="offline replay of versioned inference prompt templates",
    )
    evaluate.add_argument("--data-dir", type=Path, default=Path("data"))
    evaluate.add_argument(
        "--template",
        dest="templates",
        action="append",
        choices=template_ids(),
        help="template ID; repeat to compare multiple templates",
    )
    evaluate.add_argument("--limit", type=int, default=50)
    evaluate.add_argument("--repeats", type=int, default=1)
    evaluate.add_argument(
        "--depth",
        choices=("light", "deep"),
        default="deep",
    )
    evaluate.add_argument("--no-persist", action="store_true")
    return root


def launch_browser(port: int, profile: Path, url: str) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    os.chmod(profile, 0o700)
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome.exists():
        raise RuntimeError("Google Chrome was not found in /Applications")
    subprocess.Popen(
        [
            str(chrome),
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=http://127.0.0.1",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"Chrome launched; local CDP port: {port}")


async def _record_while_dashboard_alive(
    recorder: CDPRecorder,
    dashboard_task: "asyncio.Task[Any]",
) -> None:
    async def record_forever() -> None:
        while True:
            try:
                await recorder.run()
            except (OSError, RuntimeError) as error:
                print(f"Recorder reconnecting: {error}", file=sys.stderr)
            await asyncio.sleep(1)

    recorder_task = asyncio.create_task(record_forever())
    try:
        done, _ = await asyncio.wait(
            (dashboard_task, recorder_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if dashboard_task in done:
            await dashboard_task
            raise RuntimeError("Dashboard server stopped unexpectedly")
        await recorder_task
    finally:
        recorder_task.cancel()
        await asyncio.gather(recorder_task, return_exceptions=True)


async def _ensure_browser(port: int) -> None:
    try:
        page_target(port)
        return
    except (OSError, RuntimeError):
        launch_browser(
            port,
            Path.home() / ".wpk-recorder" / "chrome-profile",
            DEFAULT_URL,
        )
    for _ in range(30):
        await asyncio.sleep(0.5)
        try:
            page_target(port)
            return
        except (OSError, RuntimeError):
            continue
    raise RuntimeError("Chrome WPK page did not become ready")


def capture(args: argparse.Namespace) -> int:
    page_target(args.port)
    store = RecorderStore(args.data_dir, retain_raw=args.retain_raw)
    recorder = CDPRecorder(
        store=store,
        mapper=ProtocolMapper(args.protocol),
        debug_port=args.port,
        include_sent=args.include_sent,
    )
    print("Recorder attached. Press Ctrl-C to stop.")
    try:
        asyncio.run(recorder.run(args.duration))
    except KeyboardInterrupt:
        print("\nStopping recorder...")
    finally:
        store.close()
    print(json.dumps(recorder.summary(), ensure_ascii=False, indent=2))
    return 0


def record_system(args: argparse.Namespace) -> int:
    store = RecorderStore(args.data_dir, retain_raw=args.retain_wire)
    recorder = CDPRecorder(
        store=store,
        mapper=ProtocolMapper(args.protocol),
        debug_port=args.port,
        include_sent=args.retain_wire,
    )

    async def record_forever() -> None:
        await _ensure_browser(args.port)
        print("Recorder attached. Press Ctrl-C to stop.")
        while True:
            try:
                await recorder.run()
            except (OSError, RuntimeError) as error:
                print(f"Recorder reconnecting: {error}", file=sys.stderr)
            await asyncio.sleep(1)

    try:
        asyncio.run(record_forever())
    except KeyboardInterrupt:
        print("\nStopping recorder...")
    finally:
        store.close()
    return 0


def serve_dashboard(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    uvicorn.run(
        create_app(
            args.data_dir,
            assistance_mode=args.assistance_mode,
        ),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )
    return 0


def run_system(args: argparse.Namespace) -> int:
    from uvicorn import Config, Server

    from .server import create_app

    store = RecorderStore(args.data_dir, retain_raw=args.retain_wire)
    recorder = CDPRecorder(
        store=store,
        mapper=ProtocolMapper(args.protocol),
        debug_port=args.port,
        include_sent=args.retain_wire,
    )

    async def serve_and_record() -> None:
        await _ensure_browser(args.port)
        app = create_app(
            args.data_dir,
            live_hand_provider=recorder.current_hand_snapshot,
            assistance_mode=args.assistance_mode,
        )
        server = Server(
            Config(
                app,
                host="127.0.0.1",
                port=args.dashboard_port,
                log_level="warning",
            )
        )
        # Embedded server: let asyncio.run handle Ctrl-C instead of letting
        # uvicorn replace and replay process-wide signal handlers.
        server_task = asyncio.create_task(server._serve())
        try:
            await asyncio.sleep(0.5)
            if server_task.done():
                await server_task
                raise RuntimeError("Dashboard server stopped during startup")
            dashboard_url = f"http://127.0.0.1:{args.dashboard_port}/"
            if not args.no_browser:
                webbrowser.open(dashboard_url)
            print(f"Dashboard: {dashboard_url}")
            print("Recorder attached. Press Ctrl-C to stop.")
            await _record_while_dashboard_alive(recorder, server_task)
        finally:
            server.should_exit = True
            await asyncio.gather(server_task, return_exceptions=True)

    try:
        asyncio.run(serve_and_record())
    except KeyboardInterrupt:
        print("\nStopping recorder and dashboard...")
    finally:
        store.close()
    return 0


def inspect_capture(data_dir: Path) -> int:
    db_path = data_dir / "hands.sqlite3"
    if not db_path.exists():
        raise RuntimeError(f"Capture database not found: {db_path}")
    connection = sqlite3.connect(db_path)
    rows = connection.execute(
        "SELECT encoding, decoded_json, byte_length, payload_b64 FROM frames"
    ).fetchall()
    encodings = Counter(row[0] for row in rows)
    shapes: Counter[str] = Counter()
    raw_count = 0
    sizes: Counter[int] = Counter()
    for _, decoded, byte_length, payload_b64 in rows:
        sizes[byte_length] += 1
        raw_count += bool(payload_b64)
        if decoded:
            shapes[summarize_shape(json.loads(decoded))] += 1
    hands = connection.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
    connection.close()
    print(
        json.dumps(
            {
                "frames": len(rows),
                "hands": hands,
                "raw_payloads": raw_count,
                "encodings": dict(encodings.most_common()),
                "top_shapes": dict(shapes.most_common(30)),
                "top_sizes": dict(sizes.most_common(20)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def replay(data_dir: Path, protocol_path: Path) -> int:
    db_path = data_dir / "hands.sqlite3"
    connection = sqlite3.connect(db_path)
    rows = connection.execute(
        "SELECT sha256, payload_b64 FROM frames WHERE payload_b64 IS NOT NULL"
    ).fetchall()
    raw_events = connection.execute(
        """
        SELECT sequence, timestamp, payload_json
        FROM raw_events ORDER BY sequence
        """
    ).fetchall()
    connection.execute("DELETE FROM hands")
    connection.execute("DELETE FROM decision_states")
    connection.commit()
    connection.close()
    hands_path = data_dir / "hands.jsonl"
    if hands_path.exists():
        hands_path.unlink()
    text_dir = data_dir / "text"
    if text_dir.exists():
        for path in text_dir.glob("*.txt"):
            path.unlink()
    mapper = ProtocolMapper(protocol_path)
    state = HandStateMachine()
    store = RecorderStore(data_dir)
    completed = 0
    try:
        for digest, encoded in rows:
            result = decode_payload(base64.b64decode(encoded))
            for hand in state.apply_many(mapper.canonical_events(result.value), digest[:12]):
                store.save_hand(hand)
                completed += 1
        partial = state.flush()
        if partial:
            store.save_hand(partial)
            completed += 1
        _rebuild_replay_decision_states(store, protocol_path, raw_events)
    finally:
        store.close()
    print(f"Replayed {len(rows)} raw frames; wrote {completed} hand(s)")
    return 0


def _rebuild_replay_decision_states(
    store: RecorderStore,
    protocol_path: Path,
    rows: Sequence[Sequence[Any]],
) -> int:
    mapper = ProtocolMapper(protocol_path)
    state = HandStateMachine()
    active_squid_round_id = None
    saved = 0
    for sequence, timestamp, payload_json in rows:
        payload = json.loads(payload_json)
        for event in mapper.canonical_events(payload):
            event["_event_sequence"] = int(sequence)
            event["_captured_at"] = str(timestamp)
            if event.get("event") == "squid":
                scene = int(event.get("scene") or 0)
                if scene in {1, 2, 3, 5, 6}:
                    active_squid_round_id = event.get("round_id")
                elif scene == 4:
                    active_squid_round_id = None
                continue
            state.apply(event, source="replay")
            if state.current is None:
                continue
            if active_squid_round_id:
                state.current.game_mode = "squid"
                state.current.squid_round_id = str(active_squid_round_id)
            if event.get("event") != "decision_request":
                continue
            request = state.current.pending_decision
            countdown = float(request.countdown or 0.0) if request else 0.0
            if countdown > 300:
                countdown /= 1000.0
            decision = decision_state_from_hand(
                state.current,
                source="replay",
                remaining_ms=round(countdown * 1000),
            )
            if decision is not None:
                store.save_decision_state(decision)
                saved += 1
    return saved


def purge_raw(data_dir: Path) -> int:
    db_path = data_dir / "hands.sqlite3"
    connection = sqlite3.connect(db_path)
    changed = connection.execute(
        "UPDATE frames SET payload_b64 = NULL WHERE payload_b64 IS NOT NULL"
    ).rowcount
    connection.commit()
    connection.execute("VACUUM")
    connection.close()
    frames_path = data_dir / "frames.jsonl"
    if frames_path.exists():
        sanitized = []
        for line in frames_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            item["payload_b64"] = ""
            sanitized.append(json.dumps(item, ensure_ascii=False))
        frames_path.write_text("\n".join(sanitized) + ("\n" if sanitized else ""), encoding="utf-8")
        os.chmod(frames_path, 0o600)
    print(f"Purged {changed} raw payload(s)")
    return 0


def eval_templates(args: argparse.Namespace) -> int:
    reasoner = LLMReasoner.from_env()
    if not reasoner.configured:
        raise RuntimeError(
            "LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY"
        )
    selected = args.templates or template_ids()
    report = evaluate_prompt_templates(
        args.data_dir,
        reasoner,
        selected,
        limit=max(1, min(args.limit, 1000)),
        repeats=max(1, min(args.repeats, 10)),
        reasoning_depth=args.depth,
        persist=not args.no_persist,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "browser":
            launch_browser(args.port, args.profile, args.url)
            return 0
        if args.command == "capture":
            return capture(args)
        if args.command == "record":
            return record_system(args)
        if args.command == "dashboard":
            return serve_dashboard(args)
        if args.command == "inspect":
            return inspect_capture(args.data_dir)
        if args.command == "replay":
            return replay(args.data_dir, args.protocol)
        if args.command == "purge-raw":
            return purge_raw(args.data_dir)
        if args.command == "eval-templates":
            return eval_templates(args)
        if args.command == "run":
            return run_system(args)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
