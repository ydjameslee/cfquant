# -*- coding: utf-8 -*-
"""Cross-QMT order metadata helpers.

The advanced trading path may place orders from a low-latency QMT while order
callbacks arrive in a normal bridge QMT. Some QMT callback objects omit
strategy_name/order_remark for orders placed by a different QMT process. This
module keeps the custom userOrderId metadata in memory and LTtx only; it does
not use local files.
"""

import json
import re
import threading
from .stock_connect import connect_account_type
import time
from datetime import date


ORDER_META_PUSH_KEY = "cfquant.order_meta.upsert"
ORDER_META_DELETE_KEY = "cfquant.order_meta.delete"
ORDER_META_KEY_PREFIX = "cfquant.order_meta."

STORE_USER_PREFIX = "u:"
STORE_REF_PREFIX = "r:"
DEFAULT_BRIDGE_ID = "default"
DEFAULT_ACCOUNT_TYPE = "STOCK"
PENDING_MATCH_TTL_SECONDS = 10 * 60
CANONICAL_ORDER_ID_FIELD = "canonical_order_id"
ORDER_REF_FIELDS = (
    "order_ref",
    "m_strOrderRef",
    "m_strOrderID",
    "m_nRef",
    "m_nOrderID",
    "order_id",
    "m_strOrderSysID",
    "order_sysid",
)

TRADING_DAY_FIELDS = (
    "trading_day",
    "trade_date",
    "m_strTradingDay",
    "m_strTradeDate",
    "m_nTradingDay",
    "m_nTradeDate",
)
ORDER_DATE_FIELDS = (
    "order_date",
    "m_strOrderDate",
    "m_nOrderDate",
)
INTERNAL_REF_FIELDS = ("m_nRef", "internal_ref")
FULL_REF_FIELDS = (
    "m_strOrderID",
    "m_strOrderSysID",
    "order_sysid",
    "order_id",
    "order_ref",
    "m_strOrderRef",
    "m_nOrderID",
)


def current_trade_day(ts=None):
    if ts is None:
        ts = time.time()
    return time.strftime("%Y%m%d", time.localtime(ts))


def safe_key_part(value):
    text = "" if value is None else str(value).strip()
    if not text:
        text = DEFAULT_BRIDGE_ID
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", text)


def normalize_account_type(value):
    if value is None or value == "":
        return DEFAULT_ACCOUNT_TYPE
    text = connect_account_type(value)
    aliases = {
        "2": "STOCK",
        "STOCK": "STOCK",
        "SECURITY": "STOCK",
        "SECURITY_ACCOUNT": "STOCK",
        "STOCK_ACCOUNT": "STOCK",
        "7": "HUGANGTONG",
        "HGT": "HUGANGTONG",
        "HUGANGTONG_ACCOUNT": "HUGANGTONG",
        "SHANGHAI_HK_CONNECT": "HUGANGTONG",
        "11": "SHENGANGTONG",
        "SGT": "SHENGANGTONG",
        "SHENGANGTONG_ACCOUNT": "SHENGANGTONG",
        "SHENZHEN_HK_CONNECT": "SHENGANGTONG",
        "0": "FUTURE",
        "FUTURE": "FUTURE",
        "FUTURES": "FUTURE",
        "CREDIT": "CREDIT",
        "MARGIN": "CREDIT",
    }
    return aliases.get(text, text)


def normalize_bridge_id(bridge_id):
    return safe_key_part(bridge_id or DEFAULT_BRIDGE_ID)


def account_meta_channel(bridge_id, account_type, account_id):
    return "cfquant.%s.order_meta.%s.%s" % (
        normalize_bridge_id(bridge_id),
        safe_key_part(normalize_account_type(account_type)),
        safe_key_part(account_id),
    )


def account_store_key(bridge_id, account_type, account_id):
    return "cfquant.order_meta.store.%s.%s.%s" % (
        normalize_bridge_id(bridge_id),
        safe_key_part(normalize_account_type(account_type)),
        safe_key_part(account_id),
    )


def store_user_key(user_order_id, trading_day=None):
    user_order_id = normalize_text(user_order_id)
    if not user_order_id:
        return ""
    return STORE_USER_PREFIX + (normalize_date(trading_day) or "unknown") + ":" + user_order_id


def store_order_ref_key(order_ref, trading_day=None, ref_kind="generic"):
    order_ref = normalize_order_ref(order_ref)
    if not order_ref:
        return ""
    return STORE_REF_PREFIX + (normalize_date(trading_day) or "unknown") + ":" + safe_key_part(ref_kind) + ":" + order_ref


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("none", "null", "nan"):
        return ""
    return text


def normalize_date(value):
    """Return a validated YYYYMMDD date supplied by QMT, without guessing."""
    text = normalize_text(value)
    if not text:
        return ""
    if re.match(r"^\d{8}$", text):
        compact = text
    else:
        matched = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", text)
        if not matched:
            return ""
        compact = "%s%02d%02d" % (matched.group(1), int(matched.group(2)), int(matched.group(3)))
    try:
        date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
    except (TypeError, ValueError):
        return ""
    return compact


def _authoritative_date(data, names):
    if not isinstance(data, dict):
        return ""
    values = [data.get(name) for name in names if not is_empty(data.get(name))]
    if not values:
        return ""
    normalized = [normalize_date(value) for value in values]
    if not all(normalized) or len(set(normalized)) != 1:
        return ""
    return normalized[0]


def order_dates(data):
    """Extract QMT's trading and calendar dates as distinct, optional values.

    ``trade_day`` is deliberately excluded: older metadata synthesized it from
    the local machine clock, so it cannot establish an order's QMT trading day.
    """
    return {
        "trading_day": _authoritative_date(data, TRADING_DAY_FIELDS),
        "order_date": _authoritative_date(data, ORDER_DATE_FIELDS),
    }


def is_empty(value):
    return normalize_text(value) == ""


def normalize_order_ref(value):
    text = normalize_text(value)
    if not text:
        return ""
    try:
        if isinstance(value, float) and value.is_integer():
            text = str(int(value))
    except Exception:
        pass
    if text in ("0", "-1"):
        return ""
    return text


def positive_order_id(value):
    ref = normalize_order_ref(value)
    if not ref or not ref.isdigit():
        return None
    order_id = int(ref)
    return order_id if order_id > 0 else None


def canonical_order_id_from_record(record):
    if not isinstance(record, dict):
        return None
    for name in (CANONICAL_ORDER_ID_FIELD, "order_id"):
        order_id = positive_order_id(record.get(name))
        if order_id is not None:
            return order_id
    # Older stores used order_ref for the order-list ID before callbacks added
    # the other QMT references. Only use this migration fallback for bound
    # records; callback-bound records may have a callback reference in order_ref.
    if normalize_text(record.get("status")).lower() == "bound":
        return positive_order_id(record.get("order_ref"))
    return None


def reconcile_order_id(data, order_id):
    if not isinstance(data, dict):
        return False
    canonical = positive_order_id(order_id)
    if canonical is None:
        return False
    current_ref = normalize_order_ref(data.get("order_id"))
    mismatched = bool(current_ref and current_ref != str(canonical))
    if mismatched:
        data["cfquant_callback_order_id"] = data.get("order_id")
        data["cfquant_order_id_reconciled"] = True
    data["order_id"] = canonical
    return mismatched


def stock_code_base(value):
    text = normalize_text(value)
    if not text:
        return ""
    return text.split(".", 1)[0].upper()


def first_value(mapping, names):
    if not isinstance(mapping, dict):
        return ""
    for name in names:
        value = mapping.get(name)
        if not is_empty(value):
            return value
    return ""


def plain_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.decode("gbk", errors="replace")
    if isinstance(value, (list, tuple)):
        return [plain_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): plain_value(val) for key, val in value.items()}
    return str(value)


def encode_record(record):
    return json.dumps(plain_value(record or {}), ensure_ascii=False, separators=(",", ":"))


def decode_record_payload(payload):
    if payload is None:
        return None
    if isinstance(payload, dict):
        return plain_value(payload)
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except Exception:
            payload = payload.decode("gbk", errors="replace")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        try:
            decoded = json.loads(text)
        except Exception:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def split_push_message(raw):
    if raw is None:
        return None, None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            raw = raw.decode("gbk", errors="replace")
    raw = str(raw)
    if "|" not in raw:
        return None, None
    key, payload = raw.split("|", 1)
    if not key.startswith(ORDER_META_KEY_PREFIX):
        return None, None
    return key, payload


def user_order_id_from_data(data):
    return normalize_text(
        first_value(
            data,
            (
                "user_order_id",
                "client_order_id",
                "order_remark",
                "remark",
                "m_strRemark",
                "m_strOrderRemark",
            ),
        )
    )


def order_ref_from_data(data):
    refs = order_ref_candidates_from_data(data)
    return refs[0] if refs else ""


def order_ref_candidates_from_data(data):
    if not isinstance(data, dict):
        return []
    values = []
    for name in ORDER_REF_FIELDS:
        value = data.get(name)
        if not is_empty(value):
            values.append(value)
    existing = data.get("order_refs")
    if isinstance(existing, (list, tuple, set)):
        values.extend(existing)
    elif not is_empty(existing):
        values.append(existing)
    refs = []
    seen = set()
    for value in values:
        ref = normalize_order_ref(value)
        if not ref or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs


def qmt_ref_from_data(data):
    """Return the QMT callback reference used in order metadata identity."""
    if not isinstance(data, dict):
        return ""
    return normalize_order_ref(data.get("m_nRef"))


def typed_order_ref_candidates_from_data(data):
    """Return ``(kind, value)`` reference identities without cross-typing IDs."""
    if not isinstance(data, dict):
        return []
    identities = []
    seen = set()
    for kind, names in (("internal", INTERNAL_REF_FIELDS), ("full", FULL_REF_FIELDS)):
        for name in names:
            ref = normalize_order_ref(data.get(name))
            # normalize_record keeps legacy order_ref/m_strOrderRef aliases
            # for consumers. When those aliases mirror m_nRef, they are not a
            # second, full-ID identity and must not cross-match one.
            if kind == "full" and name in ("order_ref", "m_strOrderRef"):
                internal_ref = qmt_ref_from_data(data)
                if internal_ref and ref == internal_ref:
                    continue
            identity = (kind, ref)
            if not ref or identity in seen:
                continue
            seen.add(identity)
            identities.append(identity)
    return identities


def merge_order_ref_candidates(record, refs):
    if not isinstance(record, dict):
        return []
    values = order_ref_candidates_from_data(record)
    if isinstance(refs, (list, tuple, set)):
        values.extend(refs)
    elif not is_empty(refs):
        values.append(refs)
    merged = []
    seen = set()
    for value in values:
        ref = normalize_order_ref(value)
        if not ref or ref in seen:
            continue
        seen.add(ref)
        merged.append(ref)
    if merged:
        record["order_refs"] = merged
    return merged


def normalize_record(record, bridge_id=None, account_type=None, account_id=None, now=None):
    now = time.time() if now is None else now
    data = plain_value(record or {})
    if not isinstance(data, dict):
        data = {}
    data = dict(data)

    if bridge_id is not None:
        data["bridge_id"] = normalize_bridge_id(bridge_id)
    else:
        data["bridge_id"] = normalize_bridge_id(data.get("bridge_id") or DEFAULT_BRIDGE_ID)

    if account_type is not None:
        data["account_type"] = normalize_account_type(account_type)
    else:
        data["account_type"] = normalize_account_type(data.get("account_type"))

    if account_id is not None:
        data["account_id"] = normalize_text(account_id)
    else:
        data["account_id"] = normalize_text(data.get("account_id"))

    user_order_id = user_order_id_from_data(data)
    if user_order_id:
        data["user_order_id"] = user_order_id
        data.setdefault("client_order_id", user_order_id)
        if is_empty(data.get("order_remark")):
            data["order_remark"] = user_order_id

    order_refs = order_ref_candidates_from_data(data)
    order_ref = order_refs[0] if order_refs else ""
    if order_ref:
        data["order_ref"] = order_ref
        if is_empty(data.get("m_strOrderRef")):
            data["m_strOrderRef"] = order_ref
    if order_refs:
        data["order_refs"] = order_refs

    status = normalize_text(data.get("status")).lower()
    canonical_order_id = canonical_order_id_from_record(data)
    if canonical_order_id is None and status == "bound":
        canonical_order_id = positive_order_id(data.get("order_ref"))
    if canonical_order_id is not None:
        if positive_order_id(data.get("order_id")) is None:
            data["order_id"] = canonical_order_id
        data[CANONICAL_ORDER_ID_FIELD] = canonical_order_id

    stock_code = normalize_text(first_value(data, ("stock_code", "code", "m_strInstrumentID", "m_strStockCode")))
    if stock_code:
        data["stock_code"] = stock_code
        data["stock_code_base"] = stock_code_base(stock_code)

    dates = order_dates(data)
    data["trading_day"] = dates["trading_day"]
    data["order_date"] = dates["order_date"]
    if data.get("created_at") is None:
        data["created_at"] = now
    data["updated_at"] = now
    if is_empty(data.get("status")):
        data["status"] = "pending"
    return data


def ensure_callback_text_fields(data):
    if not isinstance(data, dict):
        return data
    for name in (
        "strategy_name",
        "order_remark",
        "m_strStrategyName",
        "m_strRemark",
        "m_strOrderRemark",
    ):
        if data.get(name) is None:
            data[name] = ""
    return data


def apply_record_to_callback(data, record, match_info=None):
    if not isinstance(data, dict):
        return data
    ensure_callback_text_fields(data)
    if not record:
        return data

    strategy_name = normalize_text(record.get("strategy_name"))
    order_remark = normalize_text(record.get("order_remark") or record.get("client_order_id") or record.get("user_order_id"))
    user_order_id = normalize_text(record.get("user_order_id") or order_remark)

    if strategy_name:
        for name in ("strategy_name", "m_strStrategyName"):
            if is_empty(data.get(name)):
                data[name] = strategy_name
    if order_remark:
        for name in ("order_remark", "m_strRemark", "m_strOrderRemark"):
            if is_empty(data.get(name)):
                data[name] = order_remark
    if user_order_id and is_empty(data.get("user_order_id")):
        data["user_order_id"] = user_order_id
    if not is_empty(record.get("client_order_id")) and is_empty(data.get("client_order_id")):
        data["client_order_id"] = normalize_text(record.get("client_order_id"))

    order_id = canonical_order_id_from_record(record)
    if order_id is not None:
        reconcile_order_id(data, order_id)
        order_id_ref = str(order_id)
        for name in ("m_nOrderID",):
            if not normalize_order_ref(data.get(name)):
                data[name] = order_id
        for name in ("m_strOrderRef", "m_strOrderID"):
            if is_empty(data.get(name)) or normalize_order_ref(data.get(name)) in ("0", "-1"):
                data[name] = order_id_ref

    if is_empty(data.get("order_source")) or normalize_text(data.get("order_source")).lower() == "other":
        data["order_source"] = "cfquant"
    data["cfquant_order_meta_hit"] = True
    data["cfquant_order_meta_status"] = normalize_text(record.get("status"))
    if match_info and match_info.get("match_confidence"):
        data["cfquant_order_meta_match"] = match_info.get("match_confidence")
    return data


def store_entries_for_record(record):
    record = normalize_record(record)
    payload = encode_record(record)
    entries = []
    trading_day = record.get("trading_day")
    user_key = store_user_key(record.get("user_order_id"), trading_day)
    if user_key:
        entries.append((user_key, payload))
    for ref_kind, ref in typed_order_ref_candidates_from_data(record):
        ref_key = store_order_ref_key(ref, trading_day, ref_kind)
        if ref_key:
            entries.append((ref_key, payload))
    return entries


class OrderMetaCache(object):
    def __init__(self, bridge_id=None, pending_ttl_seconds=PENDING_MATCH_TTL_SECONDS):
        self.bridge_id = normalize_bridge_id(bridge_id or DEFAULT_BRIDGE_ID)
        self.pending_ttl_seconds = pending_ttl_seconds
        self.lock = threading.RLock()
        self.by_user = {}
        self.by_ref = {}
        self.pending = []

    def _ctx(self, record):
        return (
            normalize_bridge_id(record.get("bridge_id") or self.bridge_id),
            normalize_account_type(record.get("account_type")),
            normalize_text(record.get("account_id")),
        )

    def _user_key(self, record):
        user_order_id = normalize_text(record.get("user_order_id"))
        if not user_order_id:
            return None
        return self._ctx(record) + (user_order_id,)

    def _ref_key(self, record):
        refs = self._ref_keys(record)
        if not refs:
            return None
        return refs[0]

    def _ref_keys(self, record):
        trading_day = normalize_date(record.get("trading_day"))
        identities = typed_order_ref_candidates_from_data(record)
        if not trading_day or not identities:
            return []
        return [self._ctx(record) + (trading_day, kind, ref) for kind, ref in identities]

    @staticmethod
    def _callback_bound(record):
        if not isinstance(record, dict):
            return False
        status = normalize_text(record.get("status")).lower()
        return bool(record.get("callback_bound_at")) or status in ("callback_bound", "callback_seen")

    def prune(self, trade_day=None, now=None):
        trade_day = normalize_date(trade_day)
        now = time.time() if now is None else now
        with self.lock:
            self._prune_locked(trade_day, now)

    def _prune_locked(self, trade_day, now):
        def keep(record, pending=False):
            record_day = normalize_date(record.get("trading_day"))
            # Retain records with an unknown QMT day. Dropping them based on a
            # PC calendar date loses metadata that cannot safely be identified.
            if pending and trade_day and record_day and record_day != trade_day:
                return False
            if pending:
                try:
                    return now - float(record.get("created_at") or now) <= self.pending_ttl_seconds
                except Exception:
                    return True
            return True

        self.by_user = {key: value for key, value in self.by_user.items() if keep(value)}
        self.by_ref = {
            key: [record for record in records if keep(record)]
            for key, records in self.by_ref.items()
            if any(keep(record) for record in records)
        }
        self.pending = [record for record in self.pending if keep(record, pending=True)]

    def upsert(self, record):
        record = normalize_record(record, bridge_id=self.bridge_id)
        with self.lock:
            return self._upsert_locked(record)

    def _upsert_locked(self, record):
        status = normalize_text(record.get("status")).lower()
        if status in ("delete", "deleted", "failed", "cancelled"):
            self._remove_locked(record)
            return record

        user_key = self._user_key(record)
        ref_keys = self._ref_keys(record)
        if user_key:
            self.by_user[user_key] = record
        for ref_key in ref_keys:
            existing = self.by_ref.get(ref_key, [])
            replacement_index = None
            for index, prior in enumerate(existing):
                if self._user_key(prior) == user_key and user_key is not None:
                    replacement_index = index
                    break
            if replacement_index is None:
                existing.append(record)
            else:
                existing[replacement_index] = record
            self.by_ref[ref_key] = existing

        self._remove_pending_locked(record)
        if user_key and not self._callback_bound(record):
            self.pending.append(record)
        return record

    def remove(self, record):
        record = normalize_record(record, bridge_id=self.bridge_id)
        with self.lock:
            self._remove_locked(record)

    def clear_account(self, bridge_id=None, account_type=None, account_id=None):
        ctx = (
            normalize_bridge_id(bridge_id or self.bridge_id),
            normalize_account_type(account_type),
            normalize_text(account_id),
        )
        with self.lock:
            self.by_user = {key: value for key, value in self.by_user.items() if key[:3] != ctx}
            self.by_ref = {key: value for key, value in self.by_ref.items() if key[:3] != ctx}
            self.pending = [record for record in self.pending if self._ctx(record) != ctx]

    def _remove_locked(self, record):
        user_key = self._user_key(record)
        ref_keys = self._ref_keys(record)
        if user_key:
            self.by_user.pop(user_key, None)
        for ref_key in ref_keys:
            remaining = [
                item for item in self.by_ref.get(ref_key, [])
                if self._user_key(item) != user_key
            ]
            if remaining:
                self.by_ref[ref_key] = remaining
            else:
                self.by_ref.pop(ref_key, None)
        self._remove_pending_locked(record)

    def _remove_pending_locked(self, record):
        user_key = self._user_key(record)
        ref_keys = set(self._ref_keys(record))
        next_pending = []
        for item in self.pending:
            if user_key and self._user_key(item) == user_key:
                continue
            item_ref_keys = set(self._ref_keys(item))
            if ref_keys and item_ref_keys and ref_keys.intersection(item_ref_keys):
                continue
            next_pending.append(item)
        self.pending = next_pending

    def load_store(self, store_value, bridge_id=None, account_type=None, account_id=None, trade_day=None):
        store = decode_record_payload(store_value) if not isinstance(store_value, dict) else store_value
        if not isinstance(store, dict):
            return {"loaded": 0, "stale": 0}
        trade_day = normalize_date(trade_day)
        loaded = 0
        stale = 0
        for key, payload in list(store.items()):
            if str(key).startswith("_"):
                continue
            record = decode_record_payload(payload)
            if not isinstance(record, dict):
                continue
            record = normalize_record(
                record,
                bridge_id=bridge_id or record.get("bridge_id") or self.bridge_id,
                account_type=account_type or record.get("account_type"),
                account_id=account_id or record.get("account_id"),
            )
            record_day = normalize_date(record.get("trading_day"))
            if trade_day and record_day and record_day != trade_day:
                stale += 1
                continue
            self.upsert(record)
            loaded += 1
        return {"loaded": loaded, "stale": stale}

    def resolve_callback(self, data, bridge_id=None, account_type=None, account_id=None, allow_pending=True):
        callback_record = normalize_record(
            data,
            bridge_id=bridge_id or self.bridge_id,
            account_type=account_type or data.get("account_type"),
            account_id=account_id or data.get("account_id"),
        )
        with self.lock:
            record = None
            confidence = ""
            order_refs = order_ref_candidates_from_data(callback_record)
            ref_keys = self._ref_keys(callback_record)
            # Metadata attribution is only safe when QMT supplied every part
            # of its order identity: bridge, account type/id, trading day and
            # a typed QMT reference. Unknown values must never fall back to a
            # local date, order calendar date, user ID, or FIFO heuristic.
            if ref_keys:
                candidates = []
                seen = set()
                for ref_key in ref_keys:
                    for candidate in self.by_ref.get(ref_key, []):
                        ident = id(candidate)
                        if ident not in seen:
                            seen.add(ident)
                            candidates.append(candidate)
                if len(candidates) == 1:
                    record = candidates[0]
                    confidence = "order_ref"

            bound_order_ref = ""
            bound_order_refs = []
            if record and order_refs:
                existing_refs = order_ref_candidates_from_data(record)
                existing_set = set(existing_refs)
                bound_order_refs = [ref for ref in order_refs if ref not in existing_set]
                merged_refs = merge_order_ref_candidates(record, order_refs)
                if not normalize_order_ref(record.get("order_ref")):
                    record["order_ref"] = order_refs[0]
                    record["m_strOrderRef"] = order_refs[0]
                elif is_empty(record.get("m_strOrderRef")):
                    record["m_strOrderRef"] = normalize_order_ref(record.get("order_ref"))
                if confidence in ("pending_fifo", "order_ref", "user_order_id"):
                    record["status"] = "callback_bound"
                    record["callback_bound_at"] = time.time()
                record["updated_at"] = time.time()
                bound_order_ref = order_refs[0] if bound_order_refs or merged_refs else ""
                self._upsert_locked(record)

            return record, {
                "bound_order_ref": bound_order_ref,
                "bound_order_refs": bound_order_refs,
                "match_confidence": confidence,
            }

    def _unique_records_locked(self):
        records = []
        seen = set()
        for mapping in (self.by_ref, self.by_user):
            for value in mapping.values():
                values = value if isinstance(value, list) else [value]
                for record in values:
                    ident = id(record)
                    if ident in seen:
                        continue
                    seen.add(ident)
                    records.append(record)
        for record in self.pending:
            ident = id(record)
            if ident in seen:
                continue
            seen.add(ident)
            records.append(record)
        return records

    def _match_known_locked(self, callback_record, order_refs=None):
        ref_keys = self._ref_keys(callback_record)
        if not ref_keys:
            return None, ""
        candidates = []
        seen = set()
        for ref_key in ref_keys:
            for candidate in self.by_ref.get(ref_key, []):
                ident = id(candidate)
                if ident not in seen:
                    seen.add(ident)
                    candidates.append(candidate)
        if len(candidates) != 1:
            return None, ""
        return candidates[0], "record_ref_scan"

    def _match_pending_locked(self, callback_record):
        # A pending record without QMT's m_nRef cannot prove callback
        # ownership. Resolve it only after it has the same full QMT identity.
        return self._match_known_locked(callback_record)

    @staticmethod
    def _has_callback_metadata(record):
        return bool(
            normalize_text(record.get("strategy_name"))
            or normalize_text(record.get("order_remark"))
            or normalize_text(record.get("user_order_id"))
            or normalize_text(record.get("client_order_id"))
        )

    def _context_match_evidence(self, record, callback_record):
        evidence = 0
        record_stock = stock_code_base(record.get("stock_code") or record.get("stock_code_base"))
        callback_stock = stock_code_base(callback_record.get("stock_code") or callback_record.get("stock_code_base"))
        if callback_stock and record_stock:
            if callback_stock != record_stock:
                return -1
            evidence += 1
        if not self._compatible_value(record.get("order_type"), callback_record.get("order_type")):
            return -1
        if not is_empty(record.get("order_type")) and not is_empty(callback_record.get("order_type")):
            evidence += 1
        if not self._compatible_number(record.get("order_volume"), callback_record.get("order_volume")):
            return -1
        if not is_empty(record.get("order_volume")) and not is_empty(callback_record.get("order_volume")):
            evidence += 1
        if not self._compatible_number(record.get("price"), callback_record.get("price")):
            return -1
        if not is_empty(record.get("price")) and not is_empty(callback_record.get("price")):
            evidence += 1
        return evidence

    @staticmethod
    def _has_stock_context_match(record, callback_record):
        record_stock = stock_code_base(record.get("stock_code") or record.get("stock_code_base"))
        callback_stock = stock_code_base(callback_record.get("stock_code") or callback_record.get("stock_code_base"))
        return bool(callback_stock and record_stock and callback_stock == record_stock)

    @staticmethod
    def _record_timestamp(record):
        for name in ("updated_at", "callback_bound_at", "created_at"):
            try:
                return float(record.get(name) or 0)
            except Exception:
                continue
        return 0.0

    @staticmethod
    def _record_meta_identity(record):
        return (
            normalize_text(record.get("strategy_name")),
            normalize_text(record.get("order_remark")),
            normalize_text(record.get("user_order_id")),
            normalize_text(record.get("client_order_id")),
        )

    @staticmethod
    def _compatible_value(left, right):
        if is_empty(left) or is_empty(right):
            return True
        return normalize_text(left) == normalize_text(right)

    @staticmethod
    def _compatible_number(left, right):
        if is_empty(left) or is_empty(right):
            return True
        try:
            return abs(float(left) - float(right)) <= 1e-8
        except Exception:
            return normalize_text(left) == normalize_text(right)
