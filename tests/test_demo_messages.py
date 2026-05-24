from __future__ import annotations

import json

import pytest

from duna_orders.demo_messages import (
    DemoMessagesFile,
    DemoMessagesLoadError,
    load_demo_messages,
)


def test_demo_messages_load_with_expected_shape() -> None:
    demo_messages = load_demo_messages()

    assert isinstance(demo_messages, DemoMessagesFile)
    assert len(demo_messages.messages) == 16
    assert all(entry.id.startswith("msg_") for entry in demo_messages.messages)
    assert all(entry.message.strip() for entry in demo_messages.messages)
    assert all(entry.description.strip() for entry in demo_messages.messages)


def test_demo_messages_include_realistic_parser_edge_cases() -> None:
    demo_messages = load_demo_messages()
    messages_by_id = {entry.id: entry.message for entry in demo_messages.messages}

    assert "sancocho de pescado" in messages_by_id["msg_005_unmatched_with_notes"]
    assert "unas empanaditas" in messages_by_id["msg_006_ambiguous_quantity_informal"]
    assert "no espera mejor" in messages_by_id["msg_010_in_message_correction"]
    assert "a qué hora abren" in messages_by_id["msg_015_not_an_order"]
    assert "parce" in messages_by_id["msg_016_informal_messy"]


def test_demo_messages_reject_invalid_schema(tmp_path) -> None:
    path = tmp_path / "bad_messages.json"
    path.write_text(json.dumps({"messages": [{"id": "msg_bad"}]}), encoding="utf-8")

    with pytest.raises(DemoMessagesLoadError):
        load_demo_messages(path)