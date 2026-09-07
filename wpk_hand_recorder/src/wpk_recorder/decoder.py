from __future__ import annotations

import base64
import gzip
import hashlib
import json
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import msgpack

from .privacy import scrub


@dataclass
class DecodeResult:
    encoding: str
    value: Optional[Any]
    payload: bytes
    envelope_bytes: int = 0


def cdp_payload(opcode: int, payload_data: str) -> bytes:
    if opcode == 1:
        return payload_data.encode("utf-8", errors="replace")
    return base64.b64decode(payload_data, validate=False)


def _candidates(payload: bytes) -> Iterable[Tuple[str, bytes, int]]:
    yield "raw", payload, 0
    if payload[:2] == b"\x1f\x8b":
        try:
            yield "gzip", gzip.decompress(payload), 0
        except (OSError, EOFError):
            pass
    for label, offset, fmt in (
        ("len16be", 2, ">H"),
        ("len16le", 2, "<H"),
        ("len32be", 4, ">I"),
        ("len32le", 4, "<I"),
    ):
        if len(payload) <= offset:
            continue
        declared = struct.unpack(fmt, payload[:offset])[0]
        if declared in {len(payload), len(payload) - offset}:
            yield label, payload[offset:], offset
    for offset in (0, 2, 4, 8, 12, 16):
        if len(payload) <= offset + 2:
            continue
        try:
            expanded = zlib.decompress(payload[offset:])
        except zlib.error:
            continue
        yield f"zlib+{offset}", expanded, offset


def decode_payload(payload: bytes) -> DecodeResult:
    if len(payload) >= 6 and struct.unpack(">I", payload[:4])[0] == len(payload):
        message_id = struct.unpack(">H", payload[4:6])[0]
        body = payload[6:]
        wire = inspect_protobuf(body)
        if not body or (wire and _protobuf_likelihood(wire, len(body))):
            return DecodeResult(
                "wpk-frame:protobuf-wire",
                scrub({"message_id": message_id, "body": wire}),
                payload,
                6,
            )
    for label, candidate, offset in _candidates(payload):
        try:
            text = candidate.decode("utf-8")
            value = json.loads(text)
            return DecodeResult(f"{label}:json", scrub(value), payload, offset)
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        try:
            value = msgpack.unpackb(
                candidate,
                raw=False,
                strict_map_key=False,
                unicode_errors="replace",
            )
            if not isinstance(value, (int, float, str, bytes)) or candidate:
                return DecodeResult(f"{label}:msgpack", scrub(value), payload, offset)
        except (ValueError, TypeError, msgpack.ExtraData, msgpack.FormatError, msgpack.StackError):
            pass
    wire = inspect_protobuf(payload)
    if wire and _protobuf_likelihood(wire, len(payload)):
        return DecodeResult("protobuf-wire", scrub(wire), payload)
    return DecodeResult("unknown", None, payload)


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
    raise ValueError("varint too long")


def inspect_protobuf(data: bytes, depth: int = 0) -> List[Dict[str, Any]]:
    """Best-effort schema-less protobuf view; it never assigns field meanings."""
    if not data or depth > 2:
        return []
    fields: List[Dict[str, Any]] = []
    pos = 0
    try:
        while pos < len(data):
            key, pos = _read_varint(data, pos)
            number, wire_type = key >> 3, key & 7
            if not 0 < number < 1_000_000 or wire_type not in {0, 1, 2, 5}:
                return []
            item: Dict[str, Any] = {"field": number, "wire": wire_type}
            if wire_type == 0:
                item["value"], pos = _read_varint(data, pos)
            elif wire_type == 1:
                if pos + 8 > len(data):
                    return []
                item["value_hex"] = data[pos : pos + 8].hex()
                pos += 8
            elif wire_type == 5:
                if pos + 4 > len(data):
                    return []
                item["value_hex"] = data[pos : pos + 4].hex()
                pos += 4
            else:
                size, pos = _read_varint(data, pos)
                if size > len(data) - pos:
                    return []
                chunk = data[pos : pos + size]
                pos += size
                nested = inspect_protobuf(chunk, depth + 1)
                if nested and _protobuf_likelihood(nested, len(chunk)):
                    item["message"] = nested
                else:
                    try:
                        text = chunk.decode("utf-8")
                        item["text"] = text if text.isprintable() else None
                    except UnicodeDecodeError:
                        item["bytes"] = len(chunk)
                        item["sha256"] = hashlib.sha256(chunk).hexdigest()
            fields.append(item)
    except ValueError:
        return []
    return fields


def _protobuf_likelihood(fields: List[Dict[str, Any]], size: int) -> bool:
    return bool(fields) and size >= 2 and len(fields) <= size


def summarize_shape(value: Any) -> str:
    if isinstance(value, dict):
        return "map:" + ",".join(sorted(map(str, value.keys()))[:20])
    if isinstance(value, list):
        return f"list:{len(value)}:" + (summarize_shape(value[0]) if value else "empty")
    return type(value).__name__
