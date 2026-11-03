/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
const PATH = "/t/sgl/admin/event-settings/discord-roles";

export const targets = [
  { name: "admin-event-settings-discord-roles", path: PATH },
  {
    name: "admin-event-settings-discord-roles-pick",
    path: PATH,
    act: (p) => p.getByRole("button", { name: /^OoT Randomizer/ }).click(),
    wait: "role=dialog",
  },
  {
    name: "admin-event-settings-discord-roles-who",
    path: PATH,
    act: (p) => p.getByRole("button", { name: "See who" }).click(),
    wait: "role=dialog",
  },
];

const line = (group, kind, label, caption, role, extra = {}) => ({
  group,
  kind,
  tournament_id: null,
  lanyard: null,
  label,
  caption,
  active: true,
  discord_role_id: role ? role[0] : null,
  discord_role_name: role ? role[1] : null,
  holding: role ? role[2] : null,
  problem: null,
  ...extra,
});

export const fixtures = {
  "/api/event-settings/discord-roles": {
    last_synced_at: "{{now-3m}}",
    last_error: null,
    in_place: 577,
    unreachable: [
      { name: "Link", reason: "no_discord" },
      { name: "Zelda", reason: "no_discord" },
      { name: "Ganon", reason: "not_in_server" },
    ],
    roles: [
      {
        id: "3000000000000000001",
        name: "Player",
        color: "#3ba55c",
        problem: null,
      },
      {
        id: "3000000000000000002",
        name: "Live now",
        color: "#ed4245",
        problem: null,
      },
      {
        id: "3000000000000000003",
        name: "VIP",
        color: "#f1c40f",
        problem: null,
      },
      {
        id: "3000000000000000004",
        name: "Base",
        color: "#5865f2",
        problem: null,
      },
      {
        id: "3000000000000000005",
        name: "ALTTPR",
        color: "#1abc9c",
        problem: null,
      },
      {
        id: "3000000000000000006",
        name: "Super Metroid",
        color: "#e67e22",
        problem:
          "This role is above the bot's own role. Drag the Wizzrobe role above it in Server Settings → Roles.",
      },
      {
        id: "3000000000000000007",
        name: "Moderator",
        color: null,
        problem:
          "This role is above the bot's own role. Drag the Wizzrobe role above it in Server Settings → Roles.",
      },
    ],
    roles_error: null,
    lines: [
      line(
        "players",
        "any_tournament",
        "Enrolled in any tournament",
        "Active tournaments only",
        ["3000000000000000001", "Player", 184],
      ),
      line(
        "players",
        "live_match",
        "In a match right now",
        "Added when the match starts, taken back when it finishes",
        ["3000000000000000002", "Live now", 6],
      ),
      line(
        "badges",
        "badge",
        "VIP badge",
        "Given when the badge is bought, taken back on a refund",
        ["3000000000000000003", "VIP", 22],
        { lanyard: "vip" },
      ),
      line(
        "badges",
        "badge",
        "Base badge",
        "Given when the badge is bought, taken back on a refund",
        ["3000000000000000004", "Base", 301],
        { lanyard: "base" },
      ),
      line(
        "badges",
        "badge",
        "Day pass",
        "Given when the badge is bought, taken back on a refund",
        null,
        { lanyard: "day_pass" },
      ),
      line(
        "tournaments",
        "tournament",
        "ALTTPR Main",
        null,
        ["3000000000000000005", "ALTTPR", 64],
        { tournament_id: 1 },
      ),
      line(
        "tournaments",
        "tournament",
        "Super Metroid",
        null,
        ["3000000000000000006", "Super Metroid", null],
        {
          tournament_id: 2,
          problem:
            "The bot's role is below @Super Metroid. Drag the Wizzrobe role above it in Server Settings → Roles.",
        },
      ),
      line("tournaments", "tournament", "OoT Randomizer", null, null, {
        tournament_id: 3,
      }),
    ],
  },
};
