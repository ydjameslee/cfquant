"""Web Stock Connect validation uses only mocked transports."""

import pytest

import cfquant_web_server as web


@pytest.fixture
def transport(monkeypatch):
    calls = []

    monkeypatch.setattr(web, "resolve_bridge_id", lambda **kwargs: "connect_bridge")
    monkeypatch.setattr(web, "bridge_config", lambda _bridge_id: {})
    monkeypatch.setattr(
        web,
        "account_request",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {
            "bridge_id": "connect_bridge", "channel": "trade", "mode": "ctypes",
            "fallback": False, "fallback_reason": "", "market_route": {},
            "result": {"order_id": 1001},
        },
    )
    monkeypatch.setattr(
        web,
        "account_batch_order_request",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {
            "bridge_id": "connect_bridge", "channel": "trade", "mode": "ctypes",
            "fallback": False, "fallback_reason": "", "market_route": {},
            "result": {"submitted": 1},
        },
    )
    return calls


def connect_order(account_type="HUGANGTONG", stock_code="00700.HK", **overrides):
    body = {
        "account_id": "CONNECT_ONLY",
        "account_type": account_type,
        "stock_code": stock_code,
        "side": "buy",
        "price_type": web.FIX_PRICE,
        "price": 320.5,
        "volume": 100,
        "confirm_text": "BUY 00700.HK 100 @ 320.500",
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (7, "HUGANGTONG"),
        ("HUGANGTONG_ACCOUNT", "HUGANGTONG"),
        (11, "SHENGANGTONG"),
        ("SHENGANGTONG_ACCOUNT", "SHENGANGTONG"),
    ],
)
def test_stock_connect_account_type_aliases(value, expected):
    assert web.normalize_account_type(value) == expected


@pytest.mark.parametrize(
    ("value", "account_type", "expected"),
    [
        ("00700.HK", "HUGANGTONG", "00700.HK"),
        ("HK.00700", "HUGANGTONG", "00700.HK"),
        ("00700", "SHENGANGTONG", "00700.HK"),
        ("000001", "STOCK", "000001.SZ"),
    ],
)
def test_normalize_stock_code_preserves_hong_kong_leading_zeroes(value, account_type, expected):
    assert web.normalize_stock_code(value, account_type) == expected


def test_stock_connect_order_routes_hk_code_and_typed_account(transport):
    result = web.submit_order(connect_order(stock_code="HK.00700"))

    args, kwargs = transport[0]
    assert args[3] == "xttrader.order_stock"
    assert args[4]["stock_code"] == "00700.HK"
    assert args[4]["account"] == {"account_id": "CONNECT_ONLY", "account_type": "HUGANGTONG"}
    assert kwargs["account_type"] == "HUGANGTONG"
    assert result["account_type"] == "HUGANGTONG"


def test_stock_connect_batch_routes_hk_codes_and_typed_account(transport):
    result = web.submit_batch_orders({
        "account_id": "CONNECT_ONLY",
        "account_type": "11",
        "confirm_text": "BATCH 1",
        "orders": [{"stock_code": "00700", "side": "sell", "price": 320.5, "volume": 100}],
    })

    args, kwargs = transport[0]
    assert args[3]["account"] == {"account_id": "CONNECT_ONLY", "account_type": "SHENGANGTONG"}
    assert args[3]["orders"][0]["stock_code"] == "00700.HK"
    assert kwargs["account_type"] == "SHENGANGTONG"
    assert result["account_type"] == "SHENGANGTONG"


@pytest.mark.parametrize(
    "body",
    [
        connect_order(stock_code="600000.SH", confirm_text="BUY 600000.SH 100 @ 320.500"),
        connect_order(price_type=5),
    ],
)
def test_stock_connect_order_rejects_wrong_market_or_non_limit_price(body, transport):
    with pytest.raises(ValueError):
        web.submit_order(body)
    assert transport == []


def test_stock_order_rejects_hong_kong_market(transport):
    body = connect_order(account_type="STOCK")
    with pytest.raises(ValueError):
        web.submit_order(body)
    assert transport == []


@pytest.mark.parametrize(
    "order",
    [
        {"stock_code": "600000.SH", "side": "buy", "price": 10, "volume": 100},
        {"stock_code": "00700.HK", "side": "buy", "price_type": 5, "price": 10, "volume": 100},
    ],
)
def test_stock_connect_batch_rejects_wrong_market_or_non_limit_price(order, transport):
    with pytest.raises(ValueError):
        web.submit_batch_orders({
            "account_id": "CONNECT_ONLY", "account_type": "HUGANGTONG",
            "confirm_text": "BATCH 1", "orders": [order],
        })
    assert transport == []


def test_stock_connect_cancel_routes_typed_account(transport):
    result = web.cancel_order({
        "account_id": "CONNECT_ONLY", "account_type": "7", "order_id": "1001", "confirm_text": "CANCEL 1001",
    })

    args, kwargs = transport[0]
    assert args[3] == "xttrader.cancel_order_stock"
    assert args[4]["account"] == {"account_id": "CONNECT_ONLY", "account_type": "HUGANGTONG"}
    assert kwargs["account_type"] == "HUGANGTONG"
    assert result["account_type"] == "HUGANGTONG"


def test_same_account_id_stock_connect_types_keep_isolated_bindings(tmp_path):
    config = web.WebRuntimeConfig(str(tmp_path / "web.json"), str(tmp_path / "settings.db"))
    rows = [
        config.save_account_config(
            "SAME_ACCOUNT", account_type=account_type, qmt_dir=str(tmp_path), enabled=True,
            qmt_strategy={"enabled": True},
        )
        for account_type in ("STOCK", "HUGANGTONG", "SHENGANGTONG")
    ]

    assert len({row["account_key"] for row in rows}) == 3
    assert len({row["bridge_id"] for row in rows}) == 3
    assert all(row["enabled"] for row in config.account_configs().values())
    assert set(row["account_type"] for row in config.account_pairs().values()) == {
        "STOCK", "HUGANGTONG", "SHENGANGTONG",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"volume": 100.5},
        {"volume": "100.5"},
        {"price": float("nan")},
        {"price": float("inf")},
        {"price": -1},
    ],
)
def test_stock_connect_order_rejects_non_finite_price_and_non_integer_volume(changes, transport):
    with pytest.raises((TypeError, ValueError)):
        web.submit_order(connect_order(**changes))
    assert transport == []
