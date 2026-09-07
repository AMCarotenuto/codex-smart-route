from codex_smart_route.adapters import JsonLineAppServerProxy
from codex_smart_route.models import Decision


def _decision():
    return Decision(
        "selected",
        "future-model",
        "high",
        "future-model@high",
        "codex-smart-route",
    )


def test_auto_and_manual_threads_remain_isolated_through_lifecycle():
    proxy = JsonLineAppServerProxy(lambda _params: _decision())

    auto_start = {
        "id": "auto-start",
        "method": "thread/start",
        "params": {"model": "codex-smart-route"},
    }
    manual_start = {
        "id": "manual-start",
        "method": "thread/start",
        "params": {"model": "manual-model"},
    }
    assert "model" not in proxy.process_client_message(auto_start)["params"]
    assert proxy.process_client_message(manual_start) == manual_start
    proxy.observe_server_message({"id": "auto-start", "result": {"thread": {"id": "auto-thread"}}})
    proxy.observe_server_message(
        {"id": "manual-start", "result": {"thread": {"id": "manual-thread"}}}
    )

    auto_turn = proxy.process_client_message(
        {"method": "turn/start", "params": {"threadId": "auto-thread"}}
    )
    manual_turn = {"method": "turn/start", "params": {"threadId": "manual-thread"}}
    assert auto_turn["params"]["model"] == "future-model"
    assert auto_turn["params"]["effort"] == "high"
    assert proxy.process_client_message(manual_turn) == manual_turn

    for method in ("thread/compact/start", "turn/interrupt"):
        traffic = {"method": method, "params": {"threadId": "auto-thread"}}
        assert proxy.process_client_message(traffic) == traffic

    archive = {"method": "thread/archive", "params": {"threadId": "auto-thread"}}
    assert proxy.process_client_message(archive) == archive
    after_archive = {"method": "turn/start", "params": {"threadId": "auto-thread"}}
    assert proxy.process_client_message(after_archive) == after_archive


def test_resume_auto_routes_only_resumed_thread():
    proxy = JsonLineAppServerProxy(lambda _params: _decision())
    resume = {
        "id": 1,
        "method": "thread/resume",
        "params": {"threadId": "resumed", "model": "codex-smart-route"},
    }
    assert "model" not in proxy.process_client_message(resume)["params"]
    routed = proxy.process_client_message(
        {"method": "turn/start", "params": {"threadId": "resumed"}}
    )
    assert routed["params"]["model"] == "future-model"
    untouched = {"method": "turn/start", "params": {"threadId": "other"}}
    assert proxy.process_client_message(untouched) == untouched
