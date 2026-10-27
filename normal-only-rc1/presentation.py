#!/usr/bin/env python3
"""Python 3.8+ lossless, self-contained B/C1 mail-body presentation.

C1 is selected only when its complete UTF-8 payload is at least 20% smaller.
Manifest checksums ALWAYS cover restored canonical B records, never C1 text.
No names/IDs are looked up outside the payload; missing vs null MID is retained.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_presentation_transport", Path(__file__).with_name("transport.py"))
t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t)
MAX_RECORDS = 500
MAX_BYTES = 4 * 1024 * 1024


class PresentationError(Exception):
    pass


def _json(text):
    try:
        return t.strict_json(text)
    except t.TransportError:
        raise PresentationError("invalid_presentation_json") from None


def _validate_records(records):
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        raise PresentationError("invalid_presentation_record_count")
    sources, seen = set(), set()
    for record in records:
        try:
            source, _ = t.validate_record(record)
        except t.TransportError:
            raise PresentationError("invalid_presentation_record") from None
        if record["id"] in seen:
            raise PresentationError("duplicate_presentation_message_id")
        seen.add(record["id"])
        sources.add(source)
    if len(sources) != 1:
        raise PresentationError("mixed_presentation_sources")
    return next(iter(sources))


def _identity(record, kind):
    refs = record["refs"]
    identity = [record[kind], refs[kind + "_pk"]]
    if kind + "_mid" in refs:
        identity.append(refs[kind + "_mid"])
    return identity


def encode_named(records):
    """Return format, LF-terminated payload, and both complete UTF-8 byte sizes."""
    source = _validate_records(records)
    b_payload = t.records_bytes(records).decode("utf-8")
    names, lookup = {"chat": [], "sender": []}, {"chat": {}, "sender": {}}
    rows = []
    for record in records:
        indexes = []
        for kind in ("chat", "sender"):
            identity = _identity(record, kind)
            key = tuple(identity)  # Name, ID and MID-presence are all identity components.
            if key not in lookup[kind]:
                lookup[kind][key] = len(names[kind])
                names[kind].append(identity)
            indexes.append(lookup[kind][key])
        rows.append([record["refs"]["message_pk"], record["time"], indexes[0], indexes[1], record["text"]])
    header = {"format": "C1", "source": source, "chats": names["chat"], "senders": names["sender"]}
    c_payload = t.canonical(header) + "\n" + "".join(t.canonical(row) + "\n" for row in rows)
    b_bytes, c_bytes = len(b_payload.encode("utf-8")), len(c_payload.encode("utf-8"))
    use_c = c_bytes * 5 <= b_bytes * 4
    payload = c_payload if use_c else b_payload
    # Guard the encoder itself against lossy schema drift.
    if use_c and t.records_bytes(decode_c1(header, rows)) != t.records_bytes(records):
        raise PresentationError("presentation_roundtrip_mismatch")
    return {"format": "C1" if use_c else "B", "payload": payload,
            "original_bytes": b_bytes, "encoded_bytes": c_bytes if use_c else b_bytes}


def _dictionary(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_RECORDS:
        raise PresentationError("invalid_c1_dictionary_size")
    seen = set()
    for identity in values:
        if not isinstance(identity, list) or len(identity) not in (2, 3):
            raise PresentationError("invalid_c1_identity_tuple")
        if not isinstance(identity[0], str) or not identity[0]:
            raise PresentationError("invalid_c1_display_name")
        if identity[1] is not None and not t._internal_id(identity[1]):
            raise PresentationError("invalid_c1_internal_id")
        if len(identity) == 3 and identity[2] is not None and not isinstance(identity[2], str):
            raise PresentationError("invalid_c1_mid")
        try:
            t.canonical(identity).encode("utf-8")
        except (UnicodeError, ValueError):
            raise PresentationError("invalid_c1_unicode") from None
        key = tuple(identity)
        if key in seen:
            raise PresentationError("duplicate_c1_identity_tuple")
        seen.add(key)
    return values


def decode_c1(header, rows):
    if not isinstance(header, dict) or set(header) != {"format", "source", "chats", "senders"}:
        raise PresentationError("invalid_c1_header_fields")
    if header["format"] != "C1":
        raise PresentationError("unsupported_presentation_format")
    source = header["source"]
    if not isinstance(source, str) or not t.SOURCE_RE.fullmatch(source):
        raise PresentationError("invalid_c1_source")
    chats, senders = _dictionary(header["chats"]), _dictionary(header["senders"])
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_RECORDS:
        raise PresentationError("invalid_c1_record_count")
    records, used_chats, used_senders = [], set(), set()
    for row in rows:
        if not isinstance(row, list) or len(row) != 5:
            raise PresentationError("invalid_c1_record_fields")
        pk, instant, ci, si, text = row
        if (type(ci) is not int or type(si) is not int or
                not 0 <= ci < len(chats) or not 0 <= si < len(senders)):
            raise PresentationError("c1_index_out_of_bounds")
        if not t._internal_id(pk):
            raise PresentationError("invalid_c1_message_pk")
        chat, sender = chats[ci], senders[si]
        refs = {"message_pk": pk, "chat_pk": chat[1], "sender_pk": sender[1]}
        if len(chat) == 3:
            refs["chat_mid"] = chat[2]
        if len(sender) == 3:
            refs["sender_mid"] = sender[2]
        records.append({"id": source + ":" + pk, "time": instant, "chat": chat[0],
                        "sender": sender[0], "text": text, "refs": refs})
        used_chats.add(ci)
        used_senders.add(si)
    if used_chats != set(range(len(chats))) or used_senders != set(range(len(senders))):
        raise PresentationError("unreferenced_c1_identity")
    _validate_records(records)
    return records


def decode_named(payload):
    if not isinstance(payload, str):
        raise PresentationError("invalid_presentation_payload_type")
    try:
        if len(payload.encode("utf-8")) > MAX_BYTES:
            raise PresentationError("presentation_payload_too_large")
    except UnicodeError:
        raise PresentationError("invalid_presentation_unicode") from None
    lines = payload.replace("\r\n", "\n").split("\n")
    if any("\r" in line for line in lines):
        raise PresentationError("bare_cr_in_presentation")
    lines = [line for line in lines if line.strip()]
    if not lines:
        raise PresentationError("empty_presentation_payload")
    first = _json(lines[0])
    if isinstance(first, dict) and "format" in first:
        return decode_c1(first, [_json(line) for line in lines[1:]])
    records = [first] + [_json(line) for line in lines[1:]]
    _validate_records(records)
    return records
