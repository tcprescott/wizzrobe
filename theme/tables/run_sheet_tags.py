"""The small tags on a run-sheet line: who said they're running late, and the
match's comment counts. Built per row by ``decorate_run_sheet_row``."""


def late_tags(row: dict) -> list[dict]:
    """'Player One ~20 min late · said 09:41' per player who told staff they're late.

    ``note`` is what they added, for the tag's hover title; the Needs you inbox
    prints it in full.
    """
    return [{
        'key': entry.get('user_id'),
        'label': f"{entry.get('name')} {entry.get('tag')} late"
                 + (f" · said {entry['said']}" if entry.get('said') else ''),
        'note': entry.get('note') or '',
    } for entry in row.get('late') or []]


def comment_badges(counts) -> list[dict]:
    """The line's comment counts, players first, leaving out a zero.

    ``counts`` is only set on a staff board (``MatchTableView.show_comment_counts``),
    since the staff number includes private notes.
    """
    if not counts:
        return []
    badges = []
    for key, icon, one, many in (
        ('player', 'person', 'player comment', 'player comments'),
        ('staff', 'shield', 'staff comment', 'staff comments'),
    ):
        n = counts.get(key) or 0
        if n:
            badges.append({'key': key, 'icon': icon, 'n': n, 'label': f"{n} {one if n == 1 else many}"})
    return badges
