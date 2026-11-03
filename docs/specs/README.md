# Specs

A spec is the short, agreed description of a feature before it's built: who it's
for, what's in and out, the mockup that was picked, and a list of things that
must be true when it ships. It's written once, signed off once, kept accurate
while the work is in flight, and deleted when the feature ships.

The process is deliberately light. Most asks get a one-screen **lite** spec;
only bigger work gets the **full** one.

## When a spec is needed

| Needs a spec | Skips it |
|---|---|
| A new feature | A bug fix |
| A change to how someone does a task (a workflow, a permission, who gets notified) | Copy and wording tweaks |
| | Small visual polish that doesn't change what anyone can do |
| | Refactors, tests, CI, dependency bumps, docs-only changes |

If you can't tell, write a lite spec. It's a few minutes of work.

## Lite or full

Start from [templates/lite.md](templates/lite.md). Use
[templates/full.md](templates/full.md) when the work does any of:

- adds or changes a model (so a migration),
- needs more than one PR,
- adds a feature flag, a new outside integration, or a new role or permission,
- adds a REST/MCP surface, a worker, or a new Discord DM.

The full template adds three sections to the lite one: **Flow**, **Data and
permissions**, and a **PR plan**. Same headings either way, so every spec reads
the same.

## Lifecycle

1. **Scope.** Whoever does the work (the project thread, or a Claude Code
   session) asks the scoping questions first, one per question card with a
   recommendation marked. The feature-flag question (CLAUDE.md step 0) is one of
   them.
2. **Draft.** Write the spec where the back-and-forth happens. In the
   Wizzrobe project that's `/mnt/project-files/<topic>/spec.md`, next to the
   mockups. Outside the project, write it straight to `docs/specs/<topic>.md` on
   a draft PR. Every answer goes into the **Decisions** log with its date.
3. **Mockups.** Phone and desktop, light and dark where relevant, saved under
   `<topic>/mockups/`. These are part of the spec, not a separate step.
4. **Sign-off (the only one).** Show the spec and the mockups together. The
   pick approves both: the mockup choice goes into **Mockup pick**, open
   questions must be empty, and the status becomes `Approved`.
5. **Commit it.** The first commit of the feature's draft PR copies the spec to
   `docs/specs/<topic>.md` (status `Building`, PR number filled in).
6. **Build, keeping the spec true.** When the plan changes mid-build because
   the person asked for it, edit the spec in the same PR and add a dated line to
   **Decisions**. Their ask is the approval; no new sign-off. The spec and the
   code never disagree on main.
7. **Check before leaving draft.** The PR description has an **Acceptance**
   section that copies every acceptance item and ticks it, each with how it was
   checked (a test name, a screenshot, a page exercised). An item that can't be
   met is either fixed or moved to **Scope → Out** with the person's OK, logged
   in **Decisions**. A PR doesn't leave draft with an unticked item.
8. **Ship and delete.** The PR that finishes the feature folds anything that
   describes lasting behaviour into the feature doc (`docs/features/`, or the
   reference docs) and deletes the spec. Git history keeps it. For a multi-PR
   feature the spec stays until the last PR in its **PR plan**, and each PR's
   description says which acceptance items it covers.

## How it fits the other rules

- **Mockups first** is step 3. The mockup pick and the spec sign-off are the
  same moment.
- **Phone and desktop stay one design.** Every spec with UI has at least one
  acceptance item for phone and one for desktop.
- **Draft PRs** are unchanged: a PR opens as a draft and leaves draft only when
  CI is green and every acceptance item is ticked.
- **Changelog, seed rows, help articles** are still per-PR obligations from
  CLAUDE.md. List them in the PR plan of a full spec so they aren't forgotten.
- **`/plan-feature`** writes the spec file from these templates; `/add-feature`
  builds against it.

## Naming

`docs/specs/<topic>.md`, kebab-case, the same `<topic>` as the project-files
folder (`post-event-survey`, `bo3-sets`). One spec per feature, even when it
spans several PRs.
