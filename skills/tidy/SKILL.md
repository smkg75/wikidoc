---
name: tidy
description: The regular clean of the wiki and the workspace - examines first and returns findings with their proof, then applies what the user approved. Use when the user asks to tidy or clean up the wiki, or asks whether it is healthy, stale, bloated or contradicting itself.
---

# Tidy

A small, regular clean in two halves, light enough to run every week. The examination reads the wiki, the ledger and the workspace and comes back with findings ranked by severity, touching nothing: an examination that edits as it goes cannot be checked against the state it found. Then the user picks, and the session cleans what was picked.

This is the upkeep of the wiki. The audit of a filing rule is `route.py --audit` and `--full-audit`, in `skills/tri/SKILL.md`.

## Step 0 — Load

Resolve the workspace: `$WIKIDOC_HOME`, default `~/.wikidoc`. No `wiki/index.md`: stop on "Run `/wikidoc:setup` first". Read `index.md`, list every file under `wiki/` with its line count, and find the previous tidy line in `wiki/state.md` when one exists.

Done when: the index, the file list and the previous counts are in hand.

## Step 1 — The families

Families 1, 2, 5 and 6 are mechanical: run them here. Families 3 and 4 read the long files in full: dispatch one sub-agent per family, in parallel, so those files stay out of this conversation. Each sub-agent receives the wiki path and its family, and returns facts: file, section, what was observed, the line quoted as proof. The verdict on each finding is rendered here.

1. **Index** — a wiki file with no index line; an index line whose target is gone; a line past ~150 characters, or one that states the answer instead of naming the question.
2. **Pointers** — a backticked path or a `[[link]]` in the wiki that resolves to nothing; the anchor in the instructions file, checked the same way.
3. **Facts** — one fact carried by two files; a replaced fact still standing beside its replacement; a contradiction recorded without what would settle it.
4. **State** — a dossier marked closed still listed as in flight; a deadline passed with no event after it; an open question already answered elsewhere in the wiki; history settled in `state.md` (reports of past passes, closed questions) that belongs in `logs/` or `decisions.md`.
5. **Ledger** — a path whose last `memory.jsonl` line says it was filed and whose file is gone from that destination; the `unanswered` and `refused` lines and their age; a `bench/` left in the workspace.
6. **Workshop** — backups piling up beside `config.yaml` and `memory.jsonl`, reports and stray files at the workspace root, each with its size and date.

Done when: the six families have returned, or the one that broke is named.

## Step 2 — The findings

Rank by severity: what would make the wiki give a wrong answer first (facts, pointers), what buries the current state second (state, index), what costs only tokens and disk third (ledger, workshop). Each finding: family, file and section or path, what was observed, the proof, the decision suggested, and ❓ where the wiki does not settle it.

Done when: every finding has its five fields, and the list is rendered most severe first with the count per family against the previous counts.

## Step 3 — Clean

Run hands-off, from a routine with nobody at the keyboard: write the ranked list to `logs/tidy-<date>.md`, apply nothing, and go to Step 4, whose line points at that report. The next session opens on it.

With the user present, put the list to them and take their pick: all, none, or the findings they name. Apply each approved decision under the write rules of `skills/wiki/SKILL.md`: a replaced fact is deleted where it lived, history moves to `logs/` or `decisions.md` whole, the index is brought back to one pointing line per file. A file that leaves the workspace goes to the OS bin. A finding marked ❓ is asked, one at a time, before anything is applied to it.

Done when: every approved finding is applied and re-read at its new place, and every other finding stands untouched.

## Step 4 — Record

One dated line in `wiki/state.md`: `tidy`, the count per family, how many were applied, and the report when one was written. It replaces the previous tidy line.

Done when: the line is written.
