"""Sync shared batch helpers and compatible adapters into GBK QMT scripts."""

import argparse
import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START = "# BEGIN GENERATED CFTRADER BATCH\n"
END = "# END GENERATED CFTRADER BATCH\n"


def shared_source():
    connect = (ROOT / "cfquant/stock_connect.py").read_text(encoding="utf-8")
    batch = (ROOT / "cfquant/batch_orders.py").read_text(encoding="utf-8")
    batch = '\n'.join(line for line in batch.split('\n')
                      if not line.startswith('from .stock_connect import '))
    meta = (ROOT / "cfquant/order_meta.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(meta).body if isinstance(n, ast.FunctionDef) and n.name == "normalize_account_type")
    normalizer = "".join(meta.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])
    normalizer = normalizer.replace("def normalize_account_type(", "def _lite_normalize_account_type(")
    normalizer = normalizer.replace("return DEFAULT_ACCOUNT_TYPE", 'return "STOCK"')
    return connect.rstrip() + '\n\n' + normalizer + '\n\n' + batch


def _class_method(source, class_name, method_name):
    tree = ast.parse(source)
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.name == method_name)
    start = min([method.lineno] + [n.lineno for n in method.decorator_list])
    return "".join(source.splitlines(keepends=True)[start - 1:method.end_lineno])


def _replace_class_method(source, class_name, method_name, replacement):
    tree = ast.parse(source)
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.name == method_name)
    lines = source.splitlines(keepends=True)
    start = min([method.lineno] + [n.lineno for n in method.decorator_list])
    lines[start - 1:method.end_lineno] = replacement.splitlines(keepends=True)
    return "".join(lines)


def updated_source(source):
    source = source.replace("\r\n", "\n")
    shared = shared_source()
    block = START + shared.rstrip() + "\n" + END
    if START in source:
        start = source.index(START)
        end = source.index(END, start) + len(END)
        source = source[:start] + block + source[end:]
    else:
        anchor = 'CORE_VERSION = '
        index = source.index(anchor)
        source = source[:index] + block + "\n" + source[index:]
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TxTradeBridge")
    dispatch = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_dispatch")
    lines = source.splitlines(keepends=True)
    branch = [
        '        if action == "xttrader.get_hkt_exchange_rate":\n',
        '            return query_connect_exchange_rate(self, params)\n',
        '        if action in CFTRADER_BATCH_ORDER_ACTIONS:\n',
        '            return execute_qmt_batch(self, params, msg, action.endswith("_async"))\n',
        '        if action in CFTRADER_BATCH_CANCEL_ACTIONS:\n',
        '            return execute_qmt_cancel_batch(self, params, msg, action.endswith("_async"))\n',
    ]
    if lines[dispatch.lineno:dispatch.lineno + len(branch)] != branch:
        if lines[dispatch.lineno:dispatch.lineno + 4] == branch[2:]:
            lines[dispatch.lineno:dispatch.lineno] = branch[:2]
        elif (lines[dispatch.lineno:dispatch.lineno + 2]
                == ['        if action in CFTRADER_BATCH_ACTIONS:\n',
                    '            return execute_qmt_batch(self, params, msg, action.endswith("_async"))\n']):
            lines[dispatch.lineno:dispatch.lineno + 2] = branch
        else:
            lines[dispatch.lineno:dispatch.lineno] = branch
    source = ''.join(lines)
    version_source = (ROOT / "cfquant/version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', version_source)
    if match:
        source = re.sub(r'^CORE_VERSION\s*=\s*"[^"]+"', 'CORE_VERSION = "%s"' % match.group(1),
                        source, count=1, flags=re.M)
    # Only copy methods whose dependencies exist in the standalone runtime.
    # The core identity/cancel/token methods depend on order_meta and process
    # state absent from Lite; copying those methods alone breaks live requests.
    core = (ROOT / 'cfquant/tx_trade_bridge.py').read_text(encoding='utf-8')
    for name in ('_passorder_optype', '_account_type_name', '_stock_order_type', '_get_trading_dates'):
        source = _replace_class_method(source, 'TxTradeBridge', name,
                                       _class_method(core, 'TxTradeBridge', name))
    source = source.replace('validate_connect_order(params, account_type)',
                            'normalize_connect_order(params, account_type)')
    normal = (ROOT / 'cfquant/normal_bridge.py').read_text(encoding='utf-8')
    source = _replace_class_method(source, 'NormalQmtBridge', '_callback_account_type',
                                   _class_method(normal, 'NormalQmtBridge', '_callback_account_type').replace(
                                       'order_meta.normalize_account_type', '_lite_normalize_account_type'))
    helper = _class_method(normal, 'NormalQmtBridge', '_account_type_from_account_key').replace(
        'order_meta.normalize_account_type', '_lite_normalize_account_type')
    if 'def _account_type_from_account_key(' in source:
        source = _replace_class_method(source, 'NormalQmtBridge', '_account_type_from_account_key', helper)
    else:
        anchor = '    def _callback_account_type('
        index = source.index(anchor)
        source = source[:index] + helper + '\n\n' + source[index:]
    ast.parse(source, feature_version=(3, 6))
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    outdated = []
    for path in sorted((ROOT / 'qmt_scripts').rglob('CFQUANT_LITE*.py')):
        raw = path.read_bytes()
        source = raw.decode('gbk')
        updated = updated_source(source)
        if source.replace('\r\n', '\n') == updated:
            continue
        outdated.append(str(path.relative_to(ROOT)))
        if not args.check:
            newline = '\r\n' if raw.count(b'\r\n') > raw.count(b'\n') / 2 else '\n'
            path.write_bytes(updated.replace('\n', newline).encode('gbk'))
    for path in outdated:
        print(('Outdated: ' if args.check else 'Updated: ') + path)
    return 1 if args.check and outdated else 0


if __name__ == '__main__':
    raise SystemExit(main())
