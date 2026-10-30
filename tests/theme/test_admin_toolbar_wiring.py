"""Two admin-toolbar defects the audit found, and the guards against their return.

Both were *silent*: the control rendered, the click did nothing, and the only
evidence was a line in the server log.

1. **A hidden column is not hidden.** ``{'hidden': True}`` is this app's own
   column convention. ``mobile_grid`` honours it when building the phone card;
   Quasar's ``QTable`` has no such property and painted the column anyway, so
   six admin tables printed a raw primary key as their first desktop column
   while correctly hiding it on a phone. ``apply_column_visibility`` translates
   the convention into Quasar's ``visible-columns``.

2. **A refresh button spawned from a click loses its tenant.** A task created in
   a client event handler has neither the tenant contextvar nor the client stash
   the fallback reads, so the first scoped query inside raises and NiceGUI
   swallows it. Eleven of fifteen admin refresh controls were dead this way.
   ``refresh_button`` captures the render context at build time.

The source scan is the part that matters: a *new* tab that hand-rolls either
pattern is the regression, and only a scan can see it.
"""

import ast
import re
from pathlib import Path

from theme.tables.mobile_grid import visible_column_names

REPO = Path(__file__).resolve().parents[2]
PRESENTATION = [REPO / 'pages', REPO / 'theme']


def _python_files():
    for root in PRESENTATION:
        for path in root.rglob('*.py'):
            if '__pycache__' not in str(path):
                yield path


def _refresh_button_body() -> str:
    """``refresh_button``'s code with its docstring removed.

    The prose explains the very bug the assertions below pin, so matching
    against the raw source finds the explanation rather than the code.
    """
    from theme.tables import admin_crud

    tree = ast.parse(Path(admin_crud.__file__).read_text())
    func = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == 'refresh_button'
    )
    if (func.body and isinstance(func.body[0], ast.Expr)
            and isinstance(func.body[0].value, ast.Constant)):
        func.body = func.body[1:]
    return ast.unparse(func)


class TestHiddenColumnsAreActuallyHidden:
    def test_visible_names_drop_hidden_and_keep_actions(self):
        columns = [
            {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
            {'name': 'name', 'label': 'Name', 'field': 'name'},
            {'name': 'actions', 'label': '', 'field': 'actions'},
        ]
        assert visible_column_names(columns) == ['name', 'actions']

    def test_a_list_with_nothing_hidden_is_unchanged(self):
        columns = [{'name': 'a'}, {'name': 'b'}]
        assert visible_column_names(columns) == ['a', 'b']

    def test_every_file_declaring_a_hidden_column_applies_the_visibility(self):
        """A ``hidden`` column in a file that never applies it renders anyway."""
        applies = re.compile(r'enable_mobile_grid|apply_column_visibility|customize_table')
        delegates = re.compile(r'TableView\(\s*\n?\s*columns=')
        offenders = []
        for path in _python_files():
            source = path.read_text()
            if "'hidden': True" not in source:
                continue
            if applies.search(source) or delegates.search(source):
                continue
            offenders.append(str(path.relative_to(REPO)))
        assert not offenders, (
            'these files mark a column hidden but never apply the visibility, so '
            f'Quasar paints it: {offenders}'
        )

    def test_enable_mobile_grid_applies_it(self):
        """The common path must not need the caller to remember."""
        from theme.tables import mobile_grid

        source = ast.unparse(ast.parse(Path(mobile_grid.__file__).read_text()))
        assert 'apply_column_visibility(table, columns)' in source


class TestRefreshControlsCarryTheirContext:
    _HAND_ROLLED = re.compile(
        r"icon=['\"]refresh['\"][^\n]*\n?[^\n]*background_tasks\.create", re.MULTILINE
    )
    # Same opt-out idiom as `# mobile-grid: exempt`, for the one board whose
    # loader is genuinely tenant-agnostic (the /platform health probe).
    _EXEMPT = re.compile(r'refresh-context:\s*exempt', re.IGNORECASE)

    def test_no_tab_hand_rolls_a_refresh_button(self):
        offenders = []
        for path in _python_files():
            source = path.read_text()
            lines = source.splitlines()
            for match in self._HAND_ROLLED.finditer(source):
                line = source[: match.start()].count('\n') + 1
                window = '\n'.join(lines[max(0, line - 6):line])
                if self._EXEMPT.search(window):
                    continue
                offenders.append(f'{path.relative_to(REPO)}:{line}')
        assert not offenders, (
            'a refresh button wired straight to background_tasks.create loses the '
            'tenant and fails silently — use theme.tables.admin_crud.refresh_button: '
            f'{offenders}'
        )

    def test_refresh_button_captures_the_context_at_build_time(self):
        body = _refresh_button_body()
        # Captured in the builder, rebound in the click, not the other way round.
        assert body.index('capture_render_context()') < body.index('on_click=')
        assert 'scoped_background(context' in body


    def test_refresh_button_defers_the_call_into_the_task(self):
        """``refresh()`` must run *inside* the task, not when the button is built.

        A ``@ui.refreshable``'s ``.refresh()`` returns an ``AwaitableResponse``
        that must be awaited immediately or not at all, so calling it early —
        which is what ``background_tasks.create(view.refresh())`` did on the
        Equipment and Feedback tabs — raises instead of refreshing.
        """
        body = _refresh_button_body()
        assert 'result = refresh()' in body
        assert 'inspect.isawaitable(result)' in body
