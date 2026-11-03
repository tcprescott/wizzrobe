/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-settings-discord', path: '/t/sgl/admin/settings/discord' },
  {
    name: 'admin-settings-discord-add',
    path: '/t/sgl/admin/settings/discord',
    act: (p) => p.getByRole('button', { name: 'Add', exact: true }).click(),
    wait: 'text=Discord roles',
  },
  {
    name: 'admin-settings-discord-disconnect',
    path: '/t/sgl/admin/settings/discord',
    act: (p) => p.getByRole('button', { name: 'Disconnect…' }).click(),
    wait: 'role=dialog',
  },
]

export const fixtures = {
  "/api/settings/discord": {
    "linked": true,
    "guild_id": "1000000000000000001",
    "server_name": "SpeedGaming Live",
    "reachable": true,
    "can_manage_roles": true,
    "can_manage_events": true
  },
  "/api/settings/discord/mappings": [
    { "id": 1, "discord_role_id": "2000000000000000001", "discord_role_name": "Staff", "grants": "Staff", "applies_to": "Community-wide", "grants_nothing": false },
    { "id": 2, "discord_role_id": "2000000000000000002", "discord_role_name": "Proctors", "grants": "Proctor", "applies_to": "SGL 2027", "grants_nothing": false },
    { "id": 3, "discord_role_id": "2000000000000000004", "discord_role_name": "Volunteers", "grants": "Volunteer", "applies_to": "No event: grants nothing", "grants_nothing": true },
    { "id": 4, "discord_role_id": "2000000000000000010", "discord_role_name": "ALTTPR Organisers", "grants": "Tournament Admin", "applies_to": "ALTTPR Main", "grants_nothing": false }
  ],
  "/api/settings/discord/mapping-options": {
    "discord_roles": [
      { "id": "2000000000000000010", "name": "ALTTPR Organisers", "color": 15158332 },
      { "id": "2000000000000000002", "name": "Proctors", "color": 3447003 },
      { "id": "2000000000000000001", "name": "Staff", "color": 0 }
    ],
    "grants": [
      { "value": "staff", "label": "Staff", "kind": "community" },
      { "value": "proctor", "label": "Proctor", "kind": "event" },
      { "value": "volunteer", "label": "Volunteer", "kind": "event" },
      { "value": "tournament_admin", "label": "Tournament Admin", "kind": "tournament" },
      { "value": "crew_coordinator", "label": "Crew Coordinator", "kind": "tournament" }
    ],
    "tournaments": [{ "id": 1, "name": "ALTTPR Main" }],
    "events": [{ "id": 1, "name": "SGL 2027" }],
    "default_event_id": 1
  },
  "/api/settings/discord/events": {
    "problem": null,
    "tournaments": [
      { "id": 1, "name": "ALTTPR Main", "enabled": true, "mirrored": 12 },
      { "id": 2, "name": "Super Metroid", "enabled": false, "mirrored": 0 },
      { "id": 3, "name": "OoT Randomizer", "enabled": true, "mirrored": 4 }
    ]
  }
}
