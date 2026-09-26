"""Offline checks for authoritative QMT order identity scoping."""

import pytest

from cfquant.tx_trade_bridge import TxTradeBridge
from cfquant.qmt_bridge import CfquantQmtBridge


@pytest.mark.parametrize("bridge_class", [TxTradeBridge, CfquantQmtBridge])
def test_unverified_cancel_never_falls_through_to_native(bridge_class):
    calls = []
    bridge = bridge_class(None, show=False, globals_dict={
        "cancel": lambda *args: calls.append(args) or True,
        "get_trade_detail_data": lambda *args: [],
    })
    try:
        with pytest.raises(ValueError):
            bridge._cancel_order_stock({"account": {"account_id": "A1", "account_type": "STOCK"},
                                        "order_id": 42})
        assert calls == []
    finally:
        bridge.close()


@pytest.mark.parametrize("bridge_class", [TxTradeBridge, CfquantQmtBridge])
def test_auto_cancel_rejects_cross_namespace_collision_but_sysid_is_explicit(bridge_class):
    calls = []
    rows = [
        {"m_strAccountID": "A1", "m_nBrokerType": 2, "m_nRef": 1,
         "m_strOrderSysID": "42", "m_strTradingDay": "20260928"},
        {"m_strAccountID": "A1", "m_nBrokerType": 2, "m_nRef": 42,
         "m_strOrderSysID": "OTHER", "m_strTradingDay": "20260928"},
    ]
    bridge = bridge_class(None, show=False, globals_dict={
        "cancel": lambda *args: calls.append(args) or True,
        "get_trade_detail_data": lambda *args: rows,
    })
    params = {"account": {"account_id": "A1", "account_type": "STOCK"},
              "order_id": "42", "trading_day": "20260928"}
    try:
        with pytest.raises(ValueError):
            bridge._cancel_order_stock(params)
        assert calls == []
        bridge._cancel_order_stock(dict(params, order_id_kind="sysid"))
        assert len(calls) == 1 and calls[0][0] == "42"
    finally:
        bridge.close()


def _bridge():
    bridge = TxTradeBridge(None, bridge_id="date-scope", show=False)
    bridge._send_trader_event = lambda *args: None
    return bridge


def test_order_detail_keeps_qmt_trading_day_separate_from_order_date_and_ids():
    bridge = _bridge()
    try:
        row = bridge._format_trade_detail({
            "m_strAccountID": "A1",
            "m_strInstrumentID": "000001",
            "m_strExchangeID": "SZ",
            "m_nRef": 901,
            "m_strOrderID": "FULL-901",
            "m_strOrderSysID": "SYS-901",
            "m_strTradingDay": "20260925",
            "m_strOrderDate": "20260924",
        }, "order")

        assert row["trading_day"] == "20260925"
        assert row["order_date"] == "20260924"
        assert row["order_id"] == 901
        assert row["m_nRef"] == 901
        assert row["m_strOrderID"] == "FULL-901"
        assert row["order_sysid"] == "SYS-901"
    finally:
        bridge.close()


def test_async_callback_only_consumes_pending_order_in_matching_type_and_trading_day():
    bridge = _bridge()
    try:
        stock_token = bridge._register_order_error_context("A1", "STOCK", "000001.SZ", "", "same")
        credit_token = bridge._register_order_error_context("A1", "CREDIT", "000001.SZ", "", "same")
        bridge.pending_async_orders[:] = [
            {"seq": 1, "client_id": "client", "account_id": "A1", "account_type": "STOCK",
             "stock_code": "000001.SZ", "order_remark": "same", "strategy_name": "",
             "bridge_id": bridge.bridge_id, "request_token": stock_token,
             "trading_day": "20260924", "created_at": 9999999999},
            {"seq": 2, "client_id": "client", "account_id": "A1", "account_type": "CREDIT",
             "stock_code": "000001.SZ", "order_remark": "same", "strategy_name": "",
             "bridge_id": bridge.bridge_id, "request_token": credit_token,
             "trading_day": "20260925", "created_at": 9999999999},
        ]

        for wrong in ({"account_type": "STOCK"}, {"m_strTradingDay": "20260924"}):
            callback = {"account_id": "A1", "account_type": "CREDIT", "stock_code": "000001.SZ",
                        "m_nRef": 902, "m_strStrategyName": credit_token, "m_strTradingDay": "20260925"}
            callback.update(wrong)
            assert bridge._handle_async_order_callback(callback) is False
        assert len(bridge.pending_async_orders) == 2
        assert bridge._handle_async_order_callback({
            "account_id": "A1", "account_type": "CREDIT", "stock_code": "000001.SZ",
            "m_nRef": 902, "m_strStrategyName": credit_token,
            "order_id": 902, "order_remark": "same", "m_strTradingDay": "20260925",
        }) is True
        assert [record["seq"] for record in bridge.pending_async_orders] == [1]
    finally:
        bridge.close()


def test_request_metadata_does_not_cross_account_type_or_trading_day():
    bridge = _bridge()
    try:
        bridge._remember_order_request(
            "A1", "000001.SZ", "same", "stock-strategy", order_id=901,
            account_type="STOCK", trading_day="20260924",
        )
        bridge._remember_order_request(
            "A1", "000001.SZ", "same", "credit-strategy", order_id=902,
            account_type="CREDIT", trading_day="20260925",
        )
        callback = {
            "account_id": "A1", "account_type": "CREDIT", "stock_code": "000001.SZ",
            "order_id": 902, "m_nRef": 902, "order_remark": "same", "m_strTradingDay": "20260925",
        }

        bridge._enrich_order_request_fields(callback)

        assert callback["strategy_name"] == "credit-strategy"
        assert callback["order_remark"] == "same"
    finally:
        bridge.close()


def test_internal_ref_cancel_requires_one_authoritative_date_scoped_full_id():
    native_calls = []
    bridge = TxTradeBridge(None, bridge_id="date-scope", show=False, globals_dict={
        "cancel": lambda *args: native_calls.append(args) or True,
        "get_trade_detail_data": lambda *args: [
            {"m_strAccountID": "A1", "m_nBrokerType": 2, "m_nRef": 901,
             "m_strOrderID": "FULL-901-OLD", "m_strOrderSysID": "SYS-901-OLD", "m_strTradingDay": "20260924"},
            {"m_strAccountID": "A1", "m_nBrokerType": 2, "m_nRef": 901,
             "m_strOrderID": "FULL-901-TODAY", "m_strOrderSysID": "SYS-901-TODAY", "m_strTradingDay": "20260925"},
        ],
    })
    try:
        result = bridge._cancel_order_stock({
            "account": {"account_id": "A1", "account_type": "STOCK"},
            "internal_ref": 901,
            "trading_day": "20260925",
        })

        assert result["order_id"] == "SYS-901-TODAY"
        assert result["internal_ref"] == 901
        assert native_calls == [("SYS-901-TODAY", "A1", "STOCK", None)]

        with pytest.raises(ValueError, match="requires"):
            bridge._cancel_order_stock({
                "account": {"account_id": "A1", "account_type": "STOCK"},
                "internal_ref": 901,
            })
    finally:
        bridge.close()


def test_dated_native_cancel_rejects_an_order_id_found_on_another_qmt_day():
    native_calls = []
    bridge = TxTradeBridge(None, bridge_id="date-scope", show=False, globals_dict={
        "cancel": lambda *args: native_calls.append(args) or True,
        "get_trade_detail_data": lambda *args: [
            {"m_strAccountID": "A1", "m_nBrokerType": 2, "m_nRef": 901,
              "m_strOrderID": "FULL-901", "m_strOrderSysID": "SYS-901", "m_strTradingDay": "20260924"},
        ],
    })
    try:
        with pytest.raises(ValueError, match="trading_day"):
            bridge._cancel_order_stock({
                "account": {"account_id": "A1", "account_type": "STOCK"},
                "order_id": "SYS-901",
                "trading_day": "20260925",
            })
        assert native_calls == []
    finally:
        bridge.close()


@pytest.mark.parametrize("bridge_class,detail_type", [
    (TxTradeBridge, "order"),
    (CfquantQmtBridge, "ORDER"),
])
def test_both_bridges_preserve_distinct_authoritative_order_dates(bridge_class, detail_type):
    bridge = bridge_class(None, show=False)
    try:
        row = bridge._format_trade_detail({
            "m_strAccountID": "A1", "m_strInstrumentID": "000001", "m_strExchangeID": "SZ",
            "m_nRef": 901, "m_strOrderID": "FULL-901", "m_strTradingDay": "20260925",
            "m_strOrderDate": "20260924",
        }, detail_type)
        assert row["trading_day"] == "20260925"
        assert row["order_date"] == "20260924"
        assert row["m_nRef"] == 901
        assert row["m_strOrderID"] == "FULL-901"
    finally:
        bridge.close()


@pytest.mark.parametrize("bridge_class", [TxTradeBridge, CfquantQmtBridge])
def test_both_bridges_reject_dated_native_cancel_without_order_query(bridge_class):
    native_calls = []
    bridge = bridge_class(None, show=False, globals_dict={
        "cancel": lambda *args: native_calls.append(args) or True,
    })
    try:
        with pytest.raises(ValueError, match="requires"):
            bridge._cancel_order_stock({
                "account": {"account_id": "A1", "account_type": "STOCK"},
                "order_id": "SYS-901", "trading_day": "20260925",
            })
        assert native_calls == []
    finally:
        bridge.close()


@pytest.mark.parametrize("bridge_class", [TxTradeBridge, CfquantQmtBridge])
def test_both_bridges_reject_undated_native_cancel_with_multiple_qmt_candidates(bridge_class):
    native_calls = []
    bridge = bridge_class(None, show=False, globals_dict={
        "cancel": lambda *args: native_calls.append(args) or True,
        "get_trade_detail_data": lambda *args: [
            {"m_strAccountID": "A1", "m_nBrokerType": 2, "m_nRef": 901,
              "m_strOrderID": "FULL-901", "m_strOrderSysID": "SYS-901", "m_strTradingDay": "20260924"},
            {"m_strAccountID": "A1", "m_nBrokerType": 2, "m_nRef": 902,
              "m_strOrderID": "FULL-901", "m_strOrderSysID": "SYS-901", "m_strTradingDay": "20260925"},
        ],
    })
    try:
        with pytest.raises(ValueError, match="ambiguous"):
            bridge._cancel_order_stock({
                "account": {"account_id": "A1", "account_type": "STOCK"},
                "order_id": "SYS-901",
            })
        assert native_calls == []
    finally:
        bridge.close()


@pytest.mark.parametrize("bridge_class", [TxTradeBridge, CfquantQmtBridge])
def test_both_bridges_do_not_fabricate_a_full_cancel_id_from_internal_ref(bridge_class):
    native_calls = []
    bridge = bridge_class(None, show=False, globals_dict={
        "cancel": lambda *args: native_calls.append(args) or True,
        "get_trade_detail_data": lambda *args: [
            {"m_strAccountID": "A1", "m_nBrokerType": 2, "m_nRef": 901,
             "m_strTradingDay": "20260925"},
        ],
    })
    try:
        with pytest.raises(ValueError, match="exactly one"):
            bridge._cancel_order_stock({
                "account": {"account_id": "A1", "account_type": "STOCK"},
                "internal_ref": 901, "trading_day": "20260925",
            })
        assert native_calls == []
    finally:
        bridge.close()


@pytest.mark.parametrize("bridge_class", [TxTradeBridge, CfquantQmtBridge])
def test_both_bridges_require_trading_day_before_plain_internal_order_id_is_mapped(bridge_class):
    native_calls = []
    bridge = bridge_class(None, show=False, globals_dict={
        "cancel": lambda *args: native_calls.append(args) or True,
        "get_trade_detail_data": lambda *args: [
            {"m_strAccountID": "A1", "m_nBrokerType": 2, "m_nRef": 901,
             "m_strOrderID": "LOCAL-901", "m_strOrderSysID": "SYS-901",
             "m_strTradingDay": "20260925"},
        ],
    })
    try:
        with pytest.raises(ValueError, match="trading_day"):
            bridge._cancel_order_stock({
                "account": {"account_id": "A1", "account_type": "STOCK"},
                "order_id": 901,
            })
        assert native_calls == []
    finally:
        bridge.close()


@pytest.mark.parametrize("bridge_class", [TxTradeBridge, CfquantQmtBridge])
def test_both_bridges_reject_invalid_or_conflicting_requested_trading_day(bridge_class):
    native_calls = []
    bridge = bridge_class(None, show=False, globals_dict={
        "cancel": lambda *args: native_calls.append(args) or True,
        "get_trade_detail_data": lambda *args: [],
    })
    try:
        for dates in (
            {"trading_day": "2026-99-99"},
            {"trading_day": "20260925", "m_strTradingDay": "20260924"},
        ):
            with pytest.raises(ValueError, match="trading_day"):
                bridge._cancel_order_stock({
                    "account": {"account_id": "A1", "account_type": "STOCK"},
                    "order_id": "SYS-901", **dates,
                })
        assert native_calls == []
    finally:
        bridge.close()
