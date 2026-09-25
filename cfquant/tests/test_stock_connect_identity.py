import json

import pytest

from cfquant import account_routing, order_meta, xtconstant
from cfquant.normal_bridge import NormalQmtBridge
from cfquant.xttrader import XtQuantTrader, XtQuantTraderCallback, _account_type_name, _account_type_value
from cfquant.xttype import StockAccount, XtOrder, XtTrade


class RecordingTx(object):
    def __init__(self):
        self.pushes = []

    def push(self, kind, payload, target):
        self.pushes.append((kind, payload, target))


@pytest.mark.parametrize("value, expected", [
    (2, "STOCK"), ("stock_account", "STOCK"),
    (7, "HUGANGTONG"), ("HGT", "HUGANGTONG"), ("hugangtong_account", "HUGANGTONG"),
    (11, "SHENGANGTONG"), ("SGT", "SHENGANGTONG"), ("shengangtong_account", "SHENGANGTONG"),
])
def test_stock_connect_account_types_normalize_across_routing_metadata_and_sdk(value, expected):
    expected_value = getattr(xtconstant, expected + "_ACCOUNT", xtconstant.SECURITY_ACCOUNT)

    assert account_routing._account_type(value) == expected
    assert order_meta.normalize_account_type(value) == expected
    assert _account_type_name(value) == expected
    assert _account_type_value(value) == expected_value
    assert StockAccount("CONNECT-1", str(value)).account_type == expected_value


def test_same_account_id_routes_broker_type_callbacks_to_only_matching_connect_client():
    bridge = NormalQmtBridge(None, bridge_id="stock-connect-routing", show=False,
                             schedule_timer=False, order_meta_enabled=False)
    bridge.tx = RecordingTx()
    account_id = "SAME-ACCOUNT"
    try:
        account_routing.subscribe(bridge.bridge_id, account_id, "stock-client", account_type="STOCK")
        account_routing.subscribe(bridge.bridge_id, account_id, "hgt-client", account_type="HGT")
        account_routing.subscribe(bridge.bridge_id, account_id, "sgt-client", account_type="SGT")
        # A subsequent subscription can change this mutable bridge state; it
        # must not override QMT's raw callback broker type.
        bridge.account_type = "SHENGANGTONG"

        bridge.publish_callback_event("trader:on_stock_trade", {
            "m_strAccountID": account_id,
            "m_nBrokerType": 7,
            "m_strInstrumentID": "00700",
            "m_strExchangeID": "SH",
        })

        trader_targets = [target for kind, _, target in bridge.tx.pushes if kind == "event" and target != bridge.callback_event_channel]
        channel = json.loads(next(payload for kind, payload, target in bridge.tx.pushes
                                  if kind == "event" and target == bridge.callback_event_channel))
        assert trader_targets == ["hgt-client"]
        assert channel["account_type"] == "HUGANGTONG"
        assert channel["data"]["m_nBrokerType"] == 7
    finally:
        account_routing.unsubscribe(bridge.bridge_id, client_id="stock-client")
        account_routing.unsubscribe(bridge.bridge_id, client_id="hgt-client")
        account_routing.unsubscribe(bridge.bridge_id, client_id="sgt-client")
        bridge.close()


def test_same_account_id_callback_without_type_is_not_broadcast_to_all_connect_clients():
    bridge = NormalQmtBridge(None, bridge_id="stock-connect-ambiguous", show=False,
                             schedule_timer=False, order_meta_enabled=False)
    bridge.tx = RecordingTx()
    account_id = "SAME-ACCOUNT"
    try:
        account_routing.subscribe(bridge.bridge_id, account_id, "stock-client", account_type="STOCK")
        account_routing.subscribe(bridge.bridge_id, account_id, "hgt-client", account_type="HUGANGTONG")
        bridge.account_type = "STOCK"

        bridge.publish_callback_event("trader:on_stock_trade", {
            "m_strAccountID": account_id,
            "m_strInstrumentID": "00700",
            "m_strExchangeID": "SH",
        })

        trader_targets = [target for kind, _, target in bridge.tx.pushes if kind == "event" and target != bridge.callback_event_channel]
        channel = json.loads(next(payload for kind, payload, target in bridge.tx.pushes
                                  if kind == "event" and target == bridge.callback_event_channel))
        assert trader_targets == []
        assert channel["account_type"] == ""
        assert channel["data"]["cfquant_account_type_unresolved"] is True
    finally:
        account_routing.unsubscribe(bridge.bridge_id, client_id="stock-client")
        account_routing.unsubscribe(bridge.bridge_id, client_id="hgt-client")
        bridge.close()


def test_same_account_id_routes_account_key_callback_when_broker_type_is_missing():
    bridge = NormalQmtBridge(None, bridge_id="stock-connect-account-key", show=False,
                             schedule_timer=False, order_meta_enabled=False)
    bridge.tx = RecordingTx()
    account_id = "SAME-ACCOUNT"
    try:
        account_routing.subscribe(bridge.bridge_id, account_id, "stock-client", account_type="STOCK")
        account_routing.subscribe(bridge.bridge_id, account_id, "hgt-client", account_type="HUGANGTONG")
        account_routing.subscribe(bridge.bridge_id, account_id, "sgt-client", account_type="SHENGANGTONG")

        bridge.publish_callback_event("trader:on_stock_trade", {
            "m_strAccountID": account_id,
            "m_strAccountKey": "11____1____1____1____SAME-ACCOUNT____",
            "m_strInstrumentID": "700",
            "m_strExchangeID": "SGT",
        })

        trader_targets = [target for kind, _, target in bridge.tx.pushes
                          if kind == "event" and target != bridge.callback_event_channel]
        channel = json.loads(next(payload for kind, payload, target in bridge.tx.pushes
                                  if kind == "event" and target == bridge.callback_event_channel))
        assert trader_targets == ["sgt-client"]
        assert channel["account_type"] == "SHENGANGTONG"
        assert channel["data"]["m_strAccountKey"].startswith("11____")
    finally:
        for client_id in ("stock-client", "hgt-client", "sgt-client"):
            account_routing.unsubscribe(bridge.bridge_id, client_id=client_id)
        bridge.close()


def test_same_account_id_callback_without_type_does_not_use_last_account_metadata():
    bridge = NormalQmtBridge(None, bridge_id="stock-connect-meta", show=False, schedule_timer=False)
    bridge.tx = RecordingTx()
    account_id = "SAME-ACCOUNT"
    try:
        account_routing.subscribe(bridge.bridge_id, account_id, "stock-client", account_type="STOCK")
        account_routing.subscribe(bridge.bridge_id, account_id, "hgt-client", account_type="HUGANGTONG")
        bridge.account_type = "HUGANGTONG"
        bridge.order_meta_cache.upsert({
            "bridge_id": bridge.bridge_id,
            "account_type": "HUGANGTONG",
            "account_id": account_id,
            "order_ref": "73",
            "strategy_name": "HGT-ONLY",
        })

        bridge.publish_callback_event("trader:on_stock_trade", {
            "m_strAccountID": account_id,
            "m_nRef": 73,
            "m_strInstrumentID": "00700",
            "m_strExchangeID": "SH",
        })

        channel = json.loads(next(payload for kind, payload, target in bridge.tx.pushes
                                  if kind == "event" and target == bridge.callback_event_channel))
        assert channel["data"]["strategy_name"] == ""
    finally:
        account_routing.unsubscribe(bridge.bridge_id, client_id="stock-client")
        account_routing.unsubscribe(bridge.bridge_id, client_id="hgt-client")
        bridge.close()


def test_sdk_drops_same_id_callback_for_different_stock_connect_type():
    received = []
    trader = XtQuantTrader(callback=XtQuantTraderCallback(), account=StockAccount("SAME-ACCOUNT", "HGT"))
    trader.callback.on_stock_trade = received.append

    trader._make_trader_handler("on_stock_trade")({
        "account_id": "SAME-ACCOUNT",
        "m_nBrokerType": xtconstant.SECURITY_ACCOUNT,
        "stock_code": "600000.SH",
    })

    assert received == []


def test_sdk_uses_account_key_before_legacy_callback_type():
    received = []
    trader = XtQuantTrader(callback=XtQuantTraderCallback(), account=StockAccount("SAME-ACCOUNT", "HGT"))
    trader.callback.on_stock_trade = received.append

    trader._make_trader_handler("on_stock_trade")({
        "account_id": "SAME-ACCOUNT",
        "account_type": "HUGANGTONG",
        "m_strAccountKey": "2____1____1____1____SAME-ACCOUNT____",
        "stock_code": "600000.SH",
    })

    assert received == []


def test_foreign_callback_without_identity_does_not_inherit_configured_account_type():
    bridge = NormalQmtBridge(None, bridge_id="stock-connect-foreign", account_id="CONFIGURED",
                             show=False, schedule_timer=False, order_meta_enabled=False)
    bridge.tx = RecordingTx()
    bridge.account_type = "HUGANGTONG"
    try:
        bridge.publish_callback_event("trader:on_stock_trade", {
            "m_strAccountID": "FOREIGN",
            "m_strInstrumentID": "700",
            "m_strExchangeID": "HK",
        })

        channel = json.loads(next(payload for kind, payload, target in bridge.tx.pushes
                                  if kind == "event" and target == bridge.callback_event_channel))
        assert channel["account_type"] == ""
        assert channel["data"]["cfquant_account_type_unresolved"] is True
    finally:
        bridge.close()


def test_same_id_order_terminal_status_is_isolated_by_stock_connect_type():
    bridge = NormalQmtBridge(None, bridge_id="stock-connect-terminal", show=False,
                             schedule_timer=False, order_meta_enabled=False)
    bridge.tx = RecordingTx()
    try:
        first = {
            "m_strAccountID": "SAME-ACCOUNT",
            "m_nBrokerType": xtconstant.HUGANGTONG_ACCOUNT,
            "m_strInstrumentID": "00700",
            "m_strExchangeID": "SH",
            "m_nOrderID": 19,
            "m_nOrderStatus": xtconstant.ORDER_SUCCEEDED,
        }
        second = dict(first, m_nBrokerType=xtconstant.SECURITY_ACCOUNT,
                      m_nOrderStatus=xtconstant.ORDER_PART_SUCC)

        bridge.publish_callback_event("trader:on_stock_order", first)
        bridge.publish_callback_event("trader:on_stock_order", second)

        assert sum(kind == "event" and target == bridge.callback_event_channel
                   for kind, _, target in bridge.tx.pushes) == 2
    finally:
        bridge.close()


def test_same_id_pending_order_error_does_not_match_another_stock_connect_type():
    bridge = NormalQmtBridge(None, bridge_id="stock-connect-errors", show=False,
                             schedule_timer=False, order_meta_enabled=False)
    try:
        bridge.pending_order_errors = [{"data": {
            "account_id": "SAME-ACCOUNT",
            "m_nBrokerType": xtconstant.HUGANGTONG_ACCOUNT,
            "stock_code": "00700.SH",
        }}]

        assert bridge._match_pending_order_error({
            "account_id": "SAME-ACCOUNT",
            "m_nBrokerType": xtconstant.SECURITY_ACCOUNT,
            "stock_code": "00700.SH",
        }) is None
    finally:
        bridge.close()


@pytest.mark.parametrize("row_type", [XtOrder, XtTrade])
def test_xttype_canonicalizes_stock_connect_raw_codes_and_offset_sides(row_type):
    row = row_type.from_any({
        "m_strAccountID": "SAME-ACCOUNT",
        "m_strAccountKey": "7____1____1____1____SAME-ACCOUNT____",
        "m_strInstrumentID": 700,
        "m_strExchangeID": "HGT",
        "m_nOffsetFlag": 48,
    })

    assert row.account_type == xtconstant.HUGANGTONG_ACCOUNT
    assert row.stock_code == "00700.HK"
    assert row.order_type == xtconstant.STOCK_BUY
