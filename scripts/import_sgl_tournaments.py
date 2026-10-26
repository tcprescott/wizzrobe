#!/usr/bin/env python3
"""Create or update a community's tournaments from the SGL planning workbook.

The SGL organisers keep every tournament's admins, Challonge bracket, format,
match lengths, deadline and rules link in one published Google Sheet. This
reads its on-site table and makes the community's tournaments match it, through
``TournamentService`` and ``ChallongeService`` so every write is permission-
checked and audited as the acting staff member.

Run inside the app container, from the project root:

    poetry run python scripts/import_sgl_tournaments.py --actor <discord_id>
    poetry run python scripts/import_sgl_tournaments.py --actor <discord_id> --apply

Without ``--apply`` it only prints what it would do. ``--source`` takes the
sheet's published URL (``.../pubhtml``, ``.../pub?output=xlsx``) or a local
``.xlsx`` path; it defaults to the SGL 2026 workbook.

Matching is by tournament name within the tenant (case-insensitive). An existing
tournament gets only the fields that differ, and a blank sheet cell never clears
a field. A tournament with no Challonge link is linked to the sheet's bracket;
one already linked is left alone. Re-running is safe.

Only the published xlsx carries the cell hyperlinks (the rules doc and the
Challonge URL live there, not in the cell text), and openpyxl is not a runtime
dependency, so the workbook is read with the stdlib.
"""

import argparse
import asyncio
import io
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_SOURCE = (
    'https://docs.google.com/spreadsheets/d/e/2PACX-1vT6aXEyhltKeuxUYTHuYxSKN0nF7jpT9kTf98orvKiKg46Xx9GH3RO23O_TUh6qtLczThKAGHcm197x/pubhtml'
)

_NS = {
    'm': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'rel': 'http://schemas.openxmlformats.org/package/2006/relationships',
}
_R_ID = '{%s}id' % _NS['r']

# (name pattern, randomizer, required preset). First match wins, so HMG is
# claimed before plain ALttPR. A required preset must exist on the tenant or the
# row gets no generator: without one, alttpr rolls its casualboots fallback,
# which neither ALttPR tournament races, and dk64r rolls a placeholder settings
# string that upstream rejects. Rows with no match (ALttP NMG, SM Any%, MMR,
# which is rolled offline, and Best of NES) get no generator.
_SEED_GENERATORS: List[Tuple[str, str, Optional[str]]] = [
    (r'link to the past randomizer.*(hybrid|major glitches)', 'alttpr', 'hmg'),
    (r'link to the past randomizer', 'alttpr', 'openboots'),
    (r'donkey kong 64', 'dk64r', 'Season 5 Race Settings'),
    (r'final fantasy randomizer', 'ff1r', None),
    (r'ocarina of time', 'ootr', None),
    (r'super metroid map', 'smmap', None),
    (r'super metroid:? dash', 'smdash', None),
    (r'legend of zelda randomizer', 'z1r', None),
    (r'wind waker', 'wwr', None),
]

# Where a tournament's rules doc and the sheet's "Seed Generation" cell
# disagree, the rules doc is authoritative and wins in the description.
_SEED_GENERATION_OVERRIDES: List[Tuple[str, str]] = [
    # The sheet links v4.8.6; the FFR wiki's SGLive page names v4.9.7 flags,
    # which is what Wizzrobe rolls.
    (r'final fantasy randomizer',
     'https://4-9-7.finalfantasyrandomizer.com/?s=00000000&f=7yYeU3NWYWa-shYkqHmG37-rS90EfpcU'
     'fUjp78ZR6KibBTdXQJnVpIePSloACp-y7pmGE2-q9cgwtGhrPz.mWHn.CIpIv7SBX0Cq6Q-JkRCpdP4JdINmzfSpJn'
     'brJIUV7i9Zc0bReCbdLdiHKyjE6C-v9OEgBo-lQpuYWgo7KPkEA5Q58DLpK5GPOujIdXYCxVNLqv'),
    # The race runs on generatorDev; the stable generator's copy of the preset
    # doesn't make all locations reachable.
    (r'ocarina of time', 'https://ootrandomizer.com/generatorDev'),
    # The sheet links release s8-v2; the rules doc names this build.
    (r'wind waker', 'https://github.com/tanjo3/wwrando/releases/tag/dev_tanjo3.1.10.7.3'),
]

# Sheet typos corrected in the description, as (wrong word, right word).
_TYPO_FIXES: List[Tuple[str, str]] = [
    ('Ansyc', 'Async'),
]


def seed_generation_for(name: str, sheet_value: str) -> str:
    for pattern, value in _SEED_GENERATION_OVERRIDES:
        if re.search(pattern, name, re.IGNORECASE):
            return value
    return sheet_value


def _fix_typos(text: str) -> str:
    for wrong, right in _TYPO_FIXES:
        text = re.sub(rf'\b{wrong}\b', right, text)
    return text


@dataclass
class Cell:
    text: str = ''
    link: Optional[str] = None
    number: Optional[float] = None


@dataclass
class SheetTournament:
    name: str
    fields: Dict[str, Any] = field(default_factory=dict)
    challonge_url: Optional[str] = None
    deadline: Optional[datetime] = None
    required_preset: Optional[str] = None


# --- xlsx reading -----------------------------------------------------------

def _col_index(ref: str) -> int:
    letters = re.match(r'[A-Z]+', ref).group(0)  # type: ignore[union-attr]
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_sheet(xlsx: bytes, sheet_name: str) -> List[List[Cell]]:
    """Rows of ``sheet_name`` as cells carrying text, hyperlink and raw number."""
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        shared: List[str] = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in ElementTree.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', _NS):
                shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % _NS['m'])))

        workbook = ElementTree.fromstring(z.read('xl/workbook.xml'))
        rels = {
            r.get('Id'): r.get('Target')
            for r in ElementTree.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        }
        target = None
        for sheet in workbook.find('m:sheets', _NS):  # type: ignore[union-attr]
            if sheet.get('name') == sheet_name:
                target = rels[sheet.get(_R_ID)].lstrip('/')
                break
        if target is None:
            raise SystemExit(f'No sheet named {sheet_name!r} in the workbook.')
        path = target if target.startswith('xl/') else f'xl/{target}'
        root = ElementTree.fromstring(z.read(path))

        rels_path = f'{path.rsplit("/", 1)[0]}/_rels/{path.rsplit("/", 1)[1]}.rels'
        sheet_rels = {}
        if rels_path in z.namelist():
            sheet_rels = {
                r.get('Id'): r.get('Target') for r in ElementTree.fromstring(z.read(rels_path))
            }
        links = {
            h.get('ref'): sheet_rels.get(h.get(_R_ID))
            for h in root.iter('{%s}hyperlink' % _NS['m'])
        }

        rows: List[List[Cell]] = []
        for row in root.iter('{%s}row' % _NS['m']):
            cells: List[Cell] = []
            for c in row.findall('m:c', _NS):
                ref = c.get('r')
                idx = _col_index(ref)
                while len(cells) <= idx:
                    cells.append(Cell())
                kind = c.get('t')
                value = c.find('m:v', _NS)
                raw = value.text if value is not None else None
                cell = cells[idx]
                if kind == 's' and raw is not None:
                    cell.text = shared[int(raw)]
                elif kind == 'inlineStr':
                    cell.text = ''.join(t.text or '' for t in c.iter('{%s}t' % _NS['m']))
                elif raw is not None:
                    cell.text = raw
                    if kind in (None, 'n'):
                        cell.number = float(raw)
                cell.link = links.get(ref)
            rows.append(cells)
        return rows


def load_workbook_bytes(source: str) -> bytes:
    if not source.startswith(('http://', 'https://')):
        return Path(source).read_bytes()
    import httpx

    url = re.sub(r'/pub(html)?(\?.*)?$', '/pub?output=xlsx', source)
    response = httpx.get(url, follow_redirects=True, timeout=30)
    response.raise_for_status()
    return response.content


# --- row mapping ------------------------------------------------------------

def parse_minutes(text: str) -> Optional[int]:
    """``'90 min'`` → 90; a range like ``'120-150 min'`` → its midpoint."""
    numbers = [int(n) for n in re.findall(r'\d+', text or '')]
    if not numbers:
        return None
    return round(sum(numbers[:2]) / len(numbers[:2]))


def _seed_generator_entry(name: str) -> Tuple[Optional[str], Optional[str]]:
    for pattern, key, preset in _SEED_GENERATORS:
        if re.search(pattern, name, re.IGNORECASE):
            return key, preset
    return None, None


def seed_generator_for(name: str) -> Optional[str]:
    return _seed_generator_entry(name)[0]


def required_preset_for(name: str) -> Optional[str]:
    return _seed_generator_entry(name)[1]


def _excel_datetime(serial: float) -> datetime:
    # Serials are float days, so 17:59 can come back as 17:58:59.99.
    return datetime(1899, 12, 30) + timedelta(minutes=round(serial * 24 * 60))


def _one_line(text: str) -> str:
    return '; '.join(part.strip() for part in text.splitlines() if part.strip())


def parse_tournaments(rows: List[List[Cell]]) -> List[SheetTournament]:
    """The tournaments in the sheet's first table, up to the next header row."""
    header_at = next(
        i for i, row in enumerate(rows) if row and row[0].text.strip() == 'Tournament'
    )
    header = {cell.text.strip(): i for i, cell in enumerate(rows[header_at]) if cell.text.strip()}

    def get(row: List[Cell], column: str) -> Cell:
        i = header.get(column)
        return row[i] if i is not None and i < len(row) else Cell()

    out: List[SheetTournament] = []
    for row in rows[header_at + 1:]:
        name = get(row, 'Tournament').text.strip()
        if not name:
            continue
        if get(row, 'Admin').text.strip() == 'Admin':
            break

        fields: Dict[str, Any] = {}
        fmt = _one_line(get(row, 'Format').text)
        if fmt:
            fields['tournament_format'] = fmt
        avg = parse_minutes(get(row, 'Ave Time').text)
        if avg:
            fields['average_match_duration'] = avg
        worst = parse_minutes(get(row, 'WCS Time').text)
        if worst:
            fields['max_match_duration'] = worst
        if get(row, 'Rules').link:
            fields['rules_url'] = get(row, 'Rules').link
        generator = seed_generator_for(name)
        if generator:
            fields['seed_generator'] = generator

        challonge = get(row, 'Challonge')
        challonge_url = challonge.link or (
            f'https://challonge.com/{challonge.text.strip()}' if challonge.text.strip() else None
        )
        brackets = get(row, 'Brackets')
        if brackets.text.strip() or brackets.link:
            # The bracket runs elsewhere (start.gg for OoTR); the Challonge
            # column is then only a signup page and must not be linked.
            challonge_url = None
        bracket_url = brackets.link or challonge_url
        if bracket_url:
            fields['bracket_url'] = bracket_url

        description = []
        for label, column in (('Admins', 'Admin'), ('Qualifiers', 'Qualifiers'), ('Trophies', 'Trophies')):
            text = _fix_typos(_one_line(get(row, column).text))
            if text and text.lower() != 'no':
                description.append(f'{label}: {text}')
        seed_cell = get(row, 'Seed Generation')
        seed_text = seed_cell.link or seed_cell.text.strip()
        if seed_text and seed_text.upper() != 'N/A':
            seed_text = seed_generation_for(name, seed_text)
            description.append(f'Seed generation: {seed_text}')
        if description:
            fields['description'] = '\n'.join(description)

        deadline_cell = get(row, 'Deadline')
        deadline = _excel_datetime(deadline_cell.number) if deadline_cell.number else None

        out.append(SheetTournament(
            name, fields, challonge_url, deadline, required_preset_for(name),
        ))
    return out


# --- applying ---------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv()

    from tortoise import Tortoise

    from application.event_context import event_scope
    from application.services import ChallongeService, TimezoneService, TournamentService
    from application.services.event_service import EventService
    from application.tenant_context import tenant_scope
    from application.utils.timezone import parse_local_datetime
    from migrations.tortoise_config import TORTOISE_ORM
    from models import Preset, Tenant, Tournament, User

    sheet = parse_tournaments(read_sheet(load_workbook_bytes(args.source), args.sheet))
    mode = 'APPLY' if args.apply else 'DRY RUN'
    print(f'[{mode}] {len(sheet)} tournaments on sheet {args.sheet!r}\n')

    await Tortoise.init(config=TORTOISE_ORM)
    failures = 0
    try:
        tenant = await Tenant.get_or_none(slug=args.tenant)
        if tenant is None:
            raise SystemExit(f'No tenant with slug {args.tenant!r}.')
        actor = await User.get_or_none(discord_id=args.actor)
        if actor is None:
            raise SystemExit(f'No user with discord_id {args.actor!r}.')
        tz = await TimezoneService.tenant_timezone_name(tenant.id)

        # Into the community's default event, the one its board shows.
        event_id = await EventService.default_event_id(tenant.id)
        if event_id is None:
            raise SystemExit(f'{args.tenant!r} has no event yet.')
        with tenant_scope(tenant.id), event_scope(event_id):
            link_challonge = not args.skip_challonge
            if link_challonge and not (await ChallongeService().get_connection_status())['connected']:
                print(f"! Challonge isn't connected for {args.tenant!r}; skipping bracket links. "
                      "Connect it in Admin -> Challonge and re-run.\n")
                link_challonge = False

            existing = {
                t.name.casefold(): t for t in await Tournament.filter(tenant_id=tenant.id, event_id=event_id)
            }
            for entry in sheet:
                wanted = dict(entry.fields)
                if entry.required_preset:
                    preset = await Preset.get_or_none(
                        tenant_id=tenant.id,
                        randomizer=wanted.get('seed_generator'),
                        name=entry.required_preset,
                    )
                    if preset is not None:
                        wanted['preset_id'] = preset.id
                    else:
                        wanted.pop('seed_generator', None)
                        print(f"! {entry.name}: no {entry.required_preset!r} preset on "
                              f"{args.tenant!r}; leaving seeds off. Import built-ins on the "
                              "Presets tab and re-run.")
                if entry.deadline:
                    wanted['signups_close_at'] = parse_local_datetime(
                        entry.deadline.strftime('%Y-%m-%d'), entry.deadline.strftime('%H:%M'), tz=tz,
                    )
                try:
                    tournament = existing.get(entry.name.casefold())
                    if tournament is None:
                        print(f'+ create  {entry.name}')
                        for key, value in wanted.items():
                            print(f'      {key}: {value!r}')
                        if args.apply:
                            tournament = await TournamentService().create_tournament(
                                name=entry.name, actor=actor, **wanted,
                            )
                    else:
                        changed = {
                            k: v for k, v in wanted.items() if getattr(tournament, k) != v
                        }
                        if changed:
                            print(f'~ update  {entry.name} (id {tournament.id})')
                            for key, value in changed.items():
                                print(f'      {key}: {getattr(tournament, key)!r} -> {value!r}')
                            if args.apply:
                                await TournamentService().update_tournament(
                                    tournament, actor=actor, **changed,
                                )
                        else:
                            print(f'= same    {entry.name} (id {tournament.id})')

                    if entry.challonge_url and link_challonge:
                        linked = tournament.challonge_tournament_id if tournament else None
                        if linked:
                            print(f'      challonge: already linked ({tournament.challonge_tournament_url})')
                        else:
                            print(f'      challonge: link {entry.challonge_url}')
                            if args.apply:
                                await ChallongeService().link_tournament(
                                    tournament.id, entry.challonge_url, actor,
                                )
                except ValueError as e:
                    failures += 1
                    print(f'  ! {entry.name}: {e}')
    finally:
        await Tortoise.close_connections()

    if not args.apply:
        print('\nDry run: nothing written. Re-run with --apply to make these changes.')
    if failures:
        print(f'\n{failures} tournament(s) failed; see "!" lines above.')
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--actor', required=True, help="Discord ID of a staff member to act as")
    parser.add_argument('--tenant', default='sgl26', help='Community slug (default: sgl26)')
    parser.add_argument('--source', default=DEFAULT_SOURCE, help='Published sheet URL or local .xlsx')
    parser.add_argument('--sheet', default='Onsite', help='Worksheet name (default: Onsite)')
    parser.add_argument('--skip-challonge', action='store_true', help="Don't link Challonge brackets")
    parser.add_argument('--apply', action='store_true', help='Write changes (default is a dry run)')
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == '__main__':
    main()
