#!/usr/bin/env python3
"""The deterministic engine: guards first, then your rules, first match wins.

Every file leaves this script in exactly one lane:

    route      a rule recognised it — the agent confirms and files it
    propose    sensitive, duplicated, or matched on weak evidence — the agent
               opens the file before anything happens
    residual   no text to reason on, or nothing matched — vision reads it

Guards run ahead of the rules and cannot be overridden by them: content already
in the ledger is skipped, sensitive documents never route automatically, and a
rule that only knows a filename can never reach the `route` lane — a name is
hearsay, the bytes are the evidence.

Usage: route.py [--dry-run]
"""
import json
import os
import re
import sys
from fnmatch import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger import Ledger, nfc, pass_id, require_config  # noqa: E402

LANES = ("route", "propose", "residual", "skip")
WEAK = {"name_matches", "path_under", "ext_in", "size_gt", "size_lt"}


# ------------------------------------------------------------ conditions ----
def _text(e):
    """The text as a single line — layout is not evidence.

    A phrase split across a line break is still that phrase; matching the raw
    extractor output would miss it. Cached per entry, since every condition of
    every rule asks for it.
    """
    if "_flat" not in e:
        e["_flat"] = re.sub(r"\s+", " ", e.get("text") or "").strip().casefold()
    return e["_flat"]


def _id_values(e):
    return {re.sub(r"[ .]", "", v) for vals in (e.get("ids") or {}).values() for v in vals}


COND = {
    "text_contains_any": lambda e, v: any(nfc(str(x)).casefold() in _text(e) for x in v),
    "text_contains_all": lambda e, v: all(nfc(str(x)).casefold() in _text(e) for x in v),
    "text_matches": lambda e, v: bool(re.search(v, _text(e), re.I)),
    "ids_any": lambda e, v: bool(_id_values(e) & {re.sub(r"[ .]", "", str(x)) for x in v}),
    "id_kind_present": lambda e, v: any(k in (e.get("ids") or {}) for k in v),
    "has_text": lambda e, v: bool(e.get("text")) is bool(v),
    "doc_year_in": lambda e, v: e.get("doc_year") in [int(x) for x in v],
    "ext_in": lambda e, v: e.get("ext") in [str(x).lower() for x in v],
    "name_matches": lambda e, v: bool(re.search(v, os.path.basename(e["path"]), re.I)),
    "path_under": lambda e, v: any(fnmatch("/" + e["_rel"].replace(os.sep, "/"),
                                           (p if p.startswith(("/", "*")) else "*/" + p).rstrip("/") + "*")
                                   for p in v),
    "size_gt": lambda e, v: e.get("size", 0) > int(v),
    "size_lt": lambda e, v: e.get("size", 0) < int(v),
}


def matches(cond, e):
    if not isinstance(cond, dict) or not cond:
        return False           # an empty condition proves nothing about anything
    for key, val in cond.items():
        if key == "all":
            if not all(matches(c, e) for c in val):
                return False
        elif key == "any":
            if not any(matches(c, e) for c in val):
                return False
        elif key == "not":
            if matches(val, e):
                return False
        elif key in COND:
            if not COND[key](e, val):
                return False
        else:
            return False
    return True


def match_strength(cond, e):
    """How strong the evidence that actually fired is. 0 when nothing matched.

    Graded on the branch that matched, never on the branch that was written: an
    `any:` mixing a content test with a filename test is only as strong as
    whichever one fired. Reading the declared conditions instead would let a
    filename pass for content.

        2  read in the file's content
        1  read off the path or the filename — hearsay
    """
    if not isinstance(cond, dict) or not matches(cond, e):
        return 0
    best = 0
    for key, val in cond.items():
        if key in ("all", "any"):
            for c in val:
                best = max(best, match_strength(c, e))
        elif key == "not":
            continue                     # excluding something proves nothing
        elif key in COND:
            best = max(best, 1 if key in WEAK else 2)
    return best or 1


# ---------------------------------------------------------------- guards ----
def sensitive_hit(e, cfg):
    s = cfg.get("sensitive") or {}
    probe = {**e, "_rel": e["_rel"]}
    for key in ("text_contains_any", "name_matches", "path_under", "ext_in",
                "id_kind_present"):
        if key in s and COND[key](probe, s[key]):
            return key
    for rule in s.get("rules", []):
        if matches(rule, probe):
            return rule.get("id", "sensitive-rule")
    return None


def entity_candidates(e, cfg):
    """Every entity this document could belong to, graded by what proves it.

    Documents routinely name more than one entity — an invoice from your company
    also carries your own name — so the question is never "which entity matches"
    but "which evidence is strongest". Three grades:

        3  an identifier read in the content   (a company number, an account ref)
        2  a `match:` on the text              (a name printed on the document)
        1  a `match:` on the path or filename  (hearsay: where it happens to sit)
    """
    ids = _id_values(e)
    out = []
    for ent in cfg.get("entities", []):
        if ids & {re.sub(r"[ .]", "", str(x)) for x in (ent.get("ids") or [])}:
            out.append((3, ent, "identifier read in the content"))
            continue
        strength = match_strength(ent.get("match") or {}, e)
        if strength:
            out.append((strength, ent, "name found in the text" if strength > 1
                        else "where the file sits"))
    return out


def entity_for(e, cfg):
    """The entity, why it won, and the rivals it could not beat.

    The strongest evidence wins outright — a SIREN beats a name, a name beats a
    folder — so the order entities appear in `config.yaml` does not decide
    anything. When two entities tie at the top grade, the document genuinely
    belongs to both as far as the evidence goes (a contract between two
    companies), and that is a reading, not a routing: the caller sends it to the
    `propose` lane instead of picking one in silence.
    """
    cands = entity_candidates(e, cfg)
    if not cands:
        return None, None, []
    top = max(c[0] for c in cands)
    best = [c for c in cands if c[0] == top]
    if len(best) == 1:
        return best[0][1], best[0][2], []
    return None, None, [c[1].get("name") for c in best]


KNOWN_STATUS = ("active", "shadow", "off")


def rule_proposal(rule, e, cfg):
    """What this rule would do to this file, or None if it does not match."""
    strength = match_strength(rule.get("when") or {}, e)
    if not strength:
        return None
    lane = rule.get("level", "propose")
    weak = strength <= 1
    if lane == "route" and weak:
        lane = "propose"           # the name is hearsay: a human-checked lane
    dest = (render_destination(rule["destination"], e, cfg)
            if rule.get("destination") else None)
    return {"rule": rule["id"], "lane": lane, "destination": dest,
            "tag": rule.get("tag"), "weak": weak}


def shadow_proposals(e, cfg):
    """What the shadow rules would have done — collected before the guards.

    A guard settles the file's lane, but it must not settle what the rules get
    to learn from. Collecting shadows only on files no guard touched would blind
    every candidate rule to sensitive documents and duplicates — most of an
    administrative corpus — so a rule about payslips could never earn promotion.
    """
    return [p for p in
            (rule_proposal(r, e, cfg) for r in cfg.get("rules", [])
             if r.get("status", "shadow") == "shadow")
            if p]


def render_destination(template, e, cfg):
    """A rule names a folder, so the rendered path always ends in a separator.

    That trailing slash is the whole convention downstream: `apply.py` reads it
    as "put the file in here", and a destination without one is a full path,
    i.e. a rename. No guessing from whether the folder happens to exist yet.
    """
    fields = {
        "doc_year": e.get("doc_year") or "undated",
        "ext": (e.get("ext") or "").lstrip("."),
        "name": os.path.basename(e["path"]),
        "stem": os.path.splitext(os.path.basename(e["path"]))[0],
        "entity": e.get("_entity") or "",
    }
    try:
        out = template.format(**fields)
    except (KeyError, IndexError):
        return None
    full = out if os.path.isabs(out) else os.path.join(cfg["root"], out)
    return full.rstrip(os.sep) + os.sep


# ------------------------------------------------------------------ main ----
def route_entry(e, cfg, led):
    """One file in, one lane out — with the reason that put it there."""
    e["_rel"] = os.path.relpath(e["path"], cfg["root"])
    entity, why, rivals = entity_for(e, cfg)
    if entity:
        e["_entity"] = entity.get("bucket") or entity.get("name")

    out = {"path": e["path"], "ext": e.get("ext"), "size": e.get("size"),
           "md5": e.get("md5"), "desc": None, "entity": e.get("_entity"),
           "entity_evidence": why}
    shadows = shadow_proposals(e, cfg)
    if shadows:
        out["shadow"] = shadows

    if e.get("known_as"):
        return {**out, "lane": "skip", "rule": "guard:already-in-ledger",
                "reason": f"same content already recorded as {e['known_as']}",
                "desc": e.get("known_desc")}

    sens = sensitive_hit(e, cfg)
    if sens:
        return {**out, "lane": "propose", "rule": f"guard:sensitive:{sens}",
                "reason": "sensitive — the agent reads it before anything happens",
                "sensitive": True}

    if e.get("duplicate_of"):
        return {**out, "lane": "propose", "rule": "guard:byte-identical",
                "reason": f"byte-identical to {e['duplicate_of'][0]}",
                "duplicate_of": e["duplicate_of"]}

    if rivals:
        return {**out, "lane": "propose", "rule": "guard:entity-ambiguous",
                "reason": "claimed by " + " and ".join(rivals)
                          + " on evidence of equal strength — reading decides, not routing",
                "entities": rivals}

    # Shadows were already collected above; only active rules decide a lane.
    for rule in cfg.get("rules", []):
        if rule.get("status", "shadow") != "active":
            continue
        p = rule_proposal(rule, e, cfg)
        if not p:
            continue
        weak = p.pop("weak")
        return {**out, **p,
                "reason": ("matched on the filename alone" if weak
                           else f"rule {rule['id']}")}

    if not e.get("text"):
        return {**out, "lane": "residual", "rule": "guard:no-text",
                "reason": e.get("opaque") or ("image" if e.get("image") else "render"),
                "render": e.get("render"), "image": e.get("image", False)}

    return {**out, "lane": "residual", "rule": None, "reason": "no rule matched"}


def bump_counter(cfg_path, field, counts):
    """Add to a per-rule counter in place, creating it when it is missing.

    A targeted line edit rather than a YAML round-trip: dumping the file back
    would strip every comment, and the comments are half the contract.

    Two things this has to survive, both of which silently lost every score
    before: a counter written with a trailing comment (`hits: 47  # bumped by
    route.py`, which the shipped example itself uses), and a rule that simply
    has no such line yet.
    """
    if not counts:
        return
    with open(cfg_path, encoding="utf-8") as f:
        lines = f.readlines()
    pat = re.compile(rf"(\s*){field}:\s*(\d+)\s*(#.*)?$")
    out, current, indent, done, anchor = [], None, "    ", set(), None

    def flush(at):
        """Insert the counter under the rule it belongs to, if it was absent."""
        if current and current in counts and current not in done:
            out.insert(at, f"{indent}{field}: {counts[current]}\n")
            done.add(current)

    for line in lines:
        m = re.match(r"(\s*)-\s+id:\s*(\S+)", line)
        if m:
            flush(anchor if anchor is not None else len(out))
            current = m.group(2).strip("'\"")
            indent = m.group(1) + "  "
            anchor = None
            out.append(line)
            continue
        if current in counts and current not in done:
            h = pat.match(line)
            if h:
                out.append(f"{h.group(1)}{field}: {int(h.group(2)) + counts[current]}"
                           + (f"  {h.group(3)}" if h.group(3) else "") + "\n")
                done.add(current)
                continue
            if re.match(rf"{re.escape(indent)}\S", line):
                anchor = len(out)      # last line still inside this rule
        out.append(line)
    flush(anchor if anchor is not None else len(out))

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.writelines(out)


def main():
    dry = "--dry-run" in sys.argv
    cfg = require_config()
    led = Ledger(root=cfg["root"])
    batch = os.path.join(cfg["workspace"], "batch")
    preps = sorted(f for f in os.listdir(os.path.join(batch, "prep"))
                   if f.endswith(".json")) if os.path.isdir(os.path.join(batch, "prep")) else []
    if not preps:
        sys.exit("nothing prepared — run prepare.py first")

    unknown = {r.get("id"): r.get("status") for r in cfg.get("rules", [])
               if r.get("status", "shadow") not in KNOWN_STATUS}
    if unknown:
        print(f"warning: unknown rule status, these rules do nothing: {unknown}\n"
              f"         expected one of {KNOWN_STATUS}", file=sys.stderr)

    results, counts, by_rule, by_shadow = [], {k: 0 for k in LANES}, {}, {}
    for pf in preps:
        for e in json.load(open(os.path.join(batch, "prep", pf), encoding="utf-8")):
            r = route_entry(e, cfg, led)
            counts[r["lane"]] += 1
            if r.get("rule") and not r["rule"].startswith("guard:"):
                by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + 1
            for s in r.get("shadow", []):
                by_shadow[s["rule"]] = by_shadow.get(s["rule"], 0) + 1
            results.append(r)

    payload = {"pass": pass_id(led), "counts": counts, "by_rule": by_rule,
               "by_shadow": by_shadow, "files": results}
    if dry:
        # The dry run is the safety device; it has no business hiding 87% of
        # what it found. The summary stays readable, the full routing goes to a
        # log — which is the only artefact it writes.
        log_dir = os.path.join(batch, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log = os.path.join(log_dir, "route-dry-run.json")
        with open(log, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(json.dumps({**payload, "files": results[:20],
                          "note": f"dry run — {len(results)} files, first 20 shown here, "
                                  f"all of them in {log}, nothing else written"},
                         ensure_ascii=False, indent=1))
        return
    with open(os.path.join(batch, "routed.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    cfg_path = os.path.join(cfg["workspace"], "config.yaml")
    bump_counter(cfg_path, "hits", {**by_rule, **by_shadow})
    print(json.dumps({"pass": payload["pass"], "counts": counts, "by_rule": by_rule,
                      "by_shadow": by_shadow,
                      "out": os.path.join(batch, "routed.json")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
