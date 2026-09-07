import json
import struct
import zlib

import msgpack

from wpk_recorder.decoder import decode_payload, inspect_protobuf


def test_decodes_json_and_redacts_tokens():
    result = decode_payload(json.dumps({"event": "start", "token": "secret"}).encode())
    assert result.encoding == "raw:json"
    assert result.value["event"] == "start"
    assert result.value["token"] == "[REDACTED]"


def test_decodes_msgpack_with_length_prefix():
    packed = msgpack.packb({"event": "action", "seat": 2, "action": "call"})
    result = decode_payload(struct.pack(">I", len(packed)) + packed)
    assert result.encoding == "len32be:msgpack"
    assert result.value["seat"] == 2


def test_decodes_zlib_msgpack():
    packed = msgpack.packb({"event": "board", "cards": ["As", "Kd", "2c"]})
    result = decode_payload(zlib.compress(packed))
    assert result.encoding == "zlib+0:msgpack"
    assert result.value["cards"] == ["As", "Kd", "2c"]


def test_inspects_schema_less_protobuf():
    # field 1 varint 150, field 2 string "call"
    wire = bytes([0x08, 0x96, 0x01, 0x12, 0x04]) + b"call"
    fields = inspect_protobuf(wire)
    assert fields[0] == {"field": 1, "wire": 0, "value": 150}
    assert fields[1]["text"] == "call"


def test_decodes_wpk_length_and_message_id_envelope():
    body = bytes([0x08, 0x01])
    packet = struct.pack(">IH", len(body) + 6, 321) + body
    result = decode_payload(packet)
    assert result.encoding == "wpk-frame:protobuf-wire"
    assert result.value["message_id"] == 321
    assert result.value["body"][0]["value"] == 1
