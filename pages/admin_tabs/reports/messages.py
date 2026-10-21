"""Sent messages report.

Every DM Wizzrobe sent in this community and whether it got there: counts on
top, then a server-paged table whose rows expand to the full text and each
channel's result (a card per message on a phone). Staff only, because a DM can
carry a staff-private note.
"""

from typing import Any, Optional

from nicegui import app, ui

from application.services import AuthService, SentMessageService, get_user_from_session
from application.utils.dm_text import readable_dm_text
from application.utils.timezone import (
    format_local_datetime,
    format_local_time,
    timezone_label,
)
from models import SentMessage, SentMessageStatus
from theme.section import section_panel
from theme.tables.export import csv_export_button
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

from .shared import (
    _page_range_label,
    date_range_filter,
    default_date_range,
    display_bounds,
    event_jump_options,
    filter_field,
    is_custom_range,
    kpi_card,
    kpi_grid,
    navigate_with_params,
    parse_int,
    report_filter_bar,
    report_jumps,
    report_page_shell,
)

TITLE = 'Sent messages'
SUBTITLE = 'Every DM Wizzrobe sent in this community, and whether it got there.'
PAGE_SIZE = 50

# ``state`` is the Reports tab's own filter param; this report reads it as the
# Problems only switch, and ``action`` as the search text, like the Audit Log.
_PROBLEMS = 'problems'


# (chip class, icon, label)
STATUS_CHIP = {
    SentMessageStatus.PENDING: ('wiz-chip--pending', 'schedule', 'Sending'),
    SentMessageStatus.DELIVERED: ('wiz-chip--ok', 'check_circle', 'Delivered'),
    SentMessageStatus.DISCORD_OFF: ('wiz-chip--neutral', 'do_not_disturb_on', 'Discord off'),
    SentMessageStatus.BLOCKED: ('wiz-chip--cancelled', 'block', 'Blocked DMs'),
    SentMessageStatus.FAILED: ('wiz-chip--cancelled', 'error', 'Failed'),
}


def _discord_line(m: SentMessage) -> dict:
    status = m.status
    if status == SentMessageStatus.DELIVERED:
        text = 'Discord: delivered silently' if m.silent else 'Discord: delivered'
        return {'icon': 'check_circle', 'cls': 'st-ok', 'text': text}
    if status == SentMessageStatus.DISCORD_OFF:
        return {'icon': 'do_not_disturb_on', 'cls': 'st-neutral',
                'text': 'Discord: not sent, they turned Discord DMs off in their profile'}
    if status == SentMessageStatus.BLOCKED:
        return {'icon': 'block', 'cls': 'st-cancelled',
                'text': "Discord refused: this person doesn't accept DMs from the bot"}
    if status == SentMessageStatus.FAILED:
        return {'icon': 'error', 'cls': 'st-cancelled', 'text': f'Discord failed: {m.error or "unknown error"}'}
    return {'icon': 'schedule', 'cls': 'st-pending', 'text': 'Discord: still sending'}


def _delivery_lines(m: SentMessage) -> list[dict]:
    lines = [_discord_line(m)]
    if m.push_devices:
        noun = 'device' if m.push_devices == 1 else 'devices'
        lines.append({'icon': 'check_circle', 'cls': 'st-ok',
                      'text': f'Phone notification sent to {m.push_devices} {noun}'})
    else:
        lines.append({'icon': 'remove_circle_outline', 'cls': 'st-neutral',
                      'text': 'No phone notification (no devices turned on)'})
    for label, sent in (('Email', m.email_sent), ('Telegram', m.telegram_sent)):
        if sent is True:
            lines.append({'icon': 'check_circle', 'cls': 'st-ok', 'text': f'{label}: sent'})
        elif sent is False:
            lines.append({'icon': 'error', 'cls': 'st-cancelled', 'text': f'{label}: failed'})
    return lines


def _row(m: SentMessage) -> dict[str, Any]:
    chip_cls, chip_icon, chip_label = STATUS_CHIP[m.status]
    who = m.recipient.preferred_name if m.recipient else f'Discord {m.discord_id}'
    body = readable_dm_text(m.body).strip()
    preview = body
    if m.title and preview.startswith(m.title):
        preview = preview[len(m.title):].lstrip(' :.\n')
    return {
        'id': m.id,
        'time': f"{format_local_datetime(m.created_at, '%a')} {format_local_time(m.created_at)}",
        'when': format_local_datetime(m.created_at, '%a %d %b %Y, %H:%M:%S %Z'),
        'to': who,
        'title': readable_dm_text(m.title) or '(no title)',
        'preview': preview.splitlines()[0][:160] if preview else '',
        'body': body,
        'status': chip_label,
        'status_cls': chip_cls,
        'status_icon': chip_icon,
        'silent': m.silent,
        'link_label': m.link_label or '',
        'link_url': m.link_url or '',
        'delivery': _delivery_lines(m),
        'kind': m.notice_key or '',
    }


# Shared by the desktop expanded row and the phone card. Values arrive as row
# data and render through Vue interpolation, so DM text is never markup.
_DETAIL = r'''
<div class="wiz-sent__detail">
    <div class="wiz-sent__body">{{ props.row.body }}</div>
    <div v-for="(line, i) in props.row.delivery" :key="i" class="wiz-sent__line">
        <q-icon :name="line.icon" :class="line.cls" size="18px" />
        <span>{{ line.text }}</span>
    </div>
    <div class="wiz-sent__meta">{{ props.row.when }}<span v-if="props.row.kind"> · {{ props.row.kind }}</span></div>
    <q-btn v-if="props.row.link_url" flat dense no-caps color="primary" icon="open_in_new"
           :label="props.row.link_label || 'Open link'" :href="props.row.link_url"
           target="_blank" rel="noopener" @click.stop />
</div>
'''

_RESULT = r'''
<div class="wiz-sent__chips">
    <span :class="'wiz-chip ' + props.row.status_cls">
        <q-icon :name="props.row.status_icon" size="14px" />{{ props.row.status }}
    </span>
    <span v-if="props.row.silent" class="wiz-chip wiz-chip--neutral">
        <q-icon name="notifications_off" size="14px" />Silent
    </span>
</div>
'''

_MESSAGE = r'''
<div class="wiz-sent__msg">
    <div class="wiz-sent__title">{{ props.row.title }}</div>
    <div v-if="!props.expand" class="wiz-sent__preview">{{ props.row.preview }}</div>
</div>
'''

_BODY = (
    r'''
<q-tr :props="props" class="wiz-sent__row" @click="props.expand = !props.expand">
    <q-td v-for="col in props.cols" :key="col.name" :props="props">
        <template v-if="col.name === 'message'">
            <div class="wiz-sent__msg-row">'''
    + _MESSAGE
    + r'''<q-btn flat dense round size="sm" color="primary"
                   :icon="props.expand ? 'expand_less' : 'expand_more'"
                   :aria-label="props.expand ? 'Hide details' : 'Show details'"
                   :aria-expanded="props.expand ? 'true' : 'false'"
                   @click.stop="props.expand = !props.expand" />
            </div>
            <div v-if="props.expand">''' + _DETAIL + r'''</div>
        </template>
        <template v-else-if="col.name === 'result'">''' + _RESULT + r'''</template>
        <template v-else-if="col.name === 'to'"><b>{{ col.value }}</b></template>
        <template v-else>{{ col.value }}</template>
    </q-td>
</q-tr>
'''
)

# Phone card: the message field carries the whole detail behind a tap, so the
# card reads like the expanded desktop row.
_CARD_MESSAGE = (
    r'''<div @click.stop="props.expand = !props.expand" class="wiz-sent__card-msg">'''
    + _MESSAGE
    + r'''<q-btn flat dense no-caps color="primary" size="sm"
       :label="props.expand ? 'Hide details' : 'Details'"
       :icon="props.expand ? 'expand_less' : 'expand_more'" />
<div v-if="props.expand">''' + _DETAIL + r'''</div></div>'''
)


async def messages_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    state: Optional[str] = None,
    page: Optional[int] = None,
    all_reports_link: bool = True,
    **_unused,
) -> None:
    actor = await get_user_from_session(app.storage.user)
    if actor is None or not await AuthService.is_staff(actor):
        with report_page_shell(TITLE, SUBTITLE, all_reports_link=all_reports_link):
            ui.label('Only Staff can view sent messages.').classes('text-error')
        return
    start_d, end_d = await default_date_range(start, end)
    user_id_int = parse_int(user_id)
    page_int = max(1, parse_int(page) or 1)
    search = (action or '').strip()
    problems = state == _PROBLEMS

    def nav(**overrides) -> None:
        params: dict[str, Any] = {
            'report': 'messages',
            'start': start_d, 'end': end_d,
            'user_id': user_id_int,
            'action': search or None,
            'state': _PROBLEMS if problems else None,
        }
        params.update(overrides)
        navigate_with_params(**params)

    active = (
        int(await is_custom_range(start_d, end_d)) + int(bool(search))
        + int(problems) + int(user_id_int is not None)
    )
    service = SentMessageService()
    bounds_start, bounds_end = display_bounds(start_d, end_d)
    query: dict[str, Any] = dict(
        actor=actor, start=bounds_start, end=bounds_end,
        problems_only=problems, search=search or None, recipient_id=user_id_int,
    )
    totals = await service.totals(actor=actor, start=bounds_start, end=bounds_end)
    total = await service.count_messages(**query)
    messages = await service.list_messages(**query, limit=PAGE_SIZE, offset=(page_int - 1) * PAGE_SIZE)
    rows = [_row(m) for m in messages]

    with report_page_shell(TITLE, SUBTITLE, all_reports_link=all_reports_link):
        with kpi_grid():
            kpi_card('Sent', f"{totals['sent']:,}", 'in this window')
            kpi_card('Silent', f"{totals['silent']:,}", 'delivered without a ping')
            kpi_card('Discord off or blocked', f"{totals['off_or_blocked']:,}",
                     'their phones may still have been notified')
            kpi_card('Failed', f"{totals['failed']:,}", 'Discord errors and timeouts',
                     color='negative' if totals['failed'] else 'primary')

        with report_filter_bar(active):
            date_range_filter(start_d, end_d, on_change=lambda s, e: nav(start=s, end=e, page=None))
            filter_field(ui.switch(
                'Problems only', value=problems,
                on_change=lambda e: nav(state=_PROBLEMS if e.value else None, page=None),
            ))
            search_input = filter_field(ui.input('Search', value=search,
                                                 placeholder='Person or message text'))

            def _on_search(_e):
                nav(action=(search_input.value or '').strip() or None, page=None)
            search_input.on('blur', _on_search)
            search_input.on('keydown.enter', _on_search)
            if user_id_int is not None:
                ui.button('Show everyone', icon='close',
                          on_click=lambda: nav(user_id=None, page=None)) \
                    .props('flat dense no-caps color=primary')
            report_jumps(await event_jump_options(), start_d, end_d,
                         on_pick=lambda s, e: nav(start=s, end=e, page=None))

        panel = await section_panel(
            'Messages', icon='mail',
            subtitle=_page_range_label(total, page_int, PAGE_SIZE, 'messages'),
        )
        columns = [
            {'name': 'time', 'label': f'Time ({timezone_label()})', 'field': 'time', 'align': 'left'},
            {'name': 'to', 'label': 'To', 'field': 'to', 'align': 'left'},
            {'name': 'message', 'label': 'Message', 'field': 'title', 'align': 'left'},
            {'name': 'result', 'label': 'Result', 'field': 'status', 'align': 'left'},
        ]
        csv_columns = [
            {'name': 'when', 'label': f'When ({timezone_label()})', 'field': 'when'},
            {'name': 'to', 'label': 'To', 'field': 'to'},
            {'name': 'title', 'label': 'Message', 'field': 'title'},
            {'name': 'body', 'label': 'Text', 'field': 'body'},
            {'name': 'status', 'label': 'Result', 'field': 'status'},
            {'name': 'silent', 'label': 'Silent', 'field': 'silent'},
        ]
        with panel.aside:
            csv_export_button(
                f'sent-messages-page-{page_int}-{start_d}-to-{end_d}',
                lambda: csv_columns, lambda: rows,
            )
        with panel.body:
            if not rows:
                ui.label('No messages match these filters.').classes('text-secondary q-pa-md')
                return
            table = ui.table(columns=columns, rows=rows, row_key='id').classes('full-width wiz-sent')
            table.add_slot('body', _BODY)
            # pager=False: this table pages on the server with the buttons below.
            enable_mobile_grid(
                table, columns, table_key=TableKeys.REPORTS_SENT_MESSAGES,
                field_slots={'message': _CARD_MESSAGE, 'result': _RESULT},
                pager=False,
            )
            _pager(total, page_int, nav)


def _pager(total: int, page: int, nav) -> None:
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    with ui.row().classes('items-center q-mt-sm'):
        ui.label(f'Page {page} of {total_pages}').classes('text-caption')
        ui.button(
            'Previous', icon='chevron_left', on_click=lambda: nav(page=page - 1),
        ).props('flat dense').set_enabled(page > 1)
        ui.button(
            'Next', icon='chevron_right', on_click=lambda: nav(page=page + 1),
        ).props('flat dense').set_enabled(page < total_pages)
