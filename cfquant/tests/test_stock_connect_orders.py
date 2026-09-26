"""Fake QMT functions only: never places or cancels a live order."""
import pytest
from types import SimpleNamespace
from cfquant.tx_trade_bridge import TxTradeBridge
from cfquant.qmt_bridge import CfquantQmtBridge


@pytest.mark.parametrize('kind,suffix', [('HUGANGTONG', 'HGT'), ('SHENGANGTONG', 'SGT')])
def test_connect_order_selects_type_and_market(kind, suffix):
    calls = []
    class Context:
        def set_account(self, *args):
            calls.append(('account', args))
    bridge = TxTradeBridge(Context(), show=False, globals_dict={
        'passorder': lambda *args: calls.append(('order', args)) or 123,
        'get_instrument_detail': lambda code: {'VolumeMultiple': 100, 'PriceTick': .2},
    })
    bridge.order_meta_enabled = False
    bridge._log = lambda msg: None
    params = {'account': {'account_id': 'TEST', 'account_type': kind},
              'stock_code': '00700.HK', 'order_type': 23, 'price_type': 11,
              'price': 500, 'order_volume': 100}
    try:
        bridge._order_stock(params, {'id': 'test'}, resolve_order_id=False, capture_previous_id=False)
        assert calls[0] == ('account', ('TEST', kind))
        assert calls[1][1][3] == '00700.' + suffix
        assert calls[1][1][0] == 23
        assert params['stock_code'] == '00700.HK'
    finally:
        bridge.close()


@pytest.mark.parametrize('code', ['00700.SGT', '600000.SH', '00700.SZ'])
def test_connect_rejects_wrong_market_before_order(code):
    calls = []
    bridge = TxTradeBridge(None, show=False, globals_dict={'passorder': lambda *a: calls.append(a)})
    bridge.order_meta_enabled = False
    try:
        with pytest.raises(ValueError):
            bridge._order_stock({'account': {'account_id': 'TEST', 'account_type': 7},
                                 'stock_code': code, 'order_type': 23, 'price_type': 11,
                                 'price': 500, 'order_volume': 100}, {'id': 'test'},
                                resolve_order_id=False, capture_previous_id=False)
        assert not calls
    finally:
        bridge.close()


def test_connect_does_not_fallback_to_untyped_context():
    calls = []
    class Legacy:
        def set_account(self, *args):
            calls.append(args)
            if len(args) == 2:
                raise TypeError('typed binding unavailable')
    bridge = TxTradeBridge(Legacy(), show=False)
    try:
        with pytest.raises(TypeError):
            bridge._set_context_account('TEST', 'HUGANGTONG')
        assert calls == [('TEST', 'HUGANGTONG')]
    finally:
        bridge.close()


@pytest.mark.parametrize('change', [
    {'price_type': 5}, {'order_type': 33}, {'price': float('nan')},
    {'price': float('inf')}, {'price': 0}, {'order_volume': 0},
    {'order_volume': 100.5}, {'order_volume': 50}, {'qmt_order_type': 1102},
])
def test_connect_validates_limit_board_lot_order_before_native_call(change):
    calls = []
    class Context:
        def set_account(self, *args):
            pass
    bridge = TxTradeBridge(Context(), show=False, globals_dict={
        'passorder': lambda *a: calls.append(a),
        'get_instrument_detail': lambda code: {'VolumeMultiple': 100},
    })
    bridge.order_meta_enabled = False
    params = {'account': {'account_id': 'TEST', 'account_type': 7}, 'stock_code': '00700.HK',
              'order_type': 23, 'price_type': 11, 'price': 500, 'order_volume': 100}
    params.update(change)
    try:
        with pytest.raises(ValueError):
            bridge._order_stock(params, {'id': 'test'}, resolve_order_id=False, capture_previous_id=False)
        assert calls == []
    finally:
        bridge.close()


def test_legacy_bridge_also_selects_connect_market():
    calls = []
    class Context:
        def set_account(self, *args):
            calls.append(('account', args))
    bridge = CfquantQmtBridge(Context(), show=False, globals_dict={
        'passorder': lambda *args: calls.append(('order', args)) or 123,
        'get_instrument_detail': lambda code: {'VolumeMultiple': 100},
    })
    try:
        bridge._order_stock({'account': {'account_id': 'TEST', 'account_type': 11},
                             'stock_code': '00700.HK', 'order_type': 24, 'price_type': 11,
                             'price': 500, 'order_volume': 100}, resolve_order_id=False)
        assert calls[0] == ('account', ('TEST', 'SHENGANGTONG'))
        assert calls[1][1][3] == '00700.SGT'
    finally:
        bridge.close()


@pytest.mark.parametrize('cls', [TxTradeBridge, CfquantQmtBridge])
def test_connect_raw_identity_currency_and_symbol_survive_formatting(cls):
    bridge = cls(None, show=False)
    raw = SimpleNamespace(m_strAccountID='TEST', m_strAccountKey='7____1____1____1____TEST____',
                          m_nBrokerType=7, m_strInstrumentID='00700', m_strExchangeID='HGT',
                          m_nOffsetFlag=48, m_dReferenceRate=.91, m_dOrderPriceRMB=455,
                          m_strCurrencyID='HKD')
    try:
        row = bridge._format_trade_detail(raw, 'ORDER')
        assert row['stock_code'] == '00700.HK'
        assert row['order_type'] == 23
        assert row['m_strAccountKey'] == raw.m_strAccountKey
        assert row['m_nBrokerType'] == 7
        assert row['m_dReferenceRate'] == .91
        assert row['m_dOrderPriceRMB'] == 455
        assert row['m_strCurrencyID'] == 'HKD'
    finally:
        bridge.close()


@pytest.mark.parametrize('kind,expected', [(7, 'hugangtong'), ('7', 'hugangtong'), ('HGT', 'hugangtong'), (11, 'shengangtong'), ('11', 'shengangtong'), ('SGT', 'shengangtong')])
def test_query_and_cancel_keep_account_type(kind, expected):
    queries, cancels = [], []
    detail_rows = [[], [{
        'm_strAccountID': 'TEST', 'm_strOrderSysID': '1001',
        'm_strTradingDay': '20260925',
    }]]
    bridge = TxTradeBridge(None, show=False, globals_dict={
        'get_trade_detail_data': lambda *a: queries.append(a) or detail_rows.pop(0),
        'cancel': lambda *a: cancels.append(a) or True,
    })
    params = {'account': {'account_id': 'TEST', 'account_type': kind}, 'order_id': '1001'}
    try:
        assert bridge._query_trade_detail(params, 'order') == []
        bridge._cancel_order_stock(params)
        assert queries == [('TEST', expected, 'order'), ('TEST', expected, 'order')]
        assert cancels[0][1:3] == ('TEST', expected)
    finally:
        bridge.close()


@pytest.mark.parametrize('cls', [TxTradeBridge, CfquantQmtBridge])
def test_ordinary_account_rejects_hk_security(cls):
    calls = []
    bridge = cls(None, show=False, globals_dict={'passorder': lambda *a: calls.append(a)})
    params = {'account': {'account_id': 'TEST', 'account_type': 2}, 'stock_code': '00700.HK',
              'order_type': 23, 'price_type': 11, 'price': 500, 'order_volume': 100}
    bridge.order_meta_enabled = False
    try:
        with pytest.raises(ValueError, match='港股'):
            if cls == TxTradeBridge:
                bridge._order_stock(params, {'id': 'test'}, resolve_order_id=False, capture_previous_id=False)
            else:
                bridge._order_stock(params, resolve_order_id=False)
        assert calls == []
    finally:
        bridge.close()


@pytest.mark.parametrize("kind", ["HUGANGTONG", "SHENGANGTONG"])
def test_batch_cancel_canonical_hk_code_does_not_infer_a_share_market(kind):
    from cfquant.batch_orders import execute_qmt_cancel_batch
    calls = []
    bridge = SimpleNamespace(_cancel_order_stock=lambda params: calls.append(params) or {"cancel_result": 0})
    params = {"account": {"account_id": "TEST", "account_type": kind}, "batch_id": "hk-cancel",
              "cancels": [{"order_id": "SYS-1", "stock_code": "00700.HK"}]}
    result = execute_qmt_cancel_batch(bridge, params, {}, False)
    assert result["submitted"] == 1
    assert calls[0]["market"] == "HK"
    assert calls[0]["account"]["account_type"] == kind
