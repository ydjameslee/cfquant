# -*- coding: utf-8 -*-
"""Stock Connect symbols at the SDK/QMT boundary (no transport or trading)."""
import re
from decimal import Decimal, InvalidOperation

CONNECT_MARKETS = {"HUGANGTONG": "HGT", "SHENGANGTONG": "SGT"}
TRADE_IDENTITY_FIELDS = (
    "account_type", "m_nBrokerType", "m_nAccountType", "m_strAccountKey",
    "currency", "m_strCurrencyID", "m_strCurrency", "m_dReferenceRate",
    "m_dOrderPriceRMB", "m_dPriceRMB", "m_dTradeAmountRMB",
)


def connect_account_type(value):
    text = str(value or "").strip().upper()
    return {"7": "HUGANGTONG", "11": "SHENGANGTONG",
            "HGT": "HUGANGTONG", "SGT": "SHENGANGTONG",
            "SHANGHAI_HK_CONNECT": "HUGANGTONG", "SHENZHEN_HK_CONNECT": "SHENGANGTONG",
            "HUGANGTONG_ACCOUNT": "HUGANGTONG",
            "SHENGANGTONG_ACCOUNT": "SHENGANGTONG"}.get(text, text)


def is_hk_code(code):
    text = str(code or "").strip().upper()
    return text.startswith("HK.") or text.endswith((".HK", ".HGT", ".SGT"))


def stock_connect_code(code, account_type, qmt=False):
    """Canonical SDK code is HK; QMT order codes explicitly select HGT/SGT."""
    kind = connect_account_type(account_type)
    if kind not in CONNECT_MARKETS:
        raise ValueError("Stock Connect account_type is required")
    text = str(code or "").strip().upper()
    if text.startswith("HK."):
        text = text[3:] + ".HK"
    parts = text.split(".")
    number = parts[0]
    market = parts[1] if len(parts) == 2 else "HK"
    if len(parts) > 2 or not re.fullmatch(r"[0-9]{5}", number) or int(number) == 0:
        raise ValueError("港股代码必须是五位数字，例如 00700.HK")
    if market not in ("HK", CONNECT_MARKETS[kind]):
        raise ValueError("港股代码市场与账户类型不匹配: %s / %s" % (text, kind))
    return number + "." + (CONNECT_MARKETS[kind] if qmt else "HK")


def validate_connect_order(params, optype, detail):
    """Validate the first-release contract: limit orders in whole board lots."""
    if optype not in (23, 24) or params.get("price_type", 11) != 11:
        raise ValueError("港股通首版仅支持限价买入/卖出")
    if params.get("qmt_order_type", 1101) != 1101:
        raise ValueError("港股通仅支持按股数下单 qmt_order_type=1101")
    try:
        price = Decimal(str(params.get("price", 0)))
        volume = Decimal(str(params.get("order_volume", params.get("num", 0))))
    except InvalidOperation:
        raise ValueError("港股通价格和数量必须是有效数字")
    if not price.is_finite() or price <= 0:
        raise ValueError("港股通价格必须为正有限数，单位为港币")
    if not volume.is_finite() or volume <= 0 or volume != volume.to_integral_value():
        raise ValueError("港股通数量必须为正整数股数")
    try:
        lot = Decimal(str((detail or {}).get("VolumeMultiple", 0)))
    except (InvalidOperation, AttributeError):
        lot = Decimal(0)
    if not lot.is_finite() or lot <= 0 or lot != lot.to_integral_value() or (detail or {}).get("cfquant_detail_fallback"):
        raise ValueError("无法获取港股每手股数，请先确认 QMT 合约信息")
    if volume % lot:
        raise ValueError("港股通数量必须是每手 %s 股的整数倍；首版不支持碎股委托" % lot)


CONNECT_ACCOUNT_MARKETS = CONNECT_MARKETS

def normalize_connect_code(value):
    text = str(value or "").strip()
    if "." not in text:
        return text
    code, market = text.rsplit(".", 1)
    market = market.strip().upper()
    if market not in ("HK", "HGT", "SGT"):
        return text
    code = code.strip()
    if not code or len(code) > 5 or not all("0" <= c <= "9" for c in code) or int(code) == 0:
        raise ValueError("HK/HGT/SGT stock code must contain 1 to 5 digits and be positive")
    return "%s.%s" % (code.zfill(5), market)


def validate_connect_market(account_type, stock_code="", market=""):
    account_type = connect_account_type(account_type)
    code = normalize_connect_code(stock_code)
    suffix = code.rsplit(".", 1)[-1].upper() if "." in code else ""
    market = str(market or "").strip().upper()
    expected = CONNECT_ACCOUNT_MARKETS.get(account_type)
    if expected and suffix == "HK":
        suffix = expected
    if expected and market == "HK":
        market = expected
    if market and suffix and market != suffix and (market in ("HGT", "SGT") or suffix in ("HGT", "SGT")):
        raise ValueError("Stock Connect market does not match stock_code")
    target = market or suffix
    expected = CONNECT_ACCOUNT_MARKETS.get(account_type)
    if expected and target and target != expected:
        raise ValueError("%s requires .%s securities" % (account_type, expected))
    if target in ("HGT", "SGT") and target != expected:
        raise ValueError(".%s requires its matching HUGANGTONG/SHENGANGTONG account" % target)
    return code


def query_connect_exchange_rate(bridge, params):
    account = params.get("account") or {}
    account_id = str(account.get("account_id") or "").strip()
    kind = connect_account_type(account.get("account_type"))
    if not account_id or kind not in CONNECT_ACCOUNT_MARKETS:
        raise ValueError("get_hkt_exchange_rate requires a HUGANGTONG/SHENGANGTONG account")
    getter = getattr(bridge, "_get_callable", None) or bridge._get_global_func
    func = getter("get_hkt_exchange_rate")
    if not func:
        raise NotImplementedError("This QMT does not expose get_hkt_exchange_rate")
    return func(account_id, kind)


def normalize_connect_order(params, account_type):
    raw_code = normalize_connect_code(params.get("stock_code", params.get("code", "")))
    kind = connect_account_type(account_type)
    if kind in CONNECT_MARKETS:
        raw_code = stock_connect_code(raw_code, kind, qmt=True)
    code = validate_connect_market(account_type, raw_code)
    expected = CONNECT_ACCOUNT_MARKETS.get(connect_account_type(account_type))
    if expected and not code.endswith("." + expected):
        raise ValueError("Stock Connect orders require an explicit .%s suffix" % expected)
    if expected:
        operation = next((params[name] for name in ("qmt_optype", "passorder_optype", "optype", "order_type")
                          if params.get(name) is not None), None)
        if operation is not None and str(operation).lower() not in ("23", "24", "buy", "sell", "stock_buy", "stock_sell"):
            raise ValueError("Stock Connect order_stock supports STOCK_BUY/SELL (23/24)")
        if any(params.get(name) for name in ("credit_action", "credit_business", "future_action",
                                            "future_business", "option_action", "option_business",
                                            "stock_option_action", "future_option_action", "derivative_action")):
            raise ValueError("Stock Connect does not support credit or derivative actions")
    if code.upper().endswith(".HK"):
        raise ValueError("港股委托必须指定 HUGANGTONG 或 SHENGANGTONG 账户")
    return code
