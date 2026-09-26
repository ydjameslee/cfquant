"""No connections or trading: test reused internal IDs across QMT scopes."""
from types import SimpleNamespace
import pytest
from cfquant.normal_bridge import NormalQmtBridge
from cfquant.xttrader import XtQuantTrader


@pytest.mark.parametrize("layer", ["bridge", "sdk"])
@pytest.mark.parametrize("change", [
    {"trading_day": "20260928"}, {"account_type": "SHENGANGTONG"},
    {"account_id": "OTHER"}, {"bridge_id": "other"},
])
def test_reused_ref_is_not_suppressed_across_identity(layer, change):
    target = (NormalQmtBridge(None, show=False, schedule_timer=False, order_meta_enabled=False)
              if layer == "bridge" else XtQuantTrader())
    first = dict(bridge_id="one", account_id="TEST", account_type="HUGANGTONG",
                 trading_day="20260925", m_nRef=403701761, order_status=56)
    def accept(row):
        return (target._accept_order_callback(row) if layer == "bridge"
                else target._accept_order_update(SimpleNamespace(**row)))
    assert accept(first)
    assert accept(dict(first, order_status=55, **change))


@pytest.mark.parametrize("layer", ["bridge", "sdk"])
def test_unknown_trading_day_does_not_suppress_a_reused_ref(layer):
    target = (NormalQmtBridge(None, show=False, schedule_timer=False, order_meta_enabled=False)
              if layer == "bridge" else XtQuantTrader())
    first = dict(account_id="TEST", account_type="HUGANGTONG", m_nRef=19, order_status=56)
    def accept(row):
        return (target._accept_order_callback(row) if layer == "bridge"
                else target._accept_order_update(SimpleNamespace(**row)))
    assert accept(first)
    assert accept(dict(first, order_status=55))


@pytest.mark.parametrize("layer", ["bridge", "sdk"])
def test_same_trading_day_after_midnight_still_filters_stale_update(layer):
    target = (NormalQmtBridge(None, show=False, schedule_timer=False, order_meta_enabled=False)
              if layer == "bridge" else XtQuantTrader())
    first = dict(account_id="TEST", account_type="HUGANGTONG", m_nRef=19,
                 trading_day="20260928", order_date="20260925", order_status=56)
    def accept(row):
        return (target._accept_order_callback(row) if layer == "bridge"
                else target._accept_order_update(SimpleNamespace(**row)))
    assert accept(first)
    assert not accept(dict(first, order_date="20260926", order_status=55))


def test_pending_errors_do_not_guess_between_same_symbol_orders():
    bridge = NormalQmtBridge(None, show=False, schedule_timer=False, order_meta_enabled=False)
    error = dict(account_id="TEST", account_type="HUGANGTONG", stock_code="02828.HK")
    bridge._queue_pending_order_error(dict(error, error_msg="first"))
    bridge._queue_pending_order_error(dict(error, error_msg="second"))
    assert bridge._match_pending_order_error(error) is None
    assert len(bridge.pending_order_errors) == 2


def test_pending_error_without_date_or_ref_is_not_bound_to_new_order():
    bridge = NormalQmtBridge(None, show=False, schedule_timer=False, order_meta_enabled=False)
    row = dict(account_id="TEST", account_type="HUGANGTONG", stock_code="02828.HK")
    bridge._queue_pending_order_error(row)
    assert bridge._match_pending_order_error(dict(row, trading_day="20260928", m_nRef=77)) is None
    assert len(bridge.pending_order_errors) == 1


@pytest.mark.parametrize("layer", ["bridge", "sdk"])
def test_shared_counter_id_does_not_override_distinct_internal_ref(layer):
    target = (NormalQmtBridge(None, show=False, schedule_timer=False, order_meta_enabled=False)
              if layer == "bridge" else XtQuantTrader())
    first = dict(account_id="TEST", account_type="HUGANGTONG", trading_day="20260928",
                 order_sysid="SYS", m_nRef=1, order_status=56)
    def accept(row):
        return (target._accept_order_callback(row) if layer == "bridge"
                else target._accept_order_update(SimpleNamespace(**row)))
    assert accept(first)
    assert accept(dict(first, m_nRef=2, order_status=55))


def test_sdk_query_ref_requires_unique_day(monkeypatch):
    trader = XtQuantTrader()
    rows = [SimpleNamespace(order_id=123, trading_day=day) for day in ("20260925", "20260928")]
    monkeypatch.setattr(trader, "query_stock_orders", lambda account: rows)
    with pytest.raises(ValueError, match="ambiguous"):
        trader.query_stock_order(None, 123)
    assert trader.query_stock_order(None, 123, trading_day="20260928") is rows[1]


def test_local_clock_does_not_clear_next_trading_day_metadata(monkeypatch):
    import datetime
    import cfquant.normal_bridge as module
    class Evening(datetime.datetime):
        @classmethod
        def now(cls):
            return cls(2026, 9, 25, 23, 59)
    monkeypatch.setattr(module, "dt", SimpleNamespace(datetime=Evening))
    bridge = NormalQmtBridge(None, show=False, schedule_timer=False, order_meta_enabled=True)
    bridge.order_meta_accounts = {("HUGANGTONG", "TEST")}
    resets = []
    monkeypatch.setattr(bridge, "_reset_order_meta_store", lambda *a, **kw: resets.append(a))
    bridge._maybe_reset_order_meta_stores()
    assert resets == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_sdk_forwards_internal_ref_scope_without_changing_native_signature(monkeypatch, asynchronous):
    from cfquant.xttype import StockAccount
    trader = XtQuantTrader()
    calls = []
    monkeypatch.setattr(trader, "_trade_request", lambda action, params: calls.append((action, params)) or {"cancel_result": 0})
    method = trader.cancel_order_stock_async if asynchronous else trader.cancel_order_stock
    method(StockAccount("TEST", "HUGANGTONG"), 403701761,
           trading_day="20260928", order_id_kind="internal")
    assert calls[0][1]["trading_day"] == "20260928"
    assert calls[0][1]["order_id_kind"] == "internal"
    assert calls[0][1]["account"]["account_id"] == "TEST"
