---
name: tidy
description: The regular clean of the wiki - examines it, cleans what the wiki or its sources settle, and reports what changed and what stays open. Runs alone, from a routine or on request. Use when the user asks to tidy or clean up the wiki, or asks whether it is healthy, stale, bloated or contradicting itself.
argument-hint: "nothing for what changed since the last tidy, 'full' to read the whole wiki"
---

# Tidy

The upkeep of the wiki, done alone: examine, clean, report. The user reads what changed afterwards; nothing waits on them. Three things make that safe. The wiki is a local git repository, so every clean is one commit the user can read and revert. The examination is finished before the first edit, so each finding is checked against the state it was found in. And a fact is only ever settled by the wiki's own dated replacement or by its source document, never by a guess: what neither settles stays, as an open question.

The audit of a filing rule is another thing: `route.py --audit`, in `skills/tri/SKILL.md`.

## Step 0 — Load

Resolve the workspace: `$WIKIDOC_HOME`, default `~/.wikidoc`. No `wiki/index.md`: stop on "Run `/wikidoc:setup` first". Read `skills/wiki/SKILL.md`: its write rules bind every edit below.

`wiki/` is a git repository, local, with no remote. Absent: `git init` it. Commit whatever is uncommitted as `before tidy <date>`: that commit holds everything sessions wrote since the last tidy.

The scope: `$ARGUMENTS` is `full`, or no `tidy <date>` commit exists yet, and the whole wiki is read. Otherwise the scope is the diff since the last `tidy` commit: the changed hunks, and for each fact they carry, the other places in the wiki that speak of the same subject, found by search. List every file under `wiki/`, recursively, with its line count.

Done when: the `before` commit exists, the scope is a list of files and hunks, and the write rules are read.

## Step 1 — Examine

Families 1, 2, 5 and 6 are mechanical: run them here. Families 3 and 4 read long text: dispatch one sub-agent per family, in parallel, so that text stays out of this conversation. Each sub-agent is told it is read-only, receives the wiki path, the scope and its family, and returns per finding: file, section, line number, what was observed, the line quoted word for word, what would settle it. A finding that spans `state.md` and another file belongs to State. Re-read every quoted line at its number before using it: a proof that is not where it was said to be is dropped.

1. **Index** — a wiki file with no index line; an index line whose target is gone, or that points at no file; a line past ~150 characters, or one that carries the fact instead of naming the question.
2. **Pointers** — a `[[link]]`, or an absolute or `~/` path, that resolves to nothing. A path whose own line records it as binned, moved or deleted is a correct trace. A path fragment is resolved against the workspace and against `root`, and skipped when neither holds it. The anchor is the instructions file (`anchors:` in `config.yaml`, default `~/.claude/CLAUDE.md`): its pointers are checked the same way.
3. **Facts** — two lines that contradict each other; a replaced fact still standing beside its replacement; the same fact with its detail in two files; one name under two spellings. A dated arbitration in `decisions.md` and the current rule in the file that owns it are two layers of one fact, by design.
4. **State** — a dossier marked closed still listed as in flight; an open question the wiki answers elsewhere; a current-state line the disk contradicts; history settled in `state.md` or any file of current rules, which belongs in `log.md`: reports of past passes, closed questions, the event-by-event journal of a dossier.
5. **Ledger** — a path whose last `memory.jsonl` line leaves the file in place (`none`, `tag`, `keep`, or no decision) and whose file is gone; an `unanswered` or `refused` line older than 30 days; a `bench/` holding a `routing.json`, which is an interrupted pass, or an empty one, which is debris.
6. **Workshop** — backups beside `config.yaml` and `memory.jsonl`, and the weight of `logs/` by subfolder, each with size and date. A chronicle or a report a session left at the workspace root: the chronicle belongs in `log.md`, the report in `logs/`.

And one family that is reported, never cleaned — **Deadlines**: every date in `state.md` by which someone must act, passed or within 14 days, with the last event recorded on it.

Done when: every family has returned or the one that broke is named, and every proof kept was re-read at its line.

## Step 2 — Clean

Each finding gets one of four fates.

**Cleaned**, when the wiki or a source settles it:
- A replaced fact whose replacement is dated in the wiki: the old line is deleted where it lived.
- A contradiction: find the source document (`memory.py find`, then read it) and keep what it proves; the arbitration goes to `decisions.md` with the source named. When the source shows both lines true of two different things, both stay and one sentence says what each of them names. A wrong fact the source reveals in passing is corrected the same way, and counted.
- A duplicated fact: it stays in the file that owns that kind of fact, the other place points at it.
- History: it moves whole to `log.md`, word for word under dated headings in date order, and the state keeps one current line per dossier. A dated entry that is the evidence of a rule stays with its rule: only history with no rule attached leaves. No `log.md` yet: create it, with its index line. Count the lines taken out against the lines laid down.
- The index: one pointing line per file.
- A pointer to a moved target: re-pointed once the target is found on disk.
- A current-state line the disk contradicts: rewritten from the disk.
- Backups older than the latest of each file, unless the wiki cites them, and an empty `bench/`: to the OS bin.

**Left as it is**, by rule: `decisions.md`, `log.md` and every dated archive are append-only, a dead link inside a dated entry is history. Anything outside the workspace belongs to the corpus and to `tri`. A file the wiki cites stays where it is cited. `logs/` of past passes are archives. `memory.jsonl` is the pass's to write: Ledger findings are reported, like the deadlines.

**Open**, when neither the wiki nor a source settles it, or the decision is the user's: the contradiction is recorded at the fact's place with what would settle it, and the question goes to `state.md`, one line, under the one heading that holds tidy's questions. Each tidy rewrites that heading: the answered leave, the rest carry over.

**Dropped**, when the proof does not hold on re-reading, or when a dated arbitration in `decisions.md` already settles the matter — search it before raising anything the user may have ruled on: named in the report with the reason, so the next examination does not raise it again.

Edit file by file, surgically, and re-read each edit at its place.

Done when: every finding has its fate, and `git diff` shows only edits a finding accounts for.

## Step 3 — Report and record

Write `logs/tidy-<date>.md` in the wiki's language, under a hundred lines: the deadlines first; then what was cleaned, one line each; what stays open, one line each with what would settle it; the count per family against the previous tidy, or "first run". Append one line to `wiki/log.md`: the date, `tidy`, cleaned, open, the report's path. The previous counts are read from the previous report.

Then commit as `tidy <date>`. With the user present, close on the deadlines and the open questions, and on how to read the change: `git -C <wiki> show`.

Done when: the report is written, the log line points at it, and the `tidy <date>` commit holds both.
