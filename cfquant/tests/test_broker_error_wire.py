"""Broker rejection payloads must survive both pipe and LTtx framing (offline)."""
import json

import pytest

from cfquant.protocol import loads_message, pack_event, pack_request, pack_response
import cfquant_web_server as web


ERROR = "[AGENT] [券商] -150906080|1|-150906080[-150906082]当前交易时间禁止委托该买卖类别'3B'!"


@pytest.mark.parametrize("prefix", ["", "response|"])
@pytest.mark.parametrize("as_bytes", [False, True])
@pytest.mark.parametrize("kind", ["request", "response", "error", "event"])
def test_protocol_preserves_pipes_in_broker_messages(prefix, as_bytes, kind):
    if kind == "request":
        raw = pack_request("offline", {"remark": ERROR})
    elif kind == "response":
        raw = pack_response("offline", result=[{"status_msg": ERROR}])
    elif kind == "error":
        raw = pack_response("offline", ok=False, error={"message": ERROR})
    else:
        raw = pack_event("trader:on_order_error", {"error_msg": ERROR})
    expected = json.loads(raw[len("cfquant:"):])
    framed = prefix + raw
    assert loads_message(framed.encode("utf-8") if as_bytes else framed) == expected


@pytest.mark.parametrize("prefix", ["", "event|"])
@pytest.mark.parametrize("packed", [False, True])
def test_web_callback_parser_preserves_error_and_identity(prefix, packed):
    data = {"error_msg": ERROR, "account_id": "TEST", "account_type": "HUGANGTONG"}
    raw = (pack_event("trader:on_order_error", data) if packed else
           json.dumps({"type": "event", "event": "trader:on_order_error", "data": data}, ensure_ascii=False))
    event = web.CallbackEventStore(channels=["offline"])._parse(prefix + raw)
    assert event["event"] == "trader:on_order_error"
    assert event["data"] == data
    assert event.get("key", "") == ("event" if prefix else "")


def test_callback_start_loads_channels_after_config_is_ready(monkeypatch):
    channels = ["default.callback"]
    monkeypatch.setattr(web, "callback_channels", lambda: list(channels))
    store = web.CallbackEventStore()
    channels.extend(["hgt.callback", "sgt.callback"])
    started = []
    monkeypatch.setattr(web.CLIENTS, "add_callback", lambda *args: None)
    monkeypatch.setattr(store, "_start_pipe_clients", lambda: started.extend(store.channels) or len(store.channels))
    monkeypatch.setattr(store, "_start_lttx_client", lambda: 0)
    store.start()
    assert started == channels
