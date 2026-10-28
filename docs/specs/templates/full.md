# <Feature name>

Status: Draft · Size: full · PRs: — · Requested: <date>, <who>

## Problem

Who runs into this, what they do today, and why that's not good enough. Two to
four sentences.

## Scope

In:
- …

Out:
- …

## Flow

What each kind of person does, step by step, from where they start to the
result. One short list per role (player, volunteer, staff…). Name the page,
button or DM at each step.

1. …

## Mockup pick

Which option was picked and a line on what it looks like. Images:
`/mnt/project-files/<topic>/mockups/<option>-{phone,desktop}-{light,dark}.png`.

## Data and permissions

- **Feature flag:** yes (`FeatureFlag.X`, off by default) / no.
- **Models:** new or changed models and fields; community-owned or event-owned
  (`tenant` / `event` FKs); anything formerly global that becomes per-community.
- **Who can see / do what:** roles per action, and what a non-member or a
  signed-out visitor gets.
- **Other surfaces:** REST, MCP, GraphQL, workers, Discord DMs (catalogue
  notice + where the DM's link lands), webhooks/events, audit actions.
- **Player input:** what players type and how it's limited (see the project
  threat model).

## PR plan

One line per PR, in order, with the acceptance items it covers. Note seed rows,
help articles, changelog files and docs each PR owes.

1. …

## Acceptance

Observable statements someone can check in the running app or a test. Include
phone and desktop when there's UI.

- [ ] …
- [ ] On a phone, …
- [ ] On desktop, …

## Open questions

Empty once approved.

## Decisions

- <YYYY-MM-DD> — <who> picked <option>: <one line on what that means>.
