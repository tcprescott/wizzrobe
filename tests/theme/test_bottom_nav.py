"""The phone bottom bar renders only where the whole nav fits in it.

It used to show `tabs[:4]` on every hub and hide the rest behind a **More**
button. On Admin — twenty-seven tabs — that spent 72px of phone screen offering
four of them picked by list order, plus a button that is the drawer with an extra
tap. Where the nav does not fit, the drawer is the honest answer.

These assert the decision, not the rendering: `_render_footer` needs a NiceGUI
client, so the rule is checked through the same predicate the method uses.
"""

from theme.base import BaseLayout

CAPACITY = BaseLayout.BOTTOM_NAV_CAPACITY


def _tabs(n):
    return [{'label': f'Tab {i}', 'icon': 'circle', 'content': lambda: None} for i in range(n)]


def renders_bar(tabs) -> bool:
    """Mirrors the guard at the top of `_render_footer`."""
    return bool(tabs) and len(tabs) <= CAPACITY


def _body(func) -> str:
    """A function's source with its docstring stripped.

    The docstring explains the More button this replaced, so a naive substring
    search over the whole source matches the history rather than the code.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    node = tree.body[0]
    statements = node.body[1:] if ast.get_docstring(node) else node.body
    return '\n'.join(ast.unparse(s) for s in statements)


class TestWhenTheBarRenders:
    def test_a_nav_that_fits_gets_the_bar(self):
        for n in range(1, CAPACITY + 1):
            assert renders_bar(_tabs(n)), f'{n} tabs should render the bar'

    def test_a_nav_that_does_not_fit_gets_no_bar(self):
        for n in (CAPACITY + 1, CAPACITY + 10, 27):
            assert not renders_bar(_tabs(n)), f'{n} tabs should not render the bar'

    def test_a_tab_less_page_gets_no_bar(self):
        assert not renders_bar([])
        assert not renders_bar(None)


class TestTheBarHasNoMoreButton:
    def test_more_is_gone(self):
        assert "'More'" not in _body(BaseLayout._render_footer)
        assert '_more_btn' not in _body(BaseLayout._render_footer)
        assert not hasattr(BaseLayout, '_more_btn')

    def test_every_tab_is_in_the_bar_when_it_renders(self):
        assert 'self.tabs[:' not in _body(BaseLayout._render_footer)
