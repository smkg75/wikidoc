---
name: help
description: The flow of the five commands, what each produces, and where this workspace stands.
disable-model-invocation: true
---

# Help

Prints the flow, then where the workspace stands. Writes nothing.

## Step 1 — The flow

Print this, as is:

```
setup     once per machine; config.yaml from the documents themselves, the wiki anchored, the inboxes swept     files
tri       one filing pass: collect, read, route, decide, apply, learn                                         files
wiki      answers from the wiki and writes to it; fires on its own when a fact is asked or surfaces           wiki
tidy      the regular clean of the wiki and the workspace: findings first, then what the user approved        wiki
help      this flow, and where the workspace stands                                                           read-only
```

Done when: the five lines are printed.

## Step 2 — Where the workspace stands

Resolve the workspace: `$WIKIDOC_HOME`, default `~/.wikidoc`. No `config.yaml`: say the machine is fresh and that `/wikidoc:setup` comes first; stop.

Found: run `memory.py stats` (`scripts/` sits beside `skills/` at the plugin root), list `logs/` for the last archived pass, and list the headings of `wiki/state.md` to read the sections that hold the open questions and what awaits the user. Print, in this order:

- the corpus — files in memory, and the `unanswered` and `refused` counts
- the last pass — its name and date, and a `bench/routing.json` still in the workspace, which is a pass left hanging that `/wikidoc:tri` resumes
- the open questions, one line each
- what awaits the user, one line each

Then name the next command. A hanging pass or open questions: `/wikidoc:tri`. Nothing waiting: say so.

Done when: the four blocks are printed and no file was written.
