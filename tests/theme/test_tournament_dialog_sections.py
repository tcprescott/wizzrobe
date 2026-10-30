"""The Add Tournament form: one required field, everything else deferred.

Measured at 1,730 px tall with 21 inputs, 4 selects and 5 checkboxes — of which
exactly one is required. The problem was never the fields; it was that nothing
distinguished "fill this now" from "come back to this when you care", so typing
a name and saving looked like the riskiest option rather than the cheapest
correct one.

Rendering the dialog needs a live NiceGUI client, so this reads the source: that
the optional fields are behind expansions, that those are collapsed on create
and open on edit, and — the one worth automating — that no second field has
quietly become required inside a collapsed section, where nobody would see it.
"""

import ast
import inspect
import textwrap

from theme.dialog.tournament_edit_dialog import TournamentDialog

SOURCE = inspect.getsource(TournamentDialog.open)
TREE = ast.parse(textwrap.dedent(SOURCE))


def _calls(name: str):
    return [
        node for node in ast.walk(TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
    ]


def test_only_the_name_field_is_required():
    """A required field inside a collapsed section is a trap.

    `.props('required')` is what marks one; there must be exactly one, and it
    must be the always-visible name input.
    """
    required = [
        node for node in _calls('props')
        if node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == 'required'
    ]
    assert len(required) == 1, f'{len(required)} fields marked required, expected 1'
    labels = [
        arg.value for call in _calls('input') for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]
    assert 'Tournament Name *' in labels


def _expansion_titles() -> list[str]:
    return [
        call.args[0].value for call in _calls('expansion')
        if call.args and isinstance(call.args[0], ast.Constant)
    ]


def test_the_optional_fields_live_in_named_sections():
    assert _expansion_titles() == [
        'Scheduling', 'Entry & administration', 'Seeds & randomizer', 'Integrations',
        'Danger zone',
    ]


def test_sections_are_collapsed_on_create_and_open_on_edit():
    """One switch drives all four, so they cannot drift apart."""
    assert 'sections_open = not is_create' in SOURCE
    for call in _calls('expansion'):
        title = call.args[0].value if call.args else None
        values = [kw for kw in call.keywords if kw.arg == 'value']
        assert values, 'an expansion with no explicit value defaults to closed on edit too'
        if title == 'Danger zone':
            assert values[0].value.value is False
            continue
        assert isinstance(values[0].value, ast.Name)
        assert values[0].value.id == 'sections_open'


def test_the_name_field_says_the_rest_can_wait():
    assert 'you can come back to it' in SOURCE


def test_every_field_the_submit_reads_is_still_built():
    """Grouping is a layout change; losing a binding would be a silent data loss.

    The submit handler names each input; if a section move dropped one, the
    dialog would raise on save rather than fail a test — so pin it here.
    """
    submit = next(
        node for node in ast.walk(TREE)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'submit'
    )
    read = {
        node.value.id
        for node in ast.walk(submit)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id.endswith(('_input', '_checkbox'))
    }
    assigned = {
        target.id
        for node in ast.walk(TREE) if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
    }
    assert read, 'expected the submit handler to read some inputs'
    assert read <= assigned, f'submit reads inputs that are never built: {read - assigned}'
