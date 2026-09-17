# Decisions

Why the plugin is shaped the way it is. Each entry is an arbitrage that was settled once, with the
reason that settled it, so it is not re-litigated or silently undone.

What is deliberately left undone sits at the end, under [Deferred](#deferred). Defects measured in
the scripts live in [`BACKLOG.md`](BACKLOG.md); interfaces and incident memory in
[`CONTRACTS.md`](CONTRACTS.md).

**Writing an entry.** The reason is what was observed, never where it was observed. No entity, no
correspondent, no amount, no path from a real workspace: the repository is public, and the facts of
a corpus live in its owner's `wiki/`.

## The plugin

**A plugin with five commands, where one skill stood** · 2026-09-17
`tri`, `setup`, `wiki`, `tidy`, `help`, under `skills/`. The single skill opened on "sort your
documents" and spent nine tenths of its length on the filing pass, so a session that only needed a
fact from the wiki loaded the whole pass manual to get ten lines, and the wiki, the part used every
day, had no entry of its own. Same layout as the author's other public plugins. A `SKILL.md` at the
plugin root is not discovered by the harness, so none is kept there.

**The plugin holds the method and the scripts, the workspace holds everything else** · 2026-09-17
`config.yaml`, `memory.jsonl`, `logs/` and `wiki/` live in `$WIKIDOC_HOME`, outside the plugin
tree. The install is a copy replaced on update, so anything of the user's kept inside would be
destroyed by the next one. The scripts stay in the plugin: they hash, walk and extract, and they
are the same for everyone.

**The checkout is its own marketplace** · 2026-09-17
`marketplace.json` declares the plugin with `"source": "./"`, so `claude plugin marketplace add
<checkout>` then `claude plugin install wikidoc@wikidoc` works from a local clone.

**Every commit ships a version** · 2026-09-17
`.githooks/pre-commit` increments the patch in `plugin.json` and `marketplace.json` and stages
them. The install is refreshed only when the version changes, so without a bump `claude plugin
update` sees the same number and the edit never reaches the installed copy. The version is a commit
counter, not semantics. A fresh clone enables it with `git config core.hooksPath .githooks`.

## The commands

**`help` is the only command not model-invocable** · 2026-09-17
A model reads the frontmatters; a human does not. `help` prints the flow and where the workspace
stands for the person at the keyboard, and its description would only cost context on every turn.
The four others keep a description so a session, or a routine, can reach them on its own.

**`wiki` reads and writes, in one skill** · 2026-09-17
Answering from the wiki, finding a document through the ledger and writing a fact down are one
gesture seen from three sides: the index, then the section, then the line. Three skills would have
paid three always-loaded descriptions for one discipline. The rule that the wiki is written in any
session still lives in the user's instructions file, where `setup` anchors it; the skill carries
how, the anchor carries when.

**`tri` keeps the user's word for the pass** · 2026-09-17
The pass was already launched under that word, and it reads in English as the root of triage,
which is the pass's own vocabulary (`triage: route | propose | residual`).

**`tidy`, not `audit`** · 2026-09-17
`route.py --audit` and `--full-audit` already name the audit of a filing rule, and one word for two
examinations would have sent a session to the wrong one. The name also had to promise what the
command does: a small regular clean, examined first and applied on the user's pick, light enough
for a weekly routine. Run hands-off it examines, writes its report under `logs/`, and applies
nothing.

**The examination of `tidy` writes nothing** · 2026-09-17
Findings first, with their proof, then the user's pick, then the clean. An examination that edits
as it goes cannot be checked against the state it found, and a wiki is exactly the place where a
confident wrong merge deletes the only copy of a fact.

## Deferred

**Following up the dossiers in flight** · 2026-09-17
A command that sweeps the mailboxes for every dossier `state.md` marks as awaiting an answer, and
lists the answers received, the reminders overdue and the deadlines coming. Useful, and set aside
until the five commands have been lived with.

**A four-block report for the hands-off pass** · 2026-09-17
Filed, to validate, refused, what broke, as a closing step of `tri` when a routine runs it. Learn
already names the leftovers; the report would only reshape them.
