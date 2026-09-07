from __future__ import annotations

import io

import pytest

from codex_smart_route.adapters import (
    CodexCliAdapter,
    JsonLineAppServerProxy,
    patch_responses_request,
    patch_turn_start,
    stream_copy,
)
from codex_smart_route.models import Decision
from codex_smart_route.router import RoutingError, mark_forwarded


def decision():
    return Decision("selected", "future-model", "high", "future-model@high", "codex-smart-route")


def test_cli_applies_model_and_effort_before_task(tmp_path):
    command = CodexCliAdapter("codex").command(decision(), "do work", tmp_path)
    assert command[:6] == [
        "codex",
        "exec",
        "-m",
        "future-model",
        "-c",
        'model_reasoning_effort="high"',
    ]
    assert command[-1] == "do work"


def test_responses_payload_preserves_tool_loop_fields():
    source = {
        "model": "codex-smart-route",
        "input": [{"type": "function_call_output", "call_id": "x"}],
        "tools": [{"type": "function"}],
        "reasoning": {"summary": "auto"},
    }
    result = patch_responses_request(source, decision())
    assert result["input"] == source["input"]
    assert result["tools"] == source["tools"]
    assert result["model"] == "future-model"
    assert result["reasoning"] == {"summary": "auto", "effort": "high"}
    assert source["model"] == "codex-smart-route"


def test_manual_responses_payload_untouched():
    source = {"model": "manual", "reasoning": {"effort": "low"}}
    assert patch_responses_request(source, decision()) == source


def test_turn_start_applies_both_fields_and_preserves_other_settings():
    source = {"threadId": "t", "input": [], "approvalPolicy": "never"}
    result = patch_turn_start(source, decision())
    assert result == {**source, "model": "future-model", "effort": "high"}


def test_blocked_decision_cannot_forward():
    blocked = Decision("blocked", None, None, None, "codex-smart-route")
    with pytest.raises(RoutingError):
        patch_turn_start({}, blocked)


def test_streaming_bytes_preserved():
    source = io.BytesIO(b"event: one\n\ndata: two\n\n")
    sink = io.BytesIO()
    stream_copy(source, sink, 3)
    assert sink.getvalue() == source.getvalue()


def test_selected_requested_forwarded_confirmed_distinct():
    forwarded = mark_forwarded(decision())
    data = forwarded.to_dict()
    assert data["selected_model"] == "future-model"
    assert data["requested_model"] == "codex-smart-route"
    assert data["forwarded_model"] == "future-model"
    assert data["confirmed_model"] == "unverified"


def test_virtual_model_capabilities_derive_from_catalog():
    message = {
        "id": 1,
        "result": {
            "data": [
                {
                    "model": "m",
                    "inputModalities": ["text", "image"],
                    "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                }
            ]
        },
    }
    result = JsonLineAppServerProxy(lambda _params: decision()).inject_auto_model(message)
    auto = result["result"]["data"][-1]
    assert auto["model"] == "codex-smart-route"
    assert auto["inputModalities"] == ["image", "text"]
    assert auto["supportedReasoningEfforts"][0]["reasoningEffort"] == "high"
