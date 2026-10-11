"""Per-submission QMT remarks, compatible with embedded Python 3.6.

The public remark is user data, never a unique order key. Keep a UUID in the
native remark so independent QMT processes and restarts can recover both.
"""

import re
import uuid


_ORDER_REMARK_SUFFIX = re.compile(r"__cfq_([0-9a-f]{32})$")


def original_order_remark(value):
    text = str(value or "")
    match = _ORDER_REMARK_SUFFIX.search(text)
    return text[:match.start()] if match else text


def is_unique_order_remark(value):
    return bool(_ORDER_REMARK_SUFFIX.search(str(value or "")))


def prepare_order_remark(params, fallback=""):
    original = next((params[name] for name in ("order_remark", "remark", "strategy_name")
                     if params.get(name) not in (None, "")), "")
    original = str(original or "")
    internal = params.get("cfquant_order_remark")
    if not (is_unique_order_remark(internal) and
            original_order_remark(internal) in (original, original or str(fallback or ""))):
        original = original or str(fallback or "")
        internal = original + "__cfq_" + uuid.uuid4().hex
    params["cfquant_order_remark"] = internal
    return internal


def order_remark_key(data):
    getter = data.get if isinstance(data, dict) else lambda name: getattr(data, name, None)
    values = [getter(name) for name in (
        "cfquant_order_remark", "user_order_id", "client_order_id",
        "order_remark", "m_strRemark", "m_strOrderRemark", "remark",
    )]
    for value in values:
        if is_unique_order_remark(value):
            return str(value)
    return next((str(value) for value in values if value not in (None, "")), "")


def restore_order_remark(data):
    if isinstance(data, dict):
        internal = order_remark_key(data)
        if is_unique_order_remark(internal):
            data["cfquant_order_remark"] = internal
            data["order_remark"] = original_order_remark(internal)
    return data
