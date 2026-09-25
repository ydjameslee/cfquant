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
