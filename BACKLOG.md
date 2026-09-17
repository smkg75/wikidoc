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

9. ~~**`route_entry` never copies a rule's `tags:` into the entry.**~~ Fixed
   2026-08-30: `route_entry` writes a rule's `tags:` to a `rule_tags` column,
   proposed the way `destination` is, and step ④ copies them into `tags`. A
   tags-only rule also says so in its `why` (`tags only, nothing to move`), so
   the decide step stops looking for a destination that was never there.
   Still open in the same area: a rule with neither `destination:` nor `tags:`
   is still triaged `route` while proposing nothing. None exists in practice.

10. **The "10 passes at 0 hits" retirement is unreachable.** `rule_report`
    computes `passes = stored + (1 if rid in hits else 0)`, so a rule that
    never matches never increments `passes`, and
    `passes >= dead_after_passes and h == 0` can never fire. Nothing retires
    a diverging rule either — only `cycle > max_cycles` does, and cycles are
    bumped by hand. skills/tri/SKILL.md promises an automatic retirement the code cannot
    produce. Direction: increment `passes` for every rule evaluated, not only
    for those that matched.

11. **The miner rewards the least specific n-gram, and mines in a space its
    own matcher cannot read.** `min(unique, key=lambda s: (len(s), s))` takes
    the SHORTEST phrase; and `ngrams()` joins word tokens with single spaces
    while `_says` matches against `norm(text)`, which keeps punctuation — so
    an n-gram mined across a comma cannot match any of its parents by
    construction. Together they produced `text_contains_any: ["4 20"]` out of
    "4,20 %": 156 documents touched corpus-wide, 0 of the 4 that engendered
    it. Direction: take the longest, require an alphabetic token, reject the
    purely numeric, and validate every candidate against the group it was born
    from before writing it.

12. **The miner's counter-sample is the pass, not the corpus.** `others` is
    built from the pass's own textual entries (248 in the measured pass) while
    the corpus held 3 860 — "unique among 248" is not "discriminating among
    thousands". skills/tri/SKILL.md ⑥ warns the agent about this; the miner itself does
    not know it. Direction: widen the counter-sample to `memory.jsonl`'s
    descriptions, as branch 4 already does for identifiers.

13. **`extract.py` never reads AcroForm fields.** A filled administrative form
    whose values are field entries with no appearance stream extracts as
    mojibake or nothing, so it goes to `needs_vision` — where the render shows
    the form BLANK and the whole reader ladder concludes "empty". Measured on
    a real employer attestation: 105 filled fields out of 642, invisible to
    every reader in the ladder. `PdfReader.get_fields()` and
    `pdftotext -layout` both restore them. Direction: query the AcroForm
    before the vision gate and fold field values into the entry's text.
