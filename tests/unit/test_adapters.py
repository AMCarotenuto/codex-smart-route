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


def test_model_less_turn_on_non_auto_thread_passes_through():
    proxy = JsonLineAppServerProxy(lambda _params: decision())
    message = {"id": 2, "method": "turn/start", "params": {"threadId": "manual"}}
    assert proxy.process_client_message(message) == message


def test_auto_thread_is_correlated_and_routed_without_leaking_to_manual_thread():
    proxy = JsonLineAppServerProxy(lambda _params: decision())
    start = {"id": 1, "method": "thread/start", "params": {"model": "codex-smart-route"}}
    assert "model" not in proxy.process_client_message(start)["params"]
    proxy.observe_server_message({"id": 1, "result": {"thread": {"id": "auto"}}})

    auto_turn = proxy.process_client_message(
        {"id": 2, "method": "turn/start", "params": {"threadId": "auto"}}
    )
    manual_turn = {"id": 3, "method": "turn/start", "params": {"threadId": "manual"}}
    assert auto_turn["params"]["model"] == "future-model"
    assert auto_turn["params"]["effort"] == "high"
    assert proxy.process_client_message(manual_turn) == manual_turn


def test_manual_model_disables_auto_and_lifecycle_cleans_state():
    proxy = JsonLineAppServerProxy(lambda _params: decision())
    proxy.process_client_message(
        {
            "id": 1,
            "method": "turn/start",
            "params": {"threadId": "thread", "model": "codex-smart-route"},
        }
    )
    manual = {
        "id": 2,
        "method": "turn/start",
        "params": {"threadId": "thread", "model": "manual"},
    }
    assert proxy.process_client_message(manual) == manual
    model_less = {"id": 3, "method": "turn/start", "params": {"threadId": "thread"}}
    assert proxy.process_client_message(model_less) == model_less

    proxy.process_client_message(
        {
            "id": 4,
            "method": "turn/start",
            "params": {"threadId": "thread", "model": "codex-smart-route"},
        }
    )
    proxy.observe_server_message({"method": "thread/closed", "params": {"threadId": "thread"}})
    assert proxy.process_client_message(model_less) == model_less


@pytest.mark.parametrize("method", ["turn/interrupt", "thread/compact/start", "item/tool/call"])
def test_non_routing_protocol_messages_pass_unchanged(method):
    proxy = JsonLineAppServerProxy(lambda _params: decision())
    message = {"method": method, "params": {"threadId": "missing", "value": [1, 2]}}
    assert proxy.process_client_message(message) == message


def test_explicit_auto_without_thread_id_routes_once_but_does_not_enable_global_auto():
    proxy = JsonLineAppServerProxy(lambda _params: decision())
    explicit = {
        "id": 1,
        "method": "turn/start",
        "params": {"model": "codex-smart-route"},
    }
    assert proxy.process_client_message(explicit)["params"]["model"] == "future-model"
    implicit = {"id": 2, "method": "turn/start", "params": {}}
    assert proxy.process_client_message(implicit) == implicit


def test_shutdown_without_params_clears_auto_state():
    proxy = JsonLineAppServerProxy(lambda _params: decision())
    proxy.process_client_message(
        {
            "method": "turn/start",
            "params": {"threadId": "thread", "model": "codex-smart-route"},
        }
    )
    shutdown = {"id": 9, "method": "shutdown"}
    assert proxy.process_client_message(shutdown) == shutdown
    model_less = {"method": "turn/start", "params": {"threadId": "thread"}}
    assert proxy.process_client_message(model_less) == model_less
