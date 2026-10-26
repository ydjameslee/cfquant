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
