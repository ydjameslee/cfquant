"""Callback event identity is bridge-owned, even on shared client channels."""
import json

import pytest

import cfquant_web_server as web
from cfquant.pipe_hub import CfquantPipeHub
from cfquant.pipe_transport import loads_pipe_message
from cfquant.protocol import loads_message
from cfquant.qmt_bridge import CfquantQmtBridge
from cfquant.tx_trade_bridge import TxTradeBridge


class RecordingTx(object):
    def __init__(self):
        self.pushes = []

    def push(self, key, payload, channel):
        self.pushes.append((key, payload, channel))


class RecordingReceiver(object):
    def __init__(self):
        self.frames = []

    def write_frame(self, raw):
        self.frames.append(raw)


def delivered_event(receiver):
    return loads_message(loads_pipe_message(receiver.frames[-1])["payload"])


@pytest.mark.parametrize("bridge_kind, bridge_id", [
    ("qmt", "qmt-bridge-a"),
    ("trade", "trade-bridge-b"),
])
def test_client_callback_event_uses_owning_bridge_identity(monkeypatch, bridge_kind, bridge_id):
    if bridge_kind == "qmt":
        monkeypatch.setattr("cfquant.qmt_bridge.get_config", lambda: {
            "host": "127.0.0.1", "port": 2049, "token": "LTtx",
            "request_channel": "cfquant.request", "bridge_id": bridge_id,
        })
        bridge = CfquantQmtBridge(None, show=False)
    else:
        bridge = TxTradeBridge(None, bridge_id=bridge_id, show=False)
    bridge.tx = RecordingTx()
    data = {"order_id": 17, "nested": {"status": "accepted"}}
    meta = {"stage": "callback", "bridge_id": "foreign-bridge"}

    bridge._send_event("shared-client", "trader:on_stock_order", data, subscription_id=4, meta=meta)

    _, raw, channel = bridge.tx.pushes[0]
    event = loads_message(raw)
    assert channel == "shared-client"
    assert event["client_id"] == "shared-client"
    assert event["subscription_id"] == 4
    assert event["data"] == data
    assert event["meta"] == {"stage": "callback", "bridge_id": bridge_id}
    assert meta == {"stage": "callback", "bridge_id": "foreign-bridge"}


def test_registered_pipe_bridge_routes_shared_web_callback_to_its_subscriber(monkeypatch):
    monkeypatch.setattr(CfquantPipeHub, "_write_status", lambda self: None)
    hub = CfquantPipeHub(pipe_name="offline", show=False)
    receiver = RecordingReceiver()
    qmt_a, qmt_b = object(), object()
    hub.qmt_conn_meta_by_conn[qmt_a] = {"bridge_id": "bridge-a"}
    hub.qmt_conn_meta_by_conn[qmt_b] = {"bridge_id": "bridge-b"}
    hub.client_by_id["web-shared"] = receiver

    route = web.LttxWebRouteServer()
    route.tx = RecordingTx()
    with route._lock:
        for bridge_id, client_id in (("bridge-a", "browser-a"), ("bridge-b", "browser-b")):
            for account_key in web.account_subscription_keys("ACCOUNT", "STOCK", bridge_id, ""):
                route._account_subscribers.setdefault(account_key, set()).add(client_id)

    for qmt_conn in (qmt_a, qmt_b):
        hub._handle_qmt_publish(qmt_conn, {
            "channel": "web-shared",
            "payload": web.pack_event("trader:on_stock_order", {
                "account_id": "ACCOUNT", "account_type": "STOCK", "order_id": 17,
            }, client_id="web-shared"),
        })
        route._on_client_event(delivered_event(receiver))

    assert [channel for _, _, channel in route.tx.pushes] == ["browser-a", "browser-b"]


@pytest.mark.parametrize("claimed", ["foreign-bridge", "foreign-envelope"])
def test_registered_pipe_bridge_drops_conflicting_callback_identity(monkeypatch, claimed):
    monkeypatch.setattr(CfquantPipeHub, "_write_status", lambda self: None)
    hub = CfquantPipeHub(pipe_name="offline", show=False)
    receiver = RecordingReceiver()
    qmt_conn = object()
    hub.qmt_conn_meta_by_conn[qmt_conn] = {"bridge_id": "bridge-a"}
    hub.client_by_id["web-shared"] = receiver
    envelope = {
        "channel": "web-shared",
        "payload": web.pack_event("trader:on_stock_order", {"account_id": "ACCOUNT"}, client_id="web-shared"),
    }
    if claimed == "foreign-bridge":
        envelope["payload"] = web.pack_event(
            "trader:on_stock_order", {"account_id": "ACCOUNT"}, client_id="web-shared",
            meta={"bridge_id": claimed},
        )
    else:
        envelope["bridge_id"] = claimed

    hub._handle_qmt_publish(qmt_conn, envelope)

    assert receiver.frames == []


def test_missing_registration_identity_is_not_inferred_from_publish(monkeypatch):
    monkeypatch.setattr(CfquantPipeHub, "_write_status", lambda self: None)
    hub = CfquantPipeHub(pipe_name="offline", show=False)
    receiver, qmt = RecordingReceiver(), object()
    hub.qmt_conn_meta_by_conn[qmt] = {"bridge_id": "-"}
    hub.client_by_id['web-shared'] = receiver
    hub._handle_qmt_publish(qmt, {
        'bridge_id': 'unregistered-claim', 'channel': 'web-shared',
        'payload': web.pack_event('trader:on_stock_order', {'account_id': 'ACCOUNT'},
                                  client_id='web-shared'),
    })
    assert hub.qmt_conn_meta_by_conn[qmt]['bridge_id'] == '-'
    assert not (delivered_event(receiver).get('meta') or {}).get('bridge_id')


def test_registered_normal_raw_callback_retains_bridge_and_broker_payload(monkeypatch):
    monkeypatch.setattr(CfquantPipeHub, "_write_status", lambda self: None)
    hub = CfquantPipeHub(pipe_name="offline", show=False)
    receiver, qmt = RecordingReceiver(), object()
    hub.qmt_conn_meta_by_conn[qmt] = {'bridge_id': 'bridge-hgt'}
    hub.client_by_id['hgt.callback'] = receiver
    original = {'type': 'event', 'event': 'trader:on_stock_order',
                'bridge_id': 'bridge-hgt', 'account_id': 'ACCOUNT',
                'account_type': 'HUGANGTONG',
                'data': {'order_sysid': 'OFFLINE-26', 'order_status': 54,
                         'm_strTradingDay': None}}
    hub._handle_qmt_publish(qmt, {'channel': 'hgt.callback', 'payload': json.dumps(original)})
    normalized = web.CallbackEventStore(channels=['hgt.callback'])._normalize_channel_event(
        delivered_event(receiver))
    for key in original:
        assert normalized[key] == original[key]
