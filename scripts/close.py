#!/usr/bin/env python3
"""Close the pass: score the shadow rules, write every ledger line, clear the bench.

A rule is not proposed because it looks right. It runs in `shadow` — evaluated,
never applied — and this step compares what it would have done against what was
actually decided, pass after pass. Only a rule with enough passes, enough hits
and no disagreement is put to you, with that record attached.

New rules are suggested the same way: from phrases measured to appear in every
file that went to one destination and in no other file of the pass. A phrase
that merely sounds discriminating is how you get a rule that quietly swallows
the wrong documents.


What this step writes is what "processed" means, so it runs even for the files
nothing happened to — a deliberate non-action is a decision, and the reason it
was taken is the one thing the old pipeline never recorded.

The journal takes only what the ledger cannot compute. Counts of moves, tags and
trashed files are read back out of the ledger, so writing them here would
recreate the report/log duplication this design just removed. What belongs:
arbitrations, incidents, promoted rules, open questions.

Notes come from `batch/journal.md` (typed lines written during the pass) and
from `--note`. Types: decision · rule · incident · question.

Usage: close.py [--note "decision | ..."] [--force]
"""
import glob
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger import Ledger, nfc, pass_id, require_config  # noqa: E402
from route import bump_counter  # noqa: E402

TYPES = ("decision", "rule", "incident", "question", "ingest")
RIPE = {"min_passes": 2, "min_hits": 5}      # overridable under `rules_review:`
NGRAM_MIN, NGRAM_MAX = 2, 5


def score_shadows(routed, decided, cfg):
    """What each shadow rule proposed, against what was actually decided."""
    agreed, disagreed, examples, touched = {}, {}, {}, set()
    for f in routed.get("files", []):
        shadows = f.get("shadow") or []
        if not shadows:
            continue
        # matched on content, not on path: a file the pass moved is recorded at
        # its destination, while the routing still names where it came from
        real = decided.get(f.get("md5")) or decided.get(nfc(f["path"]))
        if not real:
            continue
        for s in shadows:
            rid = s["rule"]
            touched.add(rid)
            if s.get("destination"):
                # Judged on where the file ENDED UP, not on whether it moved. A
                # rule that names the folder a document is already in is right,
                # and counting that as a divergence marks every correct rule
                # wrong on a tidy corpus — which is the normal state after the
                # first pass, so no rule could ever be promoted.
                ok = nfc(real["path"]).startswith(
                    nfc(os.path.relpath(s["destination"], cfg["root"])).rstrip(os.sep))
            elif s.get("tag"):
                ok = s["tag"] in (real.get("tags") or [])
            else:
                ok = real.get("level") == s.get("lane")
            if ok:
                agreed[rid] = agreed.get(rid, 0) + 1
            else:
                disagreed[rid] = disagreed.get(rid, 0) + 1
                examples.setdefault(rid, []).append(
                    f"{real['path']} -> {real['decision']}"
                    + (f" ({real['path']})" if s.get("destination") else ""))
    return agreed, disagreed, examples, touched


def ripe_rules(cfg, agreed, disagreed):
    """Shadow rules that have earned the right to be put to a human."""
    th = {**RIPE, **(cfg.get("rules_review") or {})}
    out = []
    for r in cfg.get("rules", []):
        if r.get("status") != "shadow":
            continue
        passes = int(r.get("passes", 0)) + (1 if r["id"] in agreed or r["id"] in disagreed else 0)
        hits = int(r.get("hits", 0))
        bad = int(r.get("disagreed", 0)) + disagreed.get(r["id"], 0)
        if passes >= th["min_passes"] and hits >= th["min_hits"] and bad == 0:
            out.append({"id": r["id"], "passes": passes, "hits": hits,
                        "agreed": int(r.get("agreed", 0)) + agreed.get(r["id"], 0),
                        "level": r.get("level"), "destination": r.get("destination"),
                        "tag": r.get("tag"), "when": r.get("when")})
    return out


def ngrams(text, lo=NGRAM_MIN, hi=NGRAM_MAX):
    words = re.findall(r"[\w'’-]+", (text or "").casefold())
    return {" ".join(words[i:i + n])
            for n in range(lo, hi + 1) for i in range(len(words) - n + 1)}


def suggest_rules(prep_texts, decided, cfg):
    """Phrases that single out one destination — measured, not guessed.

    A candidate phrase has to appear in EVERY file that went to a destination and
    in NO other file of the pass. That is the check the author of a rule skips.
    """
    by_dest = {}
    for path, real in decided.items():
        if real["decision"] != "move":
            continue
        by_dest.setdefault(os.path.dirname(real["path"]), []).append(path)

    out = []
    for dest, paths in by_dest.items():
        if len(paths) < 3:
            continue
        inside = [prep_texts.get(p) for p in paths if prep_texts.get(p)]
        if len(inside) < 3:
            continue
        common = set.intersection(*(ngrams(t) for t in inside))
        elsewhere = set()
        for p, t in prep_texts.items():
            if p not in paths:
                elsewhere |= ngrams(t)
        unique = sorted(common - elsewhere, key=lambda s: (-len(s), s))[:5]
        if unique:
            out.append({"destination": dest, "files": len(paths),
                        "discriminating_phrases": unique})
    return out


def load_vision(batch):
    """path -> what the agent understood of it."""
    out, vdir = {}, os.path.join(batch, "vision")
    if not os.path.isdir(vdir):
        return out
    for fn in sorted(os.listdir(vdir)):
        if not fn.endswith(".json"):
            continue
        try:
            entries = json.load(open(os.path.join(vdir, fn), encoding="utf-8"))
        except ValueError:
            continue
        for e in entries:
            if e.get("path"):
                out[nfc(e["path"])] = e
    return out


def journal_notes(batch, extra):
    notes = []
    jf = os.path.join(batch, "journal.md")
    if os.path.exists(jf):
        with open(jf, encoding="utf-8") as f:
            notes += [l.rstrip("\n") for l in f if l.strip()]
    notes += list(extra)
    return notes


def main():
    force = "--force" in sys.argv
    extra = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--note"
             and i + 1 < len(sys.argv)]
    cfg = require_config()
    led = Ledger(root=cfg["root"])
    ws = cfg["workspace"]
    batch = os.path.join(ws, "batch")
    routed_path = os.path.join(batch, "routed.json")
    if not os.path.exists(routed_path):
        sys.exit("no routed.json — this pass never got past route.py")
    routed = json.load(open(routed_path, encoding="utf-8"))
    pass_name = routed.get("pass") or pass_id(led)
    vision = load_vision(batch)

    # -- one ledger line per file, including the ones nothing happened to ----
    already = {r.get("path") for r in led.by_md5.values() if r.get("pass") == pass_name}
    records, missing_desc = [], 0
    for r in routed.get("files", []):
        p = r["path"]
        if led.rel(p) in already:
            continue                      # apply.py already recorded this one
        v = vision.get(nfc(p), {})
        desc = v.get("desc") or r.get("desc")
        if not desc:
            missing_desc += 1
        if not os.path.exists(p):
            continue                      # moved or binned outside this pass
        records.append(led.record(
            p, pass_id=pass_name, level=r.get("lane", "residual"), decision="none",
            reason=r.get("reason", ""), md5=r.get("md5"), desc=desc,
            ids=v.get("ids") or r.get("ids"), tags=v.get("tags")))
    written = led.append_many(records)

    unrecorded = len(routed.get("files", [])) - len(already) - written
    if unrecorded > 0 and not force:
        print(json.dumps({"pass": pass_name, "ledger_lines": written,
                          "unrecorded": unrecorded,
                          "bench": "kept — an interrupted pass leaves batch/ populated",
                          "hint": "settle the missing files, or re-run with --force"},
                         ensure_ascii=False, indent=1))
        return

    # -- score the shadow rules against what was actually decided ------------
    decided = {}
    for r in led.by_md5.values():
        if r.get("pass") == pass_name:
            decided[nfc(led.abs(r["path"]))] = r
            if r.get("md5"):
                decided[r["md5"]] = r
    agreed, disagreed, examples, touched = score_shadows(routed, decided, cfg)
    prep_texts = {}
    for pf in sorted(glob.glob(os.path.join(batch, "prep", "*.json"))):
        try:
            for e in json.load(open(pf, encoding="utf-8")):
                if e.get("text"):
                    prep_texts[nfc(e["path"])] = e["text"]
        except ValueError:
            pass
    suggestions = suggest_rules(prep_texts, decided, cfg)
    ripe = ripe_rules(cfg, agreed, disagreed)

    cfg_path = os.path.join(ws, "config.yaml")
    bump_counter(cfg_path, "agreed", agreed)
    bump_counter(cfg_path, "disagreed", disagreed)
    bump_counter(cfg_path, "passes", {rid: 1 for rid in touched})

    # -- the journal takes what the ledger cannot compute --------------------
    notes = journal_notes(batch, extra)
    today = datetime.now().strftime("%Y-%m-%d")
    jpath = os.path.join(ws, "journal.md")
    if notes:
        lines = []
        for n in notes:
            n = n.strip()
            if n.startswith("##"):
                lines.append(n)
                continue
            typ, _, rest = n.partition("|")
            typ = typ.strip().casefold()
            if typ in TYPES and rest.strip():
                lines.append(f"## [{today}] {typ} | {rest.strip()}")
            else:
                lines.append(f"   {n}")
        with open(jpath, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    # -- clear the bench -----------------------------------------------------
    logs = os.path.join(batch, "logs")
    kept_logs = os.path.join(ws, "logs", pass_name)
    if os.path.isdir(logs):
        os.makedirs(os.path.dirname(kept_logs), exist_ok=True)
        if os.path.exists(kept_logs):
            shutil.rmtree(kept_logs)
        shutil.move(logs, kept_logs)
    shutil.rmtree(batch, ignore_errors=True)
    os.makedirs(batch, exist_ok=True)

    counts = {}
    for r in led.by_md5.values():
        if r.get("pass") == pass_name:
            counts[r.get("decision", "none")] = counts.get(r.get("decision", "none"), 0) + 1
    print(json.dumps({"pass": pass_name, "ledger_lines_written": written,
                      "pass_decisions": counts, "journal_entries": len(notes),
                      "without_desc": missing_desc,
                      "shadow_rules_seen": len(touched),
                      "logs": kept_logs if os.path.isdir(kept_logs) else None,
                      "bench": "empty"}, ensure_ascii=False, indent=1))

    if disagreed:
        print("\nshadow rules that diverged from what you decided:")
        for rid, n in sorted(disagreed.items(), key=lambda kv: -kv[1]):
            print(f"  {rid}: {n} disagreement(s), {agreed.get(rid, 0)} agreement(s)")
            for ex in examples.get(rid, [])[:3]:
                print(f"      {ex}")
        print("  a rule that keeps diverging is wrong about your documents, not the "
              "other way round — rewrite it or drop it.")

    if ripe:
        print("\nready for your decision — these ran in shadow and never diverged:")
        for r in ripe:
            print(f"\n  {r['id']}  ·  {r['passes']} pass(es), {r['hits']} file(s) caught, "
                  f"{r['agreed']} matched your decision, 0 divergence")
            print(f"    when        {json.dumps(r['when'], ensure_ascii=False)}")
            print(f"    would       {r['level']}"
                  + (f" -> {r['destination']}" if r.get("destination") else "")
                  + (f"  tag {r['tag']}" if r.get("tag") else ""))
        print("\n  flip `status: shadow` to `active` on the ones you want, leave the rest.")

    if suggestions:
        print("\nphrases that single out a destination in this pass "
              "(present in every file that went there, in no other file):")
        for s in suggestions:
            print(f"  {s['destination']}  ({s['files']} files)")
            for p in s["discriminating_phrases"]:
                print(f"      \"{p}\"")


if __name__ == "__main__":
    main()
