"""Independent request correlation checks; native calls are local fakes."""
import pytest

from cfquant.tx_trade_bridge import TxTradeBridge
from cfquant.normal_bridge import NormalQmtBridge


@pytest.fixture
def fixture():
    native, replies = [], []
    bridge = TxTradeBridge(None, bridge_id="test", show=False, order_meta_enabled=False,
                          globals_dict={"passorder": lambda *args: native.append(args) or 0})
    bridge._send_trader_event = lambda client, name, data: replies.append(data)
    yield bridge, native, replies
    bridge.close()


def submit(fixture, seq, strategy="same", **changes):
    bridge, native, replies = fixture
    params = dict(account=dict(account_id="A", account_type="STOCK"),
                  stock_code="000001.SZ", order_type=23, order_volume=100,
                  price_type=11, price=10, strategy_name=strategy,
                  order_remark="same", seq=seq)
    params.update(changes)
    bridge._order_stock_async(params, dict(id=str(seq), client_id="client"))
    return native[-1][7]


def row(token, ref=41, **changes):
    result = dict(bridge_id="test", account_id="A", account_type="STOCK",
                  stock_code="000001.SZ", m_strStrategyName=token,
                  strategy_name=token, m_nRef=ref, m_strTradingDay="20260928")
    result.update(changes)
    return result


@pytest.mark.parametrize("changes", [
    dict(bridge_id="another"), dict(account_id="B"), dict(account_type="CREDIT"),
    dict(m_strAccountID="B"), dict(m_nBrokerType=7),
    dict(trading_day="20260925"), dict(m_strTradingDay="bad-date"),
])
def test_conflicting_or_wrong_identity_does_not_consume_pending(fixture, changes):
    bridge, native, replies = fixture
    token = submit(fixture, 1, trading_day="20260928")
    assert bridge._handle_async_order_callback(row(token, **changes)) is False
    assert len(bridge.pending_async_orders) == 1
    assert replies == []


def test_identical_user_labels_match_echoed_tokens_in_reverse_order(fixture):
    bridge, native, replies = fixture
    token1, token2 = submit(fixture, 1), submit(fixture, 2)
    assert token1 != token2
    assert bridge._handle_async_order_callback(row(token2, ref=42)) is True
    assert bridge._handle_async_order_callback(row(token1, ref=41)) is True
    assert [(reply["seq"], reply["order_id"]) for reply in replies] == [(2, 42), (1, 41)]
    assert bridge.pending_async_orders == []
    assert bridge._handle_async_order_callback(row(token1, ref=41)) is False
    assert len(replies) == 2


def test_original_user_strategy_with_token_like_suffix_is_restored_verbatim(fixture):
    bridge, native, replies = fixture
    original = "manual&&&_cfq_deadbeefdead"
    token = submit(fixture, 1, strategy=original)
    public = row(token)
    bridge._enrich_order_request_fields(public)
    assert public["strategy_name"] == original
    assert public["m_strStrategyName"] == original


def test_async_callback_restores_every_present_strategy_alias(fixture):
    bridge, native, replies = fixture
    token = submit(fixture, 1)
    public = row(token, strategyName=token)
    assert bridge._handle_async_order_callback(public) is True
    bridge._enrich_order_request_fields(public)
    assert public["strategy_name"] == public["m_strStrategyName"] == public["strategyName"] == "same"


@pytest.mark.parametrize("field", ["strategyName", "m_strStrategyName"])
def test_delayed_error_after_native_rejection_keeps_exact_alias_without_type(field):
    native = []
    bridge = NormalQmtBridge(None, bridge_id="test", show=False, schedule_timer=False,
                             order_meta_enabled=False,
                             globals_dict={"passorder": lambda *args: native.append(args) or -1})
    try:
        submit((bridge, native, []), 1)
        token = native[0][7]
        error = dict(accountID="A", orderCode="000001.SZ", error_msg="rejected")
        error[field] = token
        bridge._enrich_qmt_order_error_fields(error)
        assert error["strategy_name"] == error["m_strStrategyName"] == error["strategyName"] == "same"
        assert error["order_remark"] == error["m_strRemark"] == error["m_strOrderRemark"] == "same"
        assert bridge.pending_async_orders == []
        later = row(token)
        bridge._enrich_order_request_fields(later)
        assert later["strategy_name"] == "same"
    finally:
        bridge.close()


def test_alias_restoration_survives_error_context_original_five_minute_window(fixture):
    bridge, native, replies = fixture
    token = submit(fixture, 1)
    for record in bridge.order_error_contexts.values():
        record["created_at"] -= 3600
    submit(fixture, 2)
    public = row(token)
    bridge._enrich_order_request_fields(public)
    assert public["strategy_name"] == "same"


@pytest.mark.parametrize("reference", [None, 99])
def test_legacy_remark_metadata_cannot_invent_or_replace_order_identity(fixture, reference):
    bridge, native, replies = fixture
    bridge._remember_order_request("A", "000001.SZ", "same", "older-request",
                                   order_id=41, account_type="STOCK", trading_day="20260928")
    public = dict(account_id="A", account_type="STOCK", stock_code="000001.SZ",
                  order_remark="same", trading_day="20260928")
    if reference is not None:
        public.update(m_nRef=reference, order_id=reference)
    bridge._enrich_order_request_fields(public)
    assert public.get("m_nRef") == reference
    assert public.get("order_id") == reference
    assert public.get("strategy_name", "") == ""


@pytest.mark.parametrize("conflict", [dict(bridge_id="other"), dict(m_strAccountID="B"), dict(m_nBrokerType=7)])
def test_legacy_metadata_rejects_conflicting_scope_even_with_matching_reference(fixture, conflict):
    bridge, native, replies = fixture
    bridge._remember_order_request("A", "000001.SZ", "same", "older-request",
                                   order_id=41, account_type="STOCK", trading_day="20260928")
    public = dict(account_id="A", account_type="STOCK", stock_code="000001.SZ",
                  order_remark="same", trading_day="20260928", m_nRef=41)
    public.update(conflict)
    bridge._enrich_order_request_fields(public)
    assert public.get("strategy_name", "") == ""
    assert "order_id" not in public
