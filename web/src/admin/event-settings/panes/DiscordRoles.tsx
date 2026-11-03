import { useState } from "react";
import "./discord-roles.css";
import { api, unwrap, type Schemas } from "../../../api/client";
import {
  Banner,
  Button,
  Dialog,
  FormDialog,
  Icon,
  RowMenu,
  Spinner,
} from "../../../components";
import type { IconName } from "../../../components";
import { effectiveZone, formatDayTime } from "../../../lib/time";
import { ListCard, LoadError } from "../../kit";
import { useEventParams, useEventSetting } from "../data";

type Overview = Schemas["RoleSyncOverviewResponse"];
type Line = Schemas["RoleSyncLine"];
type Role = Schemas["RoleSyncRole"];
type Result = Schemas["RoleSyncResultResponse"];

const GROUPS: { key: Line["group"]; title: string; icon: IconName }[] = [
  { key: "players", title: "Players", icon: "sports_esports" },
  { key: "badges", title: "Badges", icon: "badge" },
  { key: "tournaments", title: "Tournaments", icon: "trophy" },
];

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;

function ago(iso: string): string {
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  return formatDayTime(iso, effectiveZone(null));
}

function lineKey(line: Line): string {
  return `${line.kind}:${line.tournament_id ?? ""}:${line.lanyard ?? ""}`;
}

function RoleDot({ color }: { color?: string | null }) {
  return (
    <span
      className="role-dot"
      style={color ? { background: color } : undefined}
      aria-hidden="true"
    />
  );
}

function StatusCard({
  data,
  onSync,
  onWho,
  onClear,
  busy,
}: {
  data: Overview;
  onSync: () => void;
  onWho: () => void;
  onClear: () => void;
  busy: boolean;
}) {
  const broken = data.lines.filter(
    (l) => l.discord_role_id && l.problem,
  ).length;
  const people = data.unreachable;
  const noDiscord = people.filter((p) => p.reason === "no_discord").length;
  const notInServer = people.length - noDiscord;
  let icon: IconName = "check_circle";
  let tone = "settings-status--ok";
  let title: string;
  const parts: string[] = [];
  if (!data.last_synced_at) {
    title = data.last_error ? "The last sync didn't finish" : "Not synced yet";
    parts.push(
      data.last_error ??
        "Pick a role on a line below and Wizzrobe syncs within a few seconds.",
    );
    if (data.last_error) {
      icon = "error";
      tone = "role-status--bad";
    } else {
      icon = "info";
      tone = "settings-status--muted";
    }
  } else {
    title = `Last synced ${ago(data.last_synced_at)} · ${plural(data.in_place, "role")} in place`;
    if (data.last_error) {
      icon = "error";
      tone = "role-status--bad";
      parts.push(data.last_error);
    }
    if (broken)
      parts.push(
        `${plural(broken, "rule needs", "rules need")} fixing, shown in red.`,
      );
    if (people.length) {
      const why = [
        noDiscord &&
          `${noDiscord} ${noDiscord === 1 ? "has" : "have"} no Discord linked`,
        notInServer &&
          `${notInServer} ${notInServer === 1 ? "isn't" : "aren't"} in the server`,
      ].filter(Boolean);
      parts.push(
        `${plural(people.length, "person", "people")} can't get roles: ${why.join(", ")}.`,
      );
    }
    if (!data.last_error && (broken || people.length)) {
      icon = "warning";
      tone = "settings-status--warn";
    }
  }
  return (
    <section className="panel settings-card" aria-label="Sync status">
      <div className="settings-list__row role-status">
        <Icon name={icon} size={20} className={tone} />
        <span className="settings-row__text">
          <span className="settings-row__title">{title}</span>
          {parts.length > 0 && (
            <span className="settings-row__sub">
              {parts.join(" ")}{" "}
              {people.length > 0 && (
                <button
                  type="button"
                  className="settings-link-button"
                  onClick={onWho}
                >
                  See who
                </button>
              )}
            </span>
          )}
        </span>
        <span className="role-status__actions">
          <Button
            variant="outline"
            icon="sync"
            onClick={onSync}
            disabled={busy || !!data.roles_error}
          >
            <span className="role-status__label">
              {busy ? "Syncing…" : "Sync now"}
            </span>
          </Button>
          <RowMenu
            label="More for Discord roles"
            items={[
              {
                label: "People who can't get roles",
                icon: "person_off",
                onSelect: onWho,
                disabled: people.length === 0,
              },
              {
                label: "Remove every role Wizzrobe gave…",
                icon: "delete",
                destructive: true,
                onSelect: onClear,
              },
            ]}
          />
        </span>
      </div>
    </section>
  );
}

function LineRow({
  line,
  roles,
  onPick,
  locked,
}: {
  line: Line;
  roles: Role[];
  onPick: () => void;
  locked: boolean;
}) {
  const role = roles.find((r) => r.id === line.discord_role_id);
  const on = !!line.discord_role_id;
  return (
    <li>
      <button
        type="button"
        className="settings-list__row role-line"
        onClick={onPick}
        disabled={locked}
        aria-label={`${line.label}: ${on ? `@${line.discord_role_name}` : "off"}. Change role`}
      >
        <span className="settings-row__text">
          <span className="settings-row__title">{line.label}</span>
          {line.caption && (
            <span className="settings-row__sub">{line.caption}</span>
          )}
          {on && line.problem && (
            <span className="role-problem">
              <Icon name="error" size={16} />
              <span>{line.problem}</span>
            </span>
          )}
        </span>
        <span className="role-line__value">
          {on ? (
            <span className="role-chip">
              <RoleDot color={role?.color} />@{line.discord_role_name}
            </span>
          ) : (
            <span className="settings-row__sub">Off</span>
          )}
          {on && !line.problem && line.holding != null && (
            <span className="settings-row__sub">{line.holding} now</span>
          )}
        </span>
        <Icon
          name="chevron_right"
          size={20}
          className="settings-status--muted"
        />
      </button>
    </li>
  );
}

function PickDialog({
  line,
  roles,
  onClose,
  onSaved,
}: {
  line: Line;
  roles: Role[];
  onClose: () => void;
  onSaved: (note: string) => void;
}) {
  const params = useEventParams();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const current = line.discord_role_id ?? "";
  const known = roles.some((r) => r.id === current);
  const choices: Role[] =
    current && !known
      ? [
          {
            id: current,
            name: line.discord_role_name ?? "Deleted role",
            color: null,
            problem: null,
          },
          ...roles,
        ]
      : roles;

  async function pick(roleId: string) {
    if (roleId === current) {
      onClose();
      return;
    }
    setBusy(true);
    setFailure(null);
    try {
      const r = await api.PUT("/api/event-settings/discord-roles/rules", {
        ...params,
        body: {
          kind: line.kind,
          tournament_id: line.tournament_id ?? null,
          lanyard: line.lanyard ?? null,
          discord_role_id: roleId || null,
        },
      });
      if (!r.response.ok) unwrap(r);
      onSaved(
        roleId
          ? `${line.label}: saved. Wizzrobe hands the role out within a few seconds.`
          : `${line.label}: turned off. Wizzrobe takes the role back on the next sync.`,
      );
    } catch (err) {
      setFailure(message(err));
      setBusy(false);
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && !busy && onClose()}
      title={`Role for ${line.label}`}
      size="sm"
    >
      <fieldset className="settings-radios role-pick" disabled={busy}>
        <legend className="visually-hidden">
          Discord role for {line.label}
        </legend>
        <label className="settings-row settings-row--radio">
          <input
            type="radio"
            name="role"
            checked={!current}
            onChange={() => void pick("")}
          />
          <Icon name="block" size={20} className="settings-status--muted" />
          <span className="settings-row__text">
            <span className="settings-row__title">No role</span>
            <span className="settings-row__sub">Turns this line off</span>
          </span>
        </label>
        {choices.map((r) => {
          const blocked = !!r.problem && r.id !== current;
          return (
            <label
              key={r.id}
              className={`settings-row settings-row--radio${blocked ? " settings-row--locked" : ""}`}
            >
              <input
                type="radio"
                name="role"
                checked={r.id === current}
                disabled={blocked}
                onChange={() => void pick(r.id)}
              />
              <RoleDot color={r.color} />
              <span className="settings-row__text">
                <span className="settings-row__title">@{r.name}</span>
                {r.problem && (
                  <span className="settings-row__sub">{r.problem}</span>
                )}
              </span>
              {blocked && (
                <Icon
                  name="lock"
                  size={18}
                  className="settings-status--muted"
                />
              )}
            </label>
          );
        })}
      </fieldset>
      {busy && <Spinner label="Saving" />}
      {failure && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
    </Dialog>
  );
}

function WhoDialog({
  people,
  onClose,
}: {
  people: Overview["unreachable"];
  onClose: () => void;
}) {
  const sections = [
    {
      title: "No Discord account linked",
      hint: "They signed in another way. Linking Discord on their profile fixes it.",
      names: people.filter((p) => p.reason === "no_discord").map((p) => p.name),
    },
    {
      title: "Not in the Discord server",
      hint: "Their Discord account is linked but not in this server.",
      names: people
        .filter((p) => p.reason === "not_in_server")
        .map((p) => p.name),
    },
  ].filter((s) => s.names.length > 0);
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="People who can't get roles"
      size="sm"
      footer={
        <>
          <span className="dialog__spacer" />
          <Button onClick={onClose}>Done</Button>
        </>
      }
    >
      {sections.map((s) => (
        <div key={s.title} className="role-who">
          <h3 className="settings-row__title">
            {s.title} ({s.names.length})
          </h3>
          <p className="settings-note">{s.hint}</p>
          <ul className="role-who__names">
            {s.names.map((n, i) => (
              <li key={`${n}-${i}`}>{n}</li>
            ))}
          </ul>
        </div>
      ))}
    </Dialog>
  );
}

/** Event settings → Discord Roles from Wizzrobe: a checklist of rules, each opening a role picker (layout C). */
export function DiscordRoles() {
  const params = useEventParams();
  const { data, error, reload } = useEventSetting("discord-roles", () =>
    api.GET("/api/event-settings/discord-roles", params).then(unwrap),
  );
  const [picking, setPicking] = useState<Line | null>(null);
  const [who, setWho] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [onlyOn, setOnlyOn] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  if (error && !data) return <LoadError error={error} onRetry={reload} />;
  if (!data) return <Spinner label="Loading Discord roles" />;

  const describe = (r: Result, done: string) =>
    r.error
      ? null
      : `${done}: ${r.added} added, ${r.removed} removed, ${r.in_place} in place.`;

  async function run(
    path:
      | "/api/event-settings/discord-roles/sync"
      | "/api/event-settings/discord-roles/clear",
  ) {
    setBusy(true);
    setFailure(null);
    setNote(null);
    try {
      const r = await api.POST(path, params).then(unwrap);
      if (r.error) setFailure(r.error);
      else
        setNote(
          describe(
            r,
            path.endsWith("clear")
              ? "Every role Wizzrobe gave is gone"
              : "Synced",
          ),
        );
      setClearing(false);
      reload();
    } catch (err) {
      setFailure(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-stack">
      <StatusCard
        data={data}
        busy={busy}
        onSync={() => void run("/api/event-settings/discord-roles/sync")}
        onWho={() => setWho(true)}
        onClear={() => setClearing(true)}
      />
      {data.roles_error && (
        <Banner tone="warning">
          Couldn't read the server's roles, so lines can't change right now.{" "}
          {data.roles_error}
        </Banner>
      )}
      {note && (
        <p className="settings-saved" role="status">
          {note}
        </p>
      )}
      {failure && !clearing && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
      {GROUPS.map((g) => {
        const all = data.lines.filter((l) => l.group === g.key);
        if (all.length === 0 && g.key !== "tournaments") return null;
        const shown =
          g.key === "tournaments" && onlyOn
            ? all.filter((l) => l.discord_role_id)
            : all;
        return (
          <ListCard
            key={g.key}
            title={g.title}
            icon={g.icon}
            subtitle={
              g.key === "tournaments"
                ? "This event's active tournaments"
                : undefined
            }
            action={
              g.key === "tournaments" ? (
                <label className="settings-list__switch role-only">
                  <input
                    type="checkbox"
                    role="switch"
                    className="switch"
                    checked={onlyOn}
                    onChange={(e) => setOnlyOn(e.target.checked)}
                  />
                  <span className="settings-row__sub">
                    Only ones with a role
                  </span>
                </label>
              ) : undefined
            }
          >
            {shown.length === 0 ? (
              <div className="settings-card__body">
                <p className="settings-note">
                  {onlyOn
                    ? "No tournaments have a role yet."
                    : "This event has no active tournaments."}
                </p>
              </div>
            ) : (
              <ul className="settings-list">
                {shown.map((line) => (
                  <LineRow
                    key={lineKey(line)}
                    line={line}
                    roles={data.roles}
                    locked={!!data.roles_error}
                    onPick={() => setPicking(line)}
                  />
                ))}
              </ul>
            )}
          </ListCard>
        );
      })}
      {picking && (
        <PickDialog
          line={picking}
          roles={data.roles}
          onClose={() => setPicking(null)}
          onSaved={(n) => {
            setPicking(null);
            setFailure(null);
            setNote(n);
            reload();
          }}
        />
      )}
      {who && (
        <WhoDialog people={data.unreachable} onClose={() => setWho(false)} />
      )}
      <FormDialog
        open={clearing}
        onOpenChange={setClearing}
        title="Remove every role Wizzrobe gave?"
        description="This turns every line off and takes back every Discord role Wizzrobe gave. Roles a moderator handed out by hand stay put."
        primaryLabel="Remove roles"
        destructive
        busy={busy}
        busyLabel="Removing roles…"
        onPrimary={() => void run("/api/event-settings/discord-roles/clear")}
        size="sm"
      >
        {failure && (
          <p className="form-error" role="alert">
            {failure}
          </p>
        )}
      </FormDialog>
    </div>
  );
}
