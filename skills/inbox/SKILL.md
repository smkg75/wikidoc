---
name: inbox
description: Quick filing pass over the inboxes alone (Desktop, Downloads) - a full tri pass sized on them, so nothing else in the corpus is read first. Use when the user asks to sort the Desktop or Downloads, or to clear the inboxes.
argument-hint: "nothing for every inbox, or the name of one inbox (Desktop, Downloads)"
---

# Inbox

The quick pass: `skills/tri/SKILL.md` run with the argument `inboxes`, so collect selects the files of the inboxes and nothing else, and the vision barrier at route waits on them alone. Read that skill and follow it from ① to ⑥; the invariants, the delegations and the questions are the same, and this one only fixes the size and the order.

1. **Size.** Pass `inboxes` to collect. One inbox named in `$ARGUMENTS`: size the pass on that inbox's file count alone.
2. **A pass already hanging.** A `bench/routing.json` in the workspace belongs to another pass, maybe running in another session. Do not collect over it: say so and stop, or let the user finish it with `/wikidoc:tri`.
3. **Order.** The files of the inboxes are read (②), routed (③), decided (④) and applied (⑤) before anything else is touched. Nothing else is in this pass, so nothing waits on it.
4. **Close.** Learn (⑥) runs as usual, in its subagent. What an inbox still holds afterwards — an `unanswered` or `refused` file, a file left undecided — is named in `wiki/state.md`.

Done when: every file the inboxes held at collect is filed or named in `wiki/state.md`, `bench/` is archived, and the `pass <pass>` commit is made.
