from cfquant import order_meta


def test_order_dates_preserves_authoritative_trading_day_separately_from_order_date():
    dates = order_meta.order_dates({
        "m_strTradingDay": "2026-09-26",
        "m_strOrderDate": "2026-09-25",
    })

    assert dates == {"trading_day": "20260926", "order_date": "20260925"}


def test_order_dates_fails_closed_when_authoritative_sources_conflict_or_are_invalid():
    assert order_meta.order_dates({
        "trading_day": "20260926", "m_strTradingDay": "20260925",
    })["trading_day"] == ""
    assert order_meta.order_dates({
        "trading_day": "not-a-date", "m_strTradingDay": "20260926",
    })["trading_day"] == ""


def test_normalize_record_does_not_make_order_or_local_calendar_date_a_trading_day():
    record = order_meta.normalize_record({"m_strOrderDate": "2026-09-25"}, now=0)

    assert record["trading_day"] == ""
    assert record["order_date"] == "20260925"
    assert record.get("trade_day", "") == ""


def test_callback_match_requires_same_authoritative_trading_day_and_m_nref():
    cache = order_meta.OrderMetaCache("bridge-a")
    first = cache.upsert({
        "bridge_id": "bridge-a", "account_type": "STOCK", "account_id": "A1",
        "m_nRef": 42, "m_strTradingDay": "20260925", "strategy_name": "previous-day",
    })
    second = cache.upsert({
        "bridge_id": "bridge-a", "account_type": "STOCK", "account_id": "A1",
        "m_nRef": 42, "m_strTradingDay": "20260926", "strategy_name": "current-day",
    })

    record, info = cache.resolve_callback({
        "m_nRef": 42, "m_strTradingDay": "20260926",
    }, bridge_id="bridge-a", account_type="STOCK", account_id="A1")

    assert record is second
    assert record is not first
    assert info["match_confidence"] == "order_ref"


def test_callback_with_missing_trading_day_or_only_order_date_fails_closed():
    cache = order_meta.OrderMetaCache("bridge-a")
    cache.upsert({
        "bridge_id": "bridge-a", "account_type": "STOCK", "account_id": "A1",
        "m_nRef": 42, "m_strTradingDay": "20260926", "strategy_name": "metadata",
    })

    missing_day, _ = cache.resolve_callback(
        {"m_nRef": 42}, bridge_id="bridge-a", account_type="STOCK", account_id="A1"
    )
    order_date_only, _ = cache.resolve_callback(
        {"m_nRef": 42, "m_strOrderDate": "20260926"},
        bridge_id="bridge-a", account_type="STOCK", account_id="A1",
    )

    assert missing_day is None
    assert order_date_only is None


def test_known_record_context_fallback_cannot_bypass_qmt_reference_identity():
    cache = order_meta.OrderMetaCache("bridge-a")
    trading_day = order_meta.current_trade_day()
    cache.upsert({
        "bridge_id": "bridge-a", "account_type": "STOCK", "account_id": "A1",
        "m_nRef": 41, "trading_day": trading_day, "trade_day": trading_day,
        "stock_code": "600000.SH",
        "order_type": 23, "order_volume": 100, "price": 10.5,
        "strategy_name": "metadata",
    })
    callback = order_meta.normalize_record({
        "bridge_id": "bridge-a", "account_type": "STOCK", "account_id": "A1",
        "m_nRef": 42, "trading_day": trading_day, "stock_code": "600000.SH",
        "order_type": 23, "order_volume": 100, "price": 10.5,
    })

    with cache.lock:
        record, confidence = cache._match_known_locked(callback)

    assert record is None
    assert confidence == ""


def test_store_entries_namespace_user_and_qmt_reference_by_trading_day():
    entries = order_meta.store_entries_for_record({
        "user_order_id": "client-42", "m_nRef": 42, "m_strTradingDay": "20260926",
    })

    assert [key for key, _ in entries] == ["u:20260926:client-42", "r:20260926:internal:42"]


def test_prune_keeps_bound_metadata_from_a_different_qmt_session():
    cache = order_meta.OrderMetaCache("bridge-a")
    record = cache.upsert({
        "bridge_id": "bridge-a", "account_type": "STOCK", "account_id": "A1",
        "m_nRef": 42, "m_strTradingDay": "20260925", "status": "callback_bound",
    })

    cache.prune("20260926", now=0)

    assert cache.by_ref[cache._ref_key(record)] == [record]


def test_callback_enrichment_does_not_turn_a_full_order_id_into_m_nref():
    callback = {"m_nRef": ""}
    order_meta.apply_record_to_callback(callback, {"order_id": 999, "status": "bound"})

    assert callback["m_nRef"] == ""


def test_callback_identity_is_isolated_by_bridge_account_type_and_account_id():
    cache = order_meta.OrderMetaCache("bridge-a")
    other_bridge = order_meta.OrderMetaCache("bridge-b")
    common = {"m_nRef": 42, "m_strTradingDay": "20260926", "strategy_name": "wrong"}
    other_bridge.upsert(dict(common, bridge_id="bridge-b", account_type="STOCK", account_id="A1"))
    cache.upsert(dict(common, bridge_id="bridge-a", account_type="FUTURE", account_id="A1"))
    cache.upsert(dict(common, bridge_id="bridge-a", account_type="STOCK", account_id="A2"))
    expected = cache.upsert(dict(common, bridge_id="bridge-a", account_type="STOCK", account_id="A1",
                                 strategy_name="expected"))

    record, _ = cache.resolve_callback(
        {"m_nRef": 42, "m_strTradingDay": "20260926"},
        bridge_id="bridge-a", account_type="STOCK", account_id="A1",
    )

    assert record is expected


def test_same_day_same_reference_with_different_full_ids_is_ambiguous():
    cache = order_meta.OrderMetaCache("bridge-a")
    common = {
        "bridge_id": "bridge-a", "account_type": "STOCK", "account_id": "A1",
        "m_nRef": 42, "m_strTradingDay": "20260926",
    }
    cache.upsert(dict(common, user_order_id="one", m_strOrderID="full-one"))
    cache.upsert(dict(common, user_order_id="two", m_strOrderID="full-two"))

    record, info = cache.resolve_callback(
        {"m_nRef": 42, "m_strTradingDay": "20260926"},
        bridge_id="bridge-a", account_type="STOCK", account_id="A1",
    )

    assert record is None
    assert info["match_confidence"] == ""


def test_legacy_trade_day_is_not_accepted_as_an_authoritative_trading_day():
    cache = order_meta.OrderMetaCache("bridge-a")
    cache.upsert({
        "bridge_id": "bridge-a", "account_type": "STOCK", "account_id": "A1",
        "m_nRef": 42, "trade_day": "20260926", "strategy_name": "legacy",
    })

    record, _ = cache.resolve_callback(
        {"m_nRef": 42, "m_strTradingDay": "20260926"},
        bridge_id="bridge-a", account_type="STOCK", account_id="A1",
    )

    assert record is None


def test_full_order_identifier_matches_only_the_same_typed_identifier():
    cache = order_meta.OrderMetaCache("bridge-a")
    cache.upsert({
        "bridge_id": "bridge-a", "account_type": "STOCK", "account_id": "A1",
        "m_strOrderID": "42", "m_strTradingDay": "20260926", "strategy_name": "full-id",
    })

    full_record, _ = cache.resolve_callback(
        {"m_strOrderID": "42", "m_strTradingDay": "20260926"},
        bridge_id="bridge-a", account_type="STOCK", account_id="A1",
    )
    internal_record, _ = cache.resolve_callback(
        {"m_nRef": 42, "m_strTradingDay": "20260926"},
        bridge_id="bridge-a", account_type="STOCK", account_id="A1",
    )

    assert full_record is not None
    assert full_record["strategy_name"] == "full-id"
    assert internal_record is None
