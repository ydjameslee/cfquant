"""Stock Connect requests ride on the same account's STOCK binding; mocked transports only."""

import threading

import pytest

import cfquant_web_server as web
from cfquant.tx_trade_bridge import TxTradeBridge


def save(config, tmp_path, account_type, enabled=True, account_id="SAME_ACCOUNT"):
    return config.save_account_config(
        account_id, account_type=account_type, qmt_dir=str(tmp_path), enabled=enabled,
        qmt_strategy={"enabled": True}, qmt_auto_login={"enabled": False},
    )


@pytest.fixture
def config(tmp_path, monkeypatch):
    value = web.WebRuntimeConfig(str(tmp_path / "web.json"), str(tmp_path / "settings.db"))
    monkeypatch.setattr(web, "WEB_CONFIG", value)
    return value


@pytest.mark.parametrize("account_type", ["HUGANGTONG", "SHENGANGTONG", 7, 11])
def test_connect_type_without_its_own_binding_uses_the_stock_binding(config, tmp_path, account_type):
    stock = save(config, tmp_path, "STOCK")
    assert web.stock_connect_host_account_key("SAME_ACCOUNT", account_type) == stock["account_key"]
    assert web.stock_connect_host_account_key(
        "SAME_ACCOUNT", account_type, bridge_id=stock["bridge_id"]) == stock["account_key"]


def test_disabled_connect_binding_also_uses_the_stock_binding(config, tmp_path):
    stock = save(config, tmp_path, "STOCK")
    save(config, tmp_path, "HUGANGTONG", enabled=False)
    assert web.stock_connect_host_account_key("SAME_ACCOUNT", "HUGANGTONG") == stock["account_key"]


def test_enabled_connect_binding_and_explicit_key_are_kept(config, tmp_path):
    save(config, tmp_path, "STOCK")
    save(config, tmp_path, "SHENGANGTONG")
    assert web.stock_connect_host_account_key("SAME_ACCOUNT", "SHENGANGTONG") is None
    assert web.stock_connect_host_account_key("SAME_ACCOUNT", "HUGANGTONG", account_key="given") == "given"


def test_other_types_and_accounts_are_not_rerouted(config, tmp_path):
    save(config, tmp_path, "STOCK")
    save(config, tmp_path, "STOCK", enabled=False, account_id="OFF_ACCOUNT")
    assert web.stock_connect_host_account_key("SAME_ACCOUNT", "CREDIT") is None
    assert web.stock_connect_host_account_key("SAME_ACCOUNT", "STOCK") is None
    assert web.stock_connect_host_account_key("OTHER_ACCOUNT", "HUGANGTONG") is None
    assert web.stock_connect_host_account_key("OFF_ACCOUNT", "HUGANGTONG") is None


def test_account_request_sends_connect_query_to_the_stock_bridge(config, tmp_path, monkeypatch):
    stock = save(config, tmp_path, "STOCK")
    save(config, tmp_path, "HUGANGTONG", enabled=False)
    calls = []
    monkeypatch.setattr(web.CLIENTS, "request",
                        lambda bridge_id, channel, action, params, **kwargs: calls.append((bridge_id, params)) or [])
    params = {"account": {"account_id": "SAME_ACCOUNT", "account_type": 7, "bridge_id": stock["bridge_id"]}}
    route = web.account_request("SAME_ACCOUNT", stock["bridge_id"], None, "xttrader.query_stock_asset", params,
                                account_type="HUGANGTONG")
    assert calls[0][0] == stock["bridge_id"]
    assert calls[0][1]["account"]["account_type"] == 7
    assert route["account_type"] == "HUGANGTONG"
    assert route["account_key"] == stock["account_key"]


@pytest.fixture
def bridge_calls(config, tmp_path, monkeypatch):
    stock = save(config, tmp_path, "STOCK")
    calls = []
    orders = [{"order_id": 2491947194236445846, "order_sysid": "831416"},
              {"order_id": 2491947194236445847, "order_sysid": ""}]

    def request(bridge_id, channel, action, params, **kwargs):
        calls.append((action, params))
        if action == "xttrader.query_stock_orders":
            return orders
        return {"cancel_result": 0, "request_result": True, "order_id": params.get("sysid", params.get("order_id"))}

    monkeypatch.setattr(web.CLIENTS, "request", request)
    return stock, calls


@pytest.mark.parametrize(("action", "sysid_action"), [
    ("xttrader.cancel_order_stock", "xttrader.cancel_order_stock_sysid"),
    ("xttrader.cancel_order_stock_async", "xttrader.cancel_order_stock_sysid_async"),
])
@pytest.mark.parametrize(("account_type", "market"), [("SHENGANGTONG", "SGT"), ("HUGANGTONG", "HGT")])
def test_connect_cancel_by_order_id_goes_out_by_sysid(bridge_calls, action, sysid_action, account_type, market):
    stock, calls = bridge_calls
    params = {"account": {"account_id": "SAME_ACCOUNT", "account_type": account_type},
              "order_id": 2491947194236445846, "seq": 5}
    route = web.account_request("SAME_ACCOUNT", stock["bridge_id"], None, action, params, default_channel="trade")
    assert [name for name, _params in calls] == ["xttrader.query_stock_orders", sysid_action]
    sent = calls[1][1]
    assert (sent["sysid"], sent["market"], sent["order_id_kind"], sent["seq"]) == ("831416", market, "sysid", 5)
    assert route["result"]["order_id"] == 2491947194236445846
    assert route["result"]["order_sysid"] == "831416"
    assert route["cancel_by_sysid"] == "831416"


def test_connect_cancel_without_a_sysid_is_sent_as_given(bridge_calls):
    stock, calls = bridge_calls
    params = {"account": {"account_id": "SAME_ACCOUNT", "account_type": "SHENGANGTONG"}, "order_id": 2491947194236445847}
    web.account_request("SAME_ACCOUNT", stock["bridge_id"], None, "xttrader.cancel_order_stock", params)
    assert [name for name, _params in calls] == ["xttrader.query_stock_orders", "xttrader.cancel_order_stock"]


@pytest.mark.parametrize(("account_type", "extra"), [("STOCK", {}), ("SHENGANGTONG", {"order_id_kind": "sysid"})])
def test_stock_cancel_and_explicit_sysid_are_not_looked_up(bridge_calls, account_type, extra):
    stock, calls = bridge_calls
    params = dict({"account": {"account_id": "SAME_ACCOUNT", "account_type": account_type}, "order_id": "831416"}, **extra)
    web.account_request("SAME_ACCOUNT", stock["bridge_id"], None, "xttrader.cancel_order_stock", params)
    assert [name for name, _params in calls] == ["xttrader.cancel_order_stock"]


def package_bridge():
    bridge = TxTradeBridge.__new__(TxTradeBridge)
    bridge.bridge_id = "test_bridge"
    bridge.account_id = ""
    bridge.account_type = ""
    bridge.subscriber_lock = threading.RLock()
    bridge.account_subscribers = {}
    bridge.client_accounts = {}
    bridge.contexts = []
    bridge._set_context_account = lambda *args: bridge.contexts.append(args)
    bridge._enable_auto_trade_callback = lambda: None
    bridge._log = lambda *args, **kwargs: None
    return bridge


def test_package_bridge_keeps_its_own_account_for_connect_subscriptions():
    bridge = package_bridge()
    try:
        bridge._subscribe_account({"account_id": "SAME_ACCOUNT", "account_type": "STOCK"}, {"client_id": "stock"})
        bridge._subscribe_account({"account_id": "SAME_ACCOUNT", "account_type": "HUGANGTONG"}, {"client_id": "hgt"})
        assert (bridge.account_id, bridge.account_type.upper()) == ("SAME_ACCOUNT", "STOCK")
        assert [(a, t.upper()) for a, t in bridge.contexts] == [("SAME_ACCOUNT", "STOCK")]
        assert bridge._client_ids_for_account("SAME_ACCOUNT", "HUGANGTONG") == ["hgt"]
        bridge._unsubscribe_account({"account_id": "SAME_ACCOUNT", "account_type": "HUGANGTONG"}, {"client_id": "hgt"})
        assert (bridge.account_id, bridge.account_type.upper()) == ("SAME_ACCOUNT", "STOCK")
    finally:
        bridge._unsubscribe_account({}, {"client_id": "stock"})
        bridge._unsubscribe_account({}, {"client_id": "hgt"})
