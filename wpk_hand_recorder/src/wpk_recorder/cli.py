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
from typing import Optional, Sequence

from .cdp import CDPRecorder, page_target
from .decoder import decode_payload, summarize_shape
from .protocol import ProtocolMapper
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
        try:
            page_target(args.port)
        except (OSError, RuntimeError):
            launch_browser(
                args.port,
                Path.home() / ".wpk-recorder" / "chrome-profile",
                DEFAULT_URL,
            )
            for _ in range(30):
                await asyncio.sleep(0.5)
                try:
                    page_target(args.port)
                    break
                except (OSError, RuntimeError):
                    continue
            else:
                raise RuntimeError("Chrome WPK page did not become ready")
        app = create_app(args.data_dir)
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
        await asyncio.sleep(0.5)
        dashboard_url = f"http://127.0.0.1:{args.dashboard_port}/"
        if not args.no_browser:
            webbrowser.open(dashboard_url)
        print(f"Dashboard: {dashboard_url}")
        print("Recorder attached. Press Ctrl-C to stop.")
        try:
            while not server_task.done():
                try:
                    await recorder.run()
                except (OSError, RuntimeError) as error:
                    print(f"Recorder reconnecting: {error}", file=sys.stderr)
                if not server_task.done():
                    await asyncio.sleep(1)
        finally:
            server.should_exit = True
            await server_task

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
    connection.execute("DELETE FROM hands")
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
    finally:
        store.close()
    print(f"Replayed {len(rows)} raw frames; wrote {completed} hand(s)")
    return 0


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


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "browser":
            launch_browser(args.port, args.profile, args.url)
            return 0
        if args.command == "capture":
            return capture(args)
        if args.command == "inspect":
            return inspect_capture(args.data_dir)
        if args.command == "replay":
            return replay(args.data_dir, args.protocol)
        if args.command == "purge-raw":
            return purge_raw(args.data_dir)
        if args.command == "run":
            return run_system(args)
    except (OSError, RuntimeError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
