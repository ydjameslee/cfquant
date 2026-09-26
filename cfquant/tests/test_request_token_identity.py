"""Offline regression tests for QMT request-token order identity."""

import json

import pytest

from cfquant.normal_bridge import NormalQmtBridge
from cfquant.tx_trade_bridge import TxTradeBridge
from cfquant.batch_orders import execute_qmt_batch


def order_params(**overrides):
    params = {
        "account": {"account_id": "A1", "account_type": "STOCK"},
        "stock_code": "000001.SZ",
        "order_type": 23,
        "order_volume": 100,
        "price_type": 11,
        "price": 10.0,
        "strategy_name": "user-strategy",
        "order_remark": "user-remark",
        "seq": 17,
    }
    params.update(overrides)
    return params


def callback(token, **overrides):
    row = {
        "bridge_id": "bridge-a",
        "account_id": "A1",
        "account_type": "STOCK",
        "stock_code": "000001.SZ",
        "m_nRef": 701,
        "m_strTradingDay": "20260926",
        "m_strStrategyName": token,
        "strategy_name": token,
        "m_strRemark": "user-remark",
        "order_remark": "user-remark",
    }
    row.update(overrides)
    return row


def test_async_pending_is_registered_before_reentrant_passorder_callback():
    events = []
    native_calls = []
    last_id_calls = []
    bridge = None

    def passorder(*args):
        native_calls.append(args)
        assert len(bridge.pending_async_orders) == 1
        assert bridge._handle_async_order_callback(callback(args[7])) is True
        return 0

    bridge = TxTradeBridge(None, bridge_id="bridge-a", show=False, order_meta_enabled=False,
                           globals_dict={
                               "passorder": passorder,
                               "get_last_order_id": lambda *args: last_id_calls.append(args) or -1,
                           })
    bridge._send_trader_event = lambda client, name, data: events.append((client, name, data))

    result = bridge._order_stock_async(order_params(), {"id": "r1", "client_id": "c1"})

    token = native_calls[0][7]
    assert token != "user-strategy" and token.startswith("user-strategy&&&_cfq_")
    assert last_id_calls == [("A1", "stock", "order", token)]
    assert result == {"seq": 17, "accepted": True, "request_result": 0}
    assert events[0][2]["seq"] == 17
    assert bridge.pending_async_orders == []


@pytest.mark.parametrize("missing", ["token", "account_type", "trading_day", "m_nRef"])
def test_async_identity_missing_any_authoritative_component_fails_closed(missing):
    native = []
    events = []
    bridge = TxTradeBridge(None, bridge_id="bridge-a", show=False, order_meta_enabled=False,
                           globals_dict={"passorder": lambda *args: native.append(args) or 0})
    bridge._send_trader_event = lambda *args: events.append(args)
    bridge._order_stock_async(order_params(), {"id": "r1", "client_id": "c1"})
    row = callback(native[0][7])
    if missing == "token":
        row["strategy_name"] = row["m_strStrategyName"] = "user-strategy"
    elif missing == "trading_day":
        row.pop("m_strTradingDay")
    else:
        row.pop(missing)

    assert bridge._handle_async_order_callback(row) is False
    assert events == []
    assert len(bridge.pending_async_orders) == 1


def test_async_match_uses_exact_token_and_rejects_ambiguous_duplicate_identity():
    native = []
    bridge = TxTradeBridge(None, bridge_id="bridge-a", show=False, order_meta_enabled=False,
                           globals_dict={"passorder": lambda *args: native.append(args) or 0})
    bridge._send_trader_event = lambda *args: None
    bridge._order_stock_async(order_params(seq=1), {"id": "r1", "client_id": "c1"})
    token = native[0][7]
    duplicate = dict(bridge.pending_async_orders[0])
    duplicate["seq"] = 2
    bridge.pending_async_orders.append(duplicate)

    assert bridge._handle_async_order_callback(callback(token)) is False
    assert [item["seq"] for item in bridge.pending_async_orders] == [1, 2]


def test_registered_token_restores_query_fields_but_unknown_lookalike_is_unchanged():
    native = []
    rows = []
    bridge = TxTradeBridge(None, bridge_id="bridge-a", show=False, order_meta_enabled=False,
                           globals_dict={
                               "passorder": lambda *args: native.append(args) or 701,
                               "get_trade_detail_data": lambda *args: rows,
                           })
    bridge._order_stock(order_params(seq=None), {"id": "r1"}, resolve_order_id=False)
    token = native[0][7]
    rows[:] = [callback(token), callback("manual&&&_cfq_deadbeefdead", m_nRef=702)]

    result = bridge._query_trade_detail(
        {"account": {"account_id": "A1", "account_type": "STOCK"}}, "order"
    )

    assert result[0]["strategy_name"] == "user-strategy"
    assert result[0]["m_strStrategyName"] == "user-strategy"
    assert result[1]["strategy_name"] == "manual&&&_cfq_deadbeefdead"
    assert result[1]["m_strStrategyName"] == "manual&&&_cfq_deadbeefdead"


def test_error_restoration_does_not_consume_token_alias_for_later_order_callback():
    native = []
    bridge = NormalQmtBridge(None, bridge_id="bridge-a", show=False, schedule_timer=False,
                             order_meta_enabled=False,
                             globals_dict={"passorder": lambda *args: native.append(args) or 701})
    bridge._order_stock(order_params(seq=None), {"id": "r1"}, resolve_order_id=False)
    token = native[0][7]
    error = callback(token, error_msg="rejected")

    bridge._enrich_qmt_order_error_fields(error)
    later = callback(token)
    bridge._enrich_order_request_fields(later)

    assert error["strategy_name"] == "user-strategy"
    assert later["strategy_name"] == "user-strategy"
    assert token in {item["internal_strategy_name"] for item in bridge.order_error_contexts.values()}


def test_error_with_only_mstr_token_and_no_type_uses_unique_exact_alias():
    native = []
    bridge = NormalQmtBridge(None, bridge_id="bridge-a", show=False, schedule_timer=False,
                             order_meta_enabled=False,
                             globals_dict={"passorder": lambda *args: native.append(args) or -1})
    bridge._order_stock(order_params(seq=None), {"id": "r1"}, resolve_order_id=False)
    error = {"account_id": "A1", "m_strStrategyName": native[0][7], "error_msg": "rejected"}

    bridge._enrich_qmt_order_error_fields(error)

    assert error["account_type"] == "STOCK"
    assert error["strategy_name"] == error["m_strStrategyName"] == error["strategyName"] == "user-strategy"
    assert error["order_remark"] == error["m_strRemark"] == error["m_strOrderRemark"] == "user-remark"


def test_error_with_conflicting_explicit_types_does_not_restore_token():
    native = []
    bridge = NormalQmtBridge(None, bridge_id="bridge-a", show=False, schedule_timer=False,
                             order_meta_enabled=False,
                             globals_dict={"passorder": lambda *args: native.append(args) or 0})
    bridge._order_stock(order_params(seq=None), {"id": "r1"}, resolve_order_id=False)
    token = native[0][7]
    error = {"account_id": "A1", "account_type": "STOCK", "m_nBrokerType": 7,
             "m_strStrategyName": token, "error_msg": "rejected"}

    bridge._enrich_qmt_order_error_fields(error)

    assert error.get("strategy_name") != "user-strategy"
    assert error["m_strStrategyName"] == token


def test_normal_callback_matches_raw_token_before_public_strategy_restoration():
    native = []
    pushes = []
    bridge = NormalQmtBridge(None, bridge_id="bridge-a", show=False, schedule_timer=False,
                             order_meta_enabled=False,
                             globals_dict={"passorder": lambda *args: native.append(args) or 0})

    class Tx(object):
        def push(self, *args):
            pushes.append(args)

    bridge.tx = Tx()
    bridge._order_stock_async(order_params(), {"id": "r1", "client_id": "c1"})
    token = native[0][7]
    bridge.publish_callback_event("trader:on_stock_order", callback(token))

    payloads = [json.loads(item[1]) for item in pushes
                if item[0] == "event" and item[2] == bridge.callback_event_channel]
    order_payload = next(item for item in payloads if item["event"] == "trader:on_stock_order")
    assert order_payload["data"]["strategy_name"] == "user-strategy"
    assert order_payload["data"]["m_strStrategyName"] == "user-strategy"
    assert token not in json.dumps(order_payload, ensure_ascii=False)
    assert bridge.pending_async_orders == []


def test_reusing_same_params_creates_a_fresh_token_and_pending_record():
    native = []
    bridge = TxTradeBridge(None, bridge_id="bridge-a", show=False, order_meta_enabled=False,
                           globals_dict={"passorder": lambda *args: native.append(args) or 0})
    params = order_params(seq=31)
    bridge._order_stock_async(params, {"id": "r1", "client_id": "c1"})
    first = native[-1][7]
    assert bridge._handle_async_order_callback(callback(first, m_nRef=731)) is True

    params["seq"] = 32
    bridge._order_stock_async(params, {"id": "r2", "client_id": "c1"})
    second = native[-1][7]

    assert second != first
    assert [(item["seq"], item["request_token"]) for item in bridge.pending_async_orders] == [(32, second)]


def test_async_batch_registers_before_passorder_and_does_not_resurrect_completed_pending():
    native = []
    events = []
    bridge = None

    def passorder(*args):
        native.append(args)
        assert len(bridge.pending_async_orders) == 1
        assert bridge._handle_async_order_callback(callback(args[7], m_nRef=800 + len(native))) is True
        return 0

    bridge = TxTradeBridge(None, bridge_id="bridge-a", show=False, order_meta_enabled=False,
                           globals_dict={"passorder": passorder})
    bridge._send_trader_event = lambda client, name, data: events.append(data)
    params = {
        "account": {"account_id": "A1", "account_type": "STOCK"},
        "batch_id": "batch-1",
        "seqs": [41, 42],
        "orders": [
            {key: value for key, value in order_params(seq=None).items()
             if key not in ("account", "seq")},
            dict({key: value for key, value in order_params(seq=None).items()
                  if key not in ("account", "seq")}, stock_code="000002.SZ", order_remark="second"),
        ],
    }

    result = execute_qmt_batch(bridge, params, {"id": "batch-r", "client_id": "c1"}, True)

    assert result["submitted"] == 2
    assert [item["seq"] for item in events] == [41, 42]
    assert bridge.pending_async_orders == []


def test_sync_order_id_lookup_uses_same_exact_qmt_token_before_public_restoration():
    native = []
    query_calls = []

    def query(*args):
        query_calls.append(args)
        token = native[0][7]
        return [
            {"m_strAccountID": "A1", "m_nAccountType": 2, "m_nRef": 700,
             "m_strInstrumentID": "000001", "m_strRemark": "user-remark",
             "m_strStrategyName": "historical-user-strategy"},
            {"m_strAccountID": "A1", "m_nAccountType": 2, "m_nRef": 701,
             "m_strInstrumentID": "000001", "m_strRemark": "user-remark",
             "m_strStrategyName": token},
        ]

    bridge = TxTradeBridge(None, bridge_id="bridge-a", show=False, order_meta_enabled=False,
                           globals_dict={
                               "passorder": lambda *args: native.append(args) or 0,
                               "get_last_order_id": lambda *args: -1,
                               "get_trade_detail_data": query,
                           })

    result = bridge._order_stock(order_params(seq=None, find_order_wait=0), {"id": "r1"})

    assert query_calls[0][3] == native[0][7]
    assert result["order_id"] == 701
