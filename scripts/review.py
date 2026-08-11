#!/usr/bin/env python3
"""Quality control on what the vision agents sent back.

A chunk that was skimmed rather than read has a signature, and it is always the
same four things: files missing from the answer, `lu` left blank, a description
that only paraphrases the filename, and — when the corpus is not in English —
summaries that came back in English anyway. Any of them flags the chunk for a
fresh run.

Rerun flagged chunks as a NEW run, never a resume: a resume replays the cached
empty answer.

Usage: review.py [--json]
"""
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger import nfc, require_config  # noqa: E402

# Defaults calibrated on one French corpus of administrative documents. A corpus
# of photographs or of source code legitimately reads differently, so every
# threshold is overridable under `review:` in config.yaml.
THRESHOLDS = {
    "min_read_rate": 0.85,          # below this the chunk was skimmed
    "min_desc_chars": 25,
    "max_paraphrase_rate": 0.25,
    "max_wrong_language": 0.15,
}

EN_MARKERS = re.compile(
    r"\b(the|and|of|with|from|this|that|for|invoice|receipt|statement|document|"
    r"letter|contract|dated|regarding|showing|containing)\b", re.I)
FR_MARKERS = re.compile(
    r"\b(le|la|les|des|une|du|de|et|avec|pour|facture|re[çc]u|relev[ée]|document|"
    r"courrier|contrat|dat[ée]|concernant|attestation)\b", re.I)


def tokens(s):
    s = unicodedata.normalize("NFD", nfc(s).casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return {t for t in re.split(r"[^a-z0-9]+", s) if len(t) > 2}


def paraphrases_name(path, desc):
    """A description that only rearranges the filename read nothing."""
    stem = os.path.splitext(os.path.basename(path))[0]
    name_t, desc_t = tokens(stem), tokens(desc)
    if not name_t or not desc_t:
        return False
    return desc_t <= name_t or (len(desc) < 60 and len(desc_t - name_t) <= 1)


def wrong_language(desc, language):
    if language != "fr":
        return False
    return len(EN_MARKERS.findall(desc)) > len(FR_MARKERS.findall(desc))


def check_chunk(name, expected_paths, entries, cfg):
    review = cfg.get("review") or {}
    th = {**THRESHOLDS, **{k: v for k, v in review.items() if k in THRESHOLDS}}
    banned = [b.casefold() for b in review.get("banned_phrases", [])]
    language = cfg.get("language", "fr")
    got = {nfc(e.get("path", "")) for e in entries}
    missing = [p for p in expected_paths if nfc(p) not in got]

    read, paraphrased, wrong_lang, banned_hits, thin = 0, [], [], [], []
    for e in entries:
        desc = (e.get("desc") or "").strip()
        if e.get("lu") and e["lu"] != "name":
            read += 1
        if len(desc) < th["min_desc_chars"]:
            thin.append(e.get("path"))
            continue
        if paraphrases_name(e.get("path", ""), desc):
            paraphrased.append(e.get("path"))
        if wrong_language(desc, language):
            wrong_lang.append(e.get("path"))
        low = desc.casefold()
        if any(b in low for b in banned):
            banned_hits.append(e.get("path"))

    n = max(1, len(entries))
    verdict = {
        "chunk": name, "expected": len(expected_paths), "returned": len(entries),
        "missing": missing, "read_rate": round(read / n, 2),
        "paraphrased": paraphrased, "thin_desc": thin,
        "wrong_language": wrong_lang, "banned_phrases": banned_hits,
    }
    reasons = []
    if missing:
        reasons.append(f"{len(missing)} file(s) absent from the answer")
    if verdict["read_rate"] < th["min_read_rate"]:
        reasons.append(f"read rate {verdict['read_rate']}")
    if (len(paraphrased) + len(thin)) / n > th["max_paraphrase_rate"]:
        reasons.append(f"{len(paraphrased) + len(thin)} descriptions paraphrase the filename")
    if len(wrong_lang) / n > th["max_wrong_language"]:
        reasons.append(f"{len(wrong_lang)} summaries in the wrong language")
    if banned_hits:
        reasons.append(f"{len(banned_hits)} banned phrase(s)")
    verdict["rerun"] = bool(reasons)
    verdict["why"] = reasons
    return verdict


def main():
    cfg = require_config()
    batch = os.path.join(cfg["workspace"], "batch")
    vision = os.path.join(batch, "vision")
    if not os.path.isdir(vision):
        sys.exit(f"no vision answers in {vision}")

    verdicts = []
    for cf in sorted(f for f in os.listdir(batch) if re.fullmatch(r"chunk-\d+\.txt", f)):
        name = cf[:-4]
        with open(os.path.join(batch, cf), encoding="utf-8", newline="") as f:
            expected = [l.rstrip("\n") for l in f if l.strip()]
        vf = os.path.join(vision, name + ".json")
        entries = []
        if os.path.exists(vf):
            try:
                entries = json.load(open(vf, encoding="utf-8"))
            except ValueError:
                entries = []
        if not entries:
            verdicts.append({"chunk": name, "expected": len(expected), "returned": 0,
                             "rerun": True, "why": ["no answer"]})
            continue
        verdicts.append(check_chunk(name, expected, entries, cfg))

    rerun = [v["chunk"] for v in verdicts if v["rerun"]]
    summary = {"chunks": len(verdicts),
               "returned": sum(v.get("returned", 0) for v in verdicts),
               "expected": sum(v.get("expected", 0) for v in verdicts),
               "rerun": rerun}
    if "--json" in sys.argv:
        print(json.dumps({"summary": summary, "verdicts": verdicts},
                         ensure_ascii=False, indent=1))
        return
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for v in verdicts:
        mark = "RERUN" if v["rerun"] else "ok   "
        print(f"{mark} {v['chunk']}  {v.get('returned', 0)}/{v.get('expected', 0)}"
              f"  read={v.get('read_rate', 0)}"
              + ("  <- " + "; ".join(v["why"]) if v["rerun"] else ""))
    log = os.path.join(batch, "logs", "review.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "w", encoding="utf-8") as f:
        json.dump(verdicts, f, ensure_ascii=False, indent=1)
    print(f"detail -> {log}")


if __name__ == "__main__":
    main()
