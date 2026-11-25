# AGENTS.md - {name}

Core operating rules for any AI agent, on any machine. Keep this file SHORT
(<= ~3k tokens): it is injected on every turn. Topic detail belongs in
`.github/instructions/<topic>.instructions.md` (auto-attached via `applyTo`
globs) and in `docs/`.

## What this repo is

TODO: two or three sentences. What it does, main language/runtime, how it is run.
Where things live (code / data / state): see `greg.md`.

## Session protocol

- **Owner says "read greg.md"** -> do what `greg.md` says.
- **Start:** if a `WAKE` block is not already in your context, run
  `python scripts/wake.py` first. Do not re-derive git/env state by hand.
- **End / switching machines:** update `SESSION_STATE.md`, then
  `python scripts/hibernate.py "<note>"`.
- **Progress journal** for non-trivial work: `docs/progress/<name>.md`, written as
  you go. If it is only in chat, it does not exist.

## Hard rules

1. Execute, don't ask - unless the action is destructive, the *what* is ambiguous,
   or credentials are needed.
2. Don't commit unless the owner asks; he verifies results first.
3. Bound every tool output (head/tail/ranges).
4. TODO: the 3-5 rules specific to this repo.

## Running things

- TODO: interpreter / how to run tests and the app.

## Routing table - where the detail is

| Working on... | Read |
|---|---|
| setup / moving machines / where things live | `greg.md` |
| TODO area | `docs/...` |
