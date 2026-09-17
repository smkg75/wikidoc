---
name: wiki
description: Reads and writes the wiki of the document corpus. Use when a question bears on the user, their entities, dossiers or correspondents, when a document must be found in the corpus, or when a durable fact or a change in a dossier's state surfaces and must be written down.
---

# Wiki

`$WIKIDOC_HOME/wiki/` (default `~/.wikidoc/wiki/`) holds what the corpus cannot say about itself: who is who, which entity was live in which period, which arbitration was made and why. `context.md` carries the durable facts, `decisions.md` the dated arbitrations, `state.md` the present — one current line per dossier in flight, and the open questions — `log.md` the chronicle of what happened and when, `filing-patterns.md` and `trash-criteria.md` the observed destinations and removal criteria. A wiki grows the files its owner needs; `index.md` names them all.

No `wiki/index.md`: stop on "Run `/wikidoc:setup` first".

## Read

1. Read `index.md`. Each line names the question a file settles.
2. Open the file that settles this question. The files are long: list the headings, read the section that answers.
3. Answer with the fact and the place it lives, `file § section`. Where the wiki is silent, say so, dig in the corpus, and write what the digging established.

Done when the answer names its source, or names the wiki's silence.

## Find a document

`memory.py find <term>` searches what every filed document was said to be: description, tags, identifiers. `memory.py show <path|md5>` gives the decision behind one file. The script lives in `scripts/` at the plugin root (`scripts/` sits beside `skills/`, two levels up from this file). On macOS Spotlight carries the same descriptions and tags, and also covers what no pass has judged yet.

Done when the path handed to the user has been re-stat'ed on disk.

## Write

The wiki is written in any session, the moment the fact surfaces: a durable fact established while answering any question, or an event that changes the state of a dossier (sent, received, signed, refused, decided, filed). It goes to the file that owns that kind of fact, then its line in `index.md` is updated, surgically, section by section: complete the existing section, keep the rest of the file as it stands. A fact re-derived from the corpus for the third time is a wiki line that was never written. That writing is not a pass: it appends nothing to `memory.jsonl`, which records gestures on files alone.

An event is written twice, once in each tense. `log.md` gets the past: one line appended at the end, opening on the ISO date and the dossier's name so the log greps by either, and nothing in it is ever rewritten. `state.md` gets the present: the dossier's line is rewritten to where things stand now, who awaits whom, and the next deadline. A state line that grows a second sentence of history is a log entry in the wrong file.

`index.md` is a table of contents, not a digest. One line per file — `- [name](file.md) — what it answers` — and the hook names the question the file settles, never the answer to it. **Never restate in the index a fact the target file carries**: two places then own one fact, the index costs the price of both, and neither defers to the other. Past ~150 characters a line has stopped pointing and started summarising; that is the bound, and it is the whole discipline.

**A replaced fact is deleted where it lived.** What replaces it takes its place, and the arbitration that moved it goes to `decisions.md` — that is the trace it leaves. A superseded fact left standing is a second answer to a settled question with nothing marking it stale, and the reader cannot tell which of the two is current. Only a contradiction you cannot settle stays: recorded WITH its contradiction and with what would settle it, in the file, never in the index.

Done when the fact lives in one file, once, and the index still points at every file.
