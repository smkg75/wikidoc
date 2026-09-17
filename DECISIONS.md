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
command does: a small regular clean, light enough for a weekly routine.

**`tidy` cleans alone, and the wiki is a git repository** · 2026-09-17
The first version examined, then waited for the user's pick. The user's word: a clean that waits
for a validation is an audit, and the person a wiki serves does not maintain it, the model does.
So `tidy` examines, cleans and reports, and nothing waits on anyone. What makes that safe is
versioning: `wiki/` is a local git repository with no remote, each tidy is one commit to read and
revert, and the commit taken before it holds everything sessions wrote in silence since the last
one. The same log gives the incremental scope, the diff since the last tidy, which is what keeps a
weekly run cheap: a first full run measured around 600 K tokens, nearly all of it in the two
families that read the long files.

**A fact is settled by a dated replacement or by its source, never by recency** · 2026-09-17
Measured on the first run: of the ten most severe findings, one was a false alarm and one could
not be decided from the wiki alone. A clean that picked the most recent of two lines would have
rewritten a filed figure on a guess. So a contradiction is settled by the source document, read,
with the arbitration archived; what no source settles is recorded with what would settle it and
becomes an open question. `decisions.md` and every dated archive are append-only, and anything
outside the workspace belongs to the corpus.

**Events go to `log.md`, the state keeps the present** · 2026-09-17
The write rule sent every event to `state.md`, and measured on a real wiki half of that file had
become history: reports of closed passes, closed questions, dossiers whose line had grown into a
twenty-line journal. A clean could empty it, and the rule would fill it again. So an event is
written in two tenses: appended to `log.md`, which is never rewritten, and folded into the one
current line its dossier keeps in `state.md`. This is the append-only log of the LLM-wiki pattern
this plugin follows, and `tidy` logs its own passes there too.

**The examiner re-reads every proof it is handed** · 2026-09-17
Four of forty-two line numbers returned by the reading sub-agents were wrong on the first run. A
finding whose quoted line is not where it was said to be is dropped.

**Pointers are `[[links]]` and rooted paths** · 2026-09-17
A wiki writes most of its paths as fragments relative to an unspoken root. Checked naively, 427 of
452 backticked strings were "dead"; bounded to links and to absolute or `~/` paths, the family is
nearly free and keeps its real findings. A path whose own line says it was binned or moved is a
correct trace.

## Deferred

**Following up the dossiers in flight** · 2026-09-17
A command that sweeps the mailboxes for every dossier `state.md` marks as awaiting an answer, and
lists the answers received, the reminders overdue and the deadlines coming. Useful, and set aside
until the five commands have been lived with.

**A four-block report for the hands-off pass** · 2026-09-17
Filed, to validate, refused, what broke, as a closing step of `tri` when a routine runs it. Learn
already names the leftovers; the report would only reshape them.
