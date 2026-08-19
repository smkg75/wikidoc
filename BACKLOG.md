# wikidoc — backlog

Noted, not scheduled.

Each entry: what you observe, why it happens, the direction a fix would take.
None of these blocks production; they are recorded here so the reason survives
until someone picks them up.

1. **Kept files have no content identity.** A `tag`/`none` decision writes its
   memory line with `md5: null` (apply hashes only what it moves or bins). So
   `seen_md5`/`known_as` recognise a duplicate of a *moved* file, never of a
   file *kept in place* — a re-download of something you tagged last month
   arrives as a stranger. Direction: hash on tag/none too (costs one md5 per
   kept file), or lazy-hash at collect when a size collision suggests a twin.

2. **`banned_phrases` no longer enforced.** an earlier review step rejected descs
   containing hedge phrases ("ce document semble…"); the current code kept only the
   paraphrase test in `Memory.record()`. Production config still carries a
   `review: banned_phrases:` block that nothing reads. Direction: fold the
   list into `record()`'s desc validation, same ValueError path as the
   paraphrase test.

3. **`--audit` replays rules against the recorded desc, not the document.**
   Full text is not stored in memory, so a text condition can only match what
   the desc happens to quote — retroactive agreed/disagreed underestimates
   hits (labelled in the output, but still). Direction: an `--audit --reread`
   variant that re-extracts page 1 of the judged files it replays, at the
   cost of the read.

4. **No per-rule triage cap.** rules could once declare `level: propose` ("bank
   statements: a human always confirms"). there is no equivalent today: once promoted
   `active`, a rule routes — there is no way to say "this rule may only ever
   propose". The production config carries exactly this intent on its banking
   rule, currently expressed as a comment. Direction: an optional `cap:
   propose` honoured at application time, checked at promotion.

5. **Containers are opaque by fiat.** Zips/archives are `opaque: "container"`
   (correct: unrenderable, so never `needs_vision`), but their entry *names*
   are cheap evidence collect could harvest as text — often enough to triage
   without opening anything. Direction: list entry names (bounded, say 50)
   into `text`, flagged so the prose gate does not mistake it for a read.

6. **`--full-audit` samples the head of the walk.** Beyond 500 candidates it
   takes the first 500 by path order — an alphabetical prefix, not a sample.
   A rule whose overreach lives in `Z-archive/` audits clean. Direction:
   stratify by top-level folder, or take a seeded random sample (seed passed
   in, never generated — scripts stay deterministic).

7. **A rename orphans the symlinks that point at the file.** A corpus may
   file deliberately in links (an archived mail-out pointing at the canonical
   originals). Renaming an original leaves those links dangling: apply reports
   the broken link on the NEXT pass (`FAIL … dangling symlink`) but never
   repairs it, and the pass that caused it says nothing. Observed in
   production the first time a rename hit a linked file. Direction: before a
   `move`/`rename`, scan for links resolving to the source and re-point them
   in the same gesture — or at minimum name them in the dry-run so the
   operator sees what the rename is about to break.

8. **Inbox guard vs promoted rules — a watch-point, not a bug.** The inbox
   guard preempts rules by design (nothing routes silently out of an inbox),
   which means even a promoted `active` rule never fires on inbox files: they
   are proposed forever. That is the contract as decided; watch whether the
   friction in practice (every Downloads invoice needing a click) justifies a
   per-inbox `allow_rules: true` escape hatch some day.
