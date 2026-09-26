"""SDK synthetic async responses require an authoritative QMT order identity."""
from types import SimpleNamespace

import pytest

from cfquant.xttrader import XtQuantTrader


def pending_request(seq, account_type, trading_day, order_ref, bridge_id="default"):
    return {
        "seq": seq,
        "account": {"account_id": "A1", "account_type": account_type, "bridge_id": bridge_id},
        "stock_code": "02828.HK",
        "strategy_name": "strategy",
        "order_remark": "same-remark",
        "trading_day": trading_day,
        "m_nRef": order_ref,
    }


def order_callback(account_type="SHENGANGTONG", trading_day="20260928", order_ref=502):
    return SimpleNamespace(
        account_id="A1",
        account_type=account_type,
        stock_code="02828.HK",
        order_remark="same-remark",
        trading_day=trading_day,
        m_nRef=order_ref,
        order_id=9002,
    )


def test_synthetic_async_response_consumes_only_exact_qmt_identity():
    """Removing the scope checks would consume seq 1 solely because it is first."""
    trader = XtQuantTrader()
    trader._register_pending_async_order(pending_request(1, "HUGANGTONG", "20260925", 501))
    trader._register_pending_async_order(pending_request(2, "SHENGANGTONG", "20260928", 502))

    response = trader._async_order_response_from_order(order_callback())

    assert response.seq == 2
    assert response.order_id == 9002
    assert [item["seq"] for item in trader._pending_async_orders] == [1]


@pytest.mark.parametrize("callback", [
    order_callback(trading_day=""),
    order_callback(order_ref=0),
])
def test_synthetic_async_response_requires_complete_qmt_identity(callback):
    """A native order callback missing QMT day or ref must not complete a seq."""
    trader = XtQuantTrader()
    trader._register_pending_async_order(pending_request(2, "SHENGANGTONG", "20260928", 502))

    assert trader._async_order_response_from_order(callback) is None
    assert [item["seq"] for item in trader._pending_async_orders] == [2]


def test_synthetic_async_response_keeps_ambiguous_identity_pending():
    """Duplicate full identities must await QMT's explicit seq response."""
    trader = XtQuantTrader()
    trader._register_pending_async_order(pending_request(2, "SHENGANGTONG", "20260928", 502))
    trader._register_pending_async_order(pending_request(3, "SHENGANGTONG", "20260928", 502))

    assert trader._async_order_response_from_order(order_callback()) is None
    assert [item["seq"] for item in trader._pending_async_orders] == [2, 3]


def test_synthetic_async_response_uses_the_callback_source_bridge():
    """A callback client for bridge-b cannot complete bridge-a's identical request."""
    responses = []
    callback = SimpleNamespace(
        on_stock_order=lambda order: None,
        on_order_stock_async_response=responses.append,
    )
    trader = XtQuantTrader(callback=callback)
    trader._register_pending_async_order(pending_request(2, "SHENGANGTONG", "20260928", 502, "bridge-a"))
    trader._register_pending_async_order(pending_request(3, "SHENGANGTONG", "20260928", 502, "bridge-b"))

    trader._make_trader_handler("on_stock_order", bridge_id="bridge-b")(vars(order_callback()))

    assert [response.seq for response in responses] == [3]
    assert [item["seq"] for item in trader._pending_async_orders] == [2]


def test_explicit_async_response_still_completes_pending_seq():
    """The real QMT async response remains the fallback for incomplete order callbacks."""
    trader = XtQuantTrader()
    trader._register_pending_async_order(pending_request(2, "SHENGANGTONG", "20260928", 502))

    assert trader._accept_async_order_response(SimpleNamespace(seq=2)) is True
    assert trader._pending_async_orders == []
