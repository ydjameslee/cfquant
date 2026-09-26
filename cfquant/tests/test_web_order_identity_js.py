import subprocess
from pathlib import Path


def test_browser_order_identity_scopes_internal_ref_by_authoritative_day():
    source = (Path(__file__).resolve().parents[2] / 'web_dashboard/app.js').read_text(encoding='utf-8')
    assert 'function orderIdentityKey(row)' in source
    function = source.split('function orderIdentityKey(row)', 1)[1].split('\nfunction ', 1)[0]
    script = 'const assert = require("assert");\nfunction orderIdentityKey(row)' + function + '''
const base = {bridge_id:'one', account_id:'A', account_type:'HUGANGTONG', trading_day:'20260928', m_nRef:42};
assert(orderIdentityKey(base));
for (const change of [{bridge_id:'two'}, {account_id:'B'}, {account_type:'SHENGANGTONG'}, {trading_day:'20260929'}, {m_nRef:43}]) {
  assert.notStrictEqual(orderIdentityKey(base), orderIdentityKey({...base,...change}));
}
assert.strictEqual(orderIdentityKey({...base,trading_day:'',order_date:'20260928'}),'');
assert.strictEqual(orderIdentityKey({...base,m_nRef:null,order_id:42}),'');
assert.strictEqual(orderIdentityKey({...base,order_date:'20260925'}),orderIdentityKey({...base,order_date:'20260926'}));
'''
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
