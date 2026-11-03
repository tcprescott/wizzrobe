"""Every desktop table is customizable, or says why not — the scan CI cannot skip.

The `check_table_prefs` hook catches this while a file is being edited; this is
the same rule enforced where it cannot be bypassed. A table that ships without a
key is not broken, it is just the one board nobody can adapt — which is exactly
what the whole feature exists to end, and it is invisible in review because the
table renders fine.
"""

import ast
import re
from pathlib import Path

from theme.tables.preferences import TABLE_KEYS, TableKeys

REPO = Path(__file__).resolve().parents[2]
PRESENTATION = [REPO / 'pages', REPO / 'theme']
EXEMPT_RE = re.compile(r'table-prefs:\s*exempt|noqa:\s*table-prefs', re.IGNORECASE)


def _python_files():
    for root in PRESENTATION:
        for path in root.rglob('*.py'):
            if '__pycache__' not in str(path):
                yield path


def _is_ui_table(node) -> bool:
    fn = node.func
    return (isinstance(fn, ast.Attribute) and fn.attr == 'table'
            and isinstance(fn.value, ast.Name) and fn.value.id == 'ui')


def _customized_targets(tree, source: str) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', None)
        if name == 'customize_table':
            targets.add(ast.get_source_segment(source, node.args[0]) or '')
        elif name == 'enable_mobile_grid' and any(k.arg == 'table_key' for k in node.keywords):
            targets.add(ast.get_source_segment(source, node.args[0]) or '')
    return targets


def _assigned_name(tree, call, source: str):
    """The name a ``ui.table(...)`` lands in, through any ``.classes().props()`` chain."""
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    node = call
    while node is not None and not isinstance(node, ast.stmt):
        node = parents.get(node)
    if isinstance(node, ast.Assign) and node.targets:
        return ast.get_source_segment(source, node.targets[0])
    if isinstance(node, ast.AnnAssign):
        return ast.get_source_segment(source, node.target)
    return None


class TestCoverage:
    def test_every_presentation_table_has_a_key_or_an_exemption(self):
        offenders = []
        for path in _python_files():
            source = path.read_text()
            if 'ui.table(' not in source:
                continue
            tree = ast.parse(source)
            if 'self.table_key' in source:
                continue
            customized = _customized_targets(tree, source)
            lines = source.splitlines()
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and _is_ui_table(node)):
                    continue
                window = '\n'.join(lines[max(0, node.lineno - 2):node.lineno])
                if EXEMPT_RE.search(window):
                    continue
                if _assigned_name(tree, node, source) in customized:
                    continue
                offenders.append(f'{path.relative_to(REPO)}:{node.lineno}')
        assert not offenders, (
            'these tables cannot be customized and carry no exemption: '
            f'{offenders}')

    def test_every_exemption_carries_a_reason(self):
        # Mirrors the `mobile-grid: exempt` convention: an exemption with no
        # reason is a TODO nobody will ever read.
        bare = []
        for path in _python_files():
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                if not EXEMPT_RE.search(line):
                    continue
                after = line.split('exempt', 1)[-1].strip(' —-:,')
                before = line.split('#', 1)[-1].split('table-prefs')[0].strip(' —-:,')
                if not after and not before:
                    bare.append(f'{path.relative_to(REPO)}:{number}')
        assert not bare, f'exemptions without a reason: {bare}'


class TestTheKeyRegistry:
    def test_every_table_key_is_declared_in_TableKeys(self):
        used = set()
        for path in _python_files():
            source = path.read_text()
            for match in re.finditer(r'(?:table_key|key)=TableKeys\.([A-Z_]+)', source):
                used.add(match.group(1))
            for match in re.finditer(r"table_key=['\"]([^'\"]+)['\"]", source):
                assert match.group(1) in TABLE_KEYS, (
                    f'{path}: inline key {match.group(1)!r} is not in TableKeys')
        declared = {name for name in vars(TableKeys) if not name.startswith('_')}
        assert used <= declared, f'undeclared keys in use: {sorted(used - declared)}'

    def test_table_keys_are_unique(self):
        values = [v for k, v in vars(TableKeys).items()
                  if not k.startswith('_') and isinstance(v, str)]
        assert len(values) == len(set(values))

    def test_every_declared_key_is_used(self):
        source = '\n'.join(p.read_text() for p in _python_files())
        unused = [
            name for name, value in vars(TableKeys).items()
            if not name.startswith('_') and isinstance(value, str)
            and f'TableKeys.{name}' not in source
        ]
        assert not unused, f'declared but never passed: {unused}'
