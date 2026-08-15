#!/usr/bin/env python3
"""Step ③ and the whole rule lifecycle: triage, learning, audit.

Four verbs:

    route.py                     fill the triage columns of bench/routing.json
    route.py --learn             close the pass: score shadows against final
                                 paths, mine candidates, report ripe and
                                 unanswered, check anchors, archive the bench
    route.py --audit <rule-id>   replay a rule against memory (ground truth)
    route.py --full-audit <id>   replay a rule against the whole root

Every file leaves the default verb in exactly one lane: `route` (a rule
recognised it), `propose` (needs eyes), `residual` (nothing matched), `skip`
(content already recorded). Guards run ahead of the rules and cannot be
overridden by them; a rule that only knows a filename never reaches `route` —
a name is hearsay, the bytes are the evidence.

The default verb writes columns in bench/ and nothing else, so it has no
--dry-run; dry-run belongs to apply.py, the only script that touches files.
"""
import json
import os
import re
import shutil
import string
import sys
from fnmatch import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract  # noqa: E402
# the condition engine lives in extract.py: apply.py's trash probe runs the
# SAME matcher, so there is exactly one notion of what a condition means
from extract import COND, matches, sensitive_hit, _id_values, _squeeze  # noqa: E402
from memory import (Memory, is_inside, nfc, norm, pass_id, rel_key,  # noqa: E402
                    require_config, self_ingestion_guard, write_json_atomic)

TRIAGE = ("route", "propose", "residual", "skip")
WEAK = {"name_matches", "path_under", "ext", "ext_in", "size_gt", "size_lt"}
IDS = {"ids_any", "id_kind_present"}
# narrower than extract.TEXT_EXT on purpose: --full-audit reads documents,
# not code checkouts
TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".html", ".htm",
            ".eml", ".yml", ".yaml"}
NGRAM_MIN, NGRAM_MAX = 2, 5
FULL_AUDIT_CAP = 500


# ------------------------------------------------------------ conditions ----
def match_strength(cond, e):
    """The strength of the evidence that actually fired. 0 when nothing matched.

    Graded on the branch that MATCHED, never on the branch that was written: an
    `any:` mixing a content test with a filename test is only as strong as
    whichever one fired. Reading the declared conditions instead would let a
    filename pass for content.

        3  validated identifier read in the content
        2  words read in the content
        1  the path or the filename — hearsay
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
            best = max(best, 3 if key in IDS else 1 if key in WEAK else 2)
    return best or 1


def cheap_match(cond, e):
    """The path-only projection of a condition, for counting candidates.

    Content tests count as true until the bytes are read, and `not` never
    prunes: a candidate wrongly kept costs one extraction, a candidate wrongly
    dropped costs the audit its point.
    """
    if not isinstance(cond, dict) or not cond:
        return False
    for key, val in cond.items():
        if key == "all":
            if not all(cheap_match(c, e) for c in val):
                return False
        elif key == "any":
            if not any(cheap_match(c, e) for c in val):
                return False
        elif key in WEAK:
            if not COND[key](e, val):
                return False
    return True


# ---------------------------------------------------------------- guards ----
def inbox_of(e, cfg):
    for ib in cfg.get("inboxes") or []:
        p = ib.get("path") if isinstance(ib, dict) else ib
        if not p:
            continue
        ap = os.path.expanduser(p)
        if not os.path.isabs(ap):
            ap = os.path.join(cfg["root"], ap)
        if is_inside(e["path"], ap):
            return p
    return None


def entity_candidates(e, cfg):
    """Every entity this document could belong to, graded by what proves it.

    Documents routinely name more than one entity — an invoice from a company
    also carries the owner's own name — so the question is never "which entity
    matches" but "which evidence is strongest".
    """
    ids = _id_values(e)
    out = []
    for ent in cfg.get("entities") or []:
        if ids & {_squeeze(x) for x in (ent.get("ids") or [])}:
            out.append((3, ent))
            continue
        s = match_strength(ent.get("match") or {}, e)
        if s:
            out.append((s, ent))
    return out


def entity_for(e, cfg):
    """The entity that won, and the rivals it could not beat.

    The strongest evidence wins outright — an identifier beats a name, a name
    beats a folder — so config order decides nothing. A tie at the top grade
    means the document genuinely belongs to both as far as the evidence goes
    (a contract between two companies), and that is a reading, not a routing:
    the caller sends it to `propose` instead of picking one in silence.
    """
    cands = entity_candidates(e, cfg)
    if not cands:
        return None, []
    top = max(s for s, _ in cands)
    best = [ent for s, ent in cands if s == top]
    if len(best) == 1:
        return best[0], []
    return None, [b.get("name") for b in best]


# ------------------------------------------------------------ destination ----
def render_destination(template, e):
    """(rendered folder, None) — or (None, the variable that stopped it).

    A rule names a folder, so the result always ends in the separator:
    apply.py reads that as "put the file in here". A variable with nothing
    behind it ({doc_year} on an undated file) does not degrade into a folder
    called `undated`: the rule simply does not fire, and the entry goes to
    `propose` carrying the variable's name.
    """
    fields = {"doc_year": e.get("doc_year"),
              "ext": (e.get("ext") or "").lstrip("."),
              "name": os.path.basename(e["path"]),
              "stem": os.path.splitext(os.path.basename(e["path"]))[0],
              "entity": e.get("_entity")}
    for _, var, _, _ in string.Formatter().parse(template):
        if var is None:
            continue
        if var not in fields or fields[var] in (None, ""):
            return None, var or "positional"
    return template.format(**fields).rstrip(os.sep) + os.sep, None


def shadow_predictions(e, cfg):
    """What the shadow rules would have done — collected before the guards.

    A guard settles the file's lane, but it must not settle what the rules get
    to learn from. Collecting shadows only on files no guard touched would
    blind every candidate rule to sensitive documents and duplicates — most of
    an administrative corpus — so no rule could ever earn promotion.
    """
    out = []
    for r in cfg.get("rules") or []:
        if r.get("status", "shadow") != "shadow":
            continue
        s = match_strength(r.get("when") or {}, e)
        if not s:
            continue
        p = {"rule": r["id"], "strength": s}
        if r.get("destination"):
            dest, missing = render_destination(r["destination"], e)
            p["destination"] = dest
            if missing:
                p["unresolved"] = missing
        if r.get("tags"):
            p["tags"] = r["tags"]
        out.append(p)
    return out


# ----------------------------------------------------------- default verb ----
def route_entry(e, cfg):
    """One file in, its triage columns out — with the reason on every branch."""
    e["_rel"] = rel_key(e["path"], cfg["root"])
    entity, rivals = entity_for(e, cfg)
    if entity:
        e["_entity"] = entity.get("bucket") or entity.get("name")
    cols = {"triage": None, "why": None, "guards": [], "rule": None,
            "entity": e.get("_entity"), "strength": None, "destination": None,
            "shadow": shadow_predictions(e, cfg)}

    checks = []
    if e.get("known_as"):
        checks.append(("skip", "skip",
                       f"same content already recorded as {e['known_as']}"))
    hit = sensitive_hit(e, cfg)
    if hit:
        checks.append(("sensitive", "propose", f"sensitive ({hit})"))
    if e.get("duplicate_of"):
        d = e["duplicate_of"]
        twin = d[0] if isinstance(d, list) and d else str(d)
        # the twin may be in the bin an hour from now, and this sentence is
        # about to become a memory line: name the content too, since the md5
        # keeps resolving (`memory.py show <md5>`) after the path stops
        checks.append(("duplicate", "propose", f"byte-identical to {twin}"
                       + (f" (md5 {e['md5']})" if e.get("md5") else "")))
    ib = inbox_of(e, cfg)
    if ib:
        checks.append(("inbox", "propose",
                       f"sitting in inbox {ib} — never a silent route"))
    if rivals:
        checks.append(("entity-tie", "propose",
                       "claimed by " + " and ".join(rivals)
                       + " on evidence of equal strength — reading decides"))
    cols["guards"] = [c[0] for c in checks]
    if checks:
        cols["triage"], cols["why"] = checks[0][1], checks[0][2]
        return cols

    for rule in cfg.get("rules") or []:
        if rule.get("status", "shadow") != "active":
            continue
        strength = match_strength(rule.get("when") or {}, e)
        if not strength:
            continue
        dest, missing = (render_destination(rule["destination"], e)
                         if rule.get("destination") else (None, None))
        cols["rule"], cols["strength"], cols["destination"] = rule["id"], strength, dest
        if missing is not None:
            cols["triage"] = "propose"
            cols["why"] = f"destination variable unresolved: {missing}"
        elif strength <= 1:
            cols["triage"] = "propose"
            cols["why"] = f"rule {rule['id']} matched on the path or filename alone"
        else:
            cols["triage"] = "route"
            cols["why"] = f"rule {rule['id']} (evidence strength {strength})"
        return cols

    cols["triage"] = "residual"
    if e.get("link_to"):
        cols["why"] = ("symlink -> %s — a pointer, not a document"
                       % rel_key(e["link_to"], cfg["root"]))
        return cols
    cols["why"] = (e.get("error") or e.get("opaque") or "no text to reason on"
                   if not (e.get("text") or "").strip() else "no rule matched")
    return cols


def load_routing(bench):
    path = os.path.join(bench, "routing.json")
    if not os.path.exists(path):
        sys.exit("no bench/routing.json — run collect.py first")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data if isinstance(data, list) else data.get("entries") or []
    return data, entries


def rel_to_root(p, cfg):
    """The memory key for a path — the same one collect and apply write."""
    return rel_key(p, cfg["root"])


def rel_dest(dest, cfg):
    """A destination as memory records paths: relative, trailing sep kept."""
    if os.path.isabs(dest) and is_inside(dest, cfg["root"]):
        return os.path.relpath(dest, cfg["root"]) + os.sep
    return dest


def cmd_route(cfg):
    bench = os.path.join(cfg["workspace"], "bench")
    data, entries = load_routing(bench)

    # Two ways past the barrier. Withdrawal: `decision: "unanswered"` with a
    # reason listing every read attempt (render p1, Read on the original,
    # --render on other pages, conversion) — never triaged, never `lu`, comes
    # back FIRST next pass. Or a real decision already on the entry: the user
    # answered about a withdrawn file, and a human judgement is the strongest
    # evidence there is — that entry is triaged `propose` below, not blocked.
    # A `known_as` entry is exempt: its md5 is already in memory, so the FIRST
    # guard triages it `skip` on identity alone and never looks at the text.
    # The barrier forbids judgement on unread bytes; recognising bytes already
    # read in an earlier pass is not a judgement on them. Binning such a file
    # still requires reading it — apply's probe refuses a trash with no
    # readable text unless `reviewed: "vision"`, and always for a sensitive.
    blocked = [e["path"] for e in entries
               if e.get("needs_vision") and not (e.get("text") or "").strip()
               and not e.get("decision") and not e.get("known_as")]
    if blocked:
        print("vision barrier — no judgement on unread bytes; still unread:",
              file=sys.stderr)
        for p in blocked:
            print("  " + p, file=sys.stderr)
        print("read them (render, Read, --render, conversion) or withdraw them:"
              ' decision "unanswered" with the attempts in `reason`',
              file=sys.stderr)
        sys.exit(2)

    unknown = {r.get("id"): r.get("status") for r in cfg.get("rules") or []
               if r.get("status", "shadow") not in ("shadow", "active")}
    if unknown:
        print(f"warning: unknown rule status, these rules are inert: {unknown}\n"
              "         expected shadow or active", file=sys.stderr)

    counts, by_rule, by_shadow = {t: 0 for t in TRIAGE}, {}, {}
    active_ids = {r.get("id") for r in cfg.get("rules") or []
                  if r.get("status", "shadow") == "active"}
    withdrawn = 0
    for e in entries:
        if e.get("decision") == "unanswered":
            withdrawn += 1          # withdrawn, unread: no triage on no evidence
            continue
        if e.get("needs_vision") and not (e.get("text") or "").strip() \
                and e.get("decision"):
            # the user answered about a file nothing could read: no judgement
            # on the bytes, but the decision itself is evidence — the human
            # judged. That is `propose` by definition, and it closes the pass.
            e.update({"triage": "propose",
                      "why": f"user decision {e['decision']!r} on an unread "
                             "entry — the human judged it",
                      "guards": [], "rule": None, "entity": None,
                      "strength": None, "destination": None, "shadow": []})
            counts["propose"] += 1
            continue
        cols = route_entry(e, cfg)
        # A shadow rule OBSERVES. It may fill the `shadow` column and nothing
        # else — never a triage, never a destination, never a gesture. That is
        # what makes a candidate rule safe to leave running for months on a
        # real corpus, and it is checked here rather than merely intended: if a
        # non-active rule ever reached the acting columns, the pass stops.
        if cols["rule"] and cols["rule"] not in active_ids:
            sys.exit("BUG: rule %r is not active but reached the routing "
                     "columns — a shadow rule must never act" % cols["rule"])
        for k in ("_flat", "_rel", "_entity"):
            e.pop(k, None)
        e.update(cols)
        counts[cols["triage"]] += 1
        if cols["rule"]:
            by_rule[cols["rule"]] = by_rule.get(cols["rule"], 0) + 1
        for s in cols["shadow"]:
            by_shadow[s["rule"]] = by_shadow.get(s["rule"], 0) + 1
    write_json_atomic(os.path.join(bench, "routing.json"), data)
    print(json.dumps({"entries": len(entries), "triage": counts,
                      "withdrawn": withdrawn,
                      "by_rule": by_rule, "by_shadow": by_shadow,
                      "routing": os.path.join(bench, "routing.json")},
                     ensure_ascii=False, indent=1))


# --------------------------------------------------------- config editing ----
def bump_counter(cfg_path, field, counts):
    """Add to a per-rule counter in place, creating it when it is missing.

    A targeted line edit rather than a YAML round-trip: dumping the file back
    would strip every comment, and the comments are half the contract.

    Two things this has to survive, both of which silently lost every score
    before: a counter written with a trailing comment (`hits: 47  # bumped`),
    and a rule that simply has no such line yet.
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


def append_rules(cfg_path, rules):
    """Write born rules under `rules:` — same no-round-trip reason as above."""
    with open(cfg_path, encoding="utf-8") as f:
        lines = f.readlines()
    idx, item_indent = None, None
    for i, line in enumerate(lines):
        if re.match(r"rules:\s*(\[\]\s*)?(#.*)?$", line):
            idx = i
            for j in range(i + 1, len(lines)):
                if re.match(r"[A-Za-z_\"']", lines[j]):
                    break
                m = re.match(r"(\s+)- ", lines[j])
                if m:
                    item_indent = m.group(1)
                    break
    ind = item_indent or "  "
    block = []
    for r in rules:
        block.append(f"{ind}- id: {r['id']}\n")
        for k in ("status", "cycle", "passes", "hits", "agreed", "disagreed"):
            block.append(f"{ind}  {k}: {r[k]}\n")
        block.append(f"{ind}  learned_from: "
                     f"{json.dumps(r['learned_from'], ensure_ascii=False)}\n")
        block.append(f"{ind}  history: []\n")
        block.append(f"{ind}  when: {json.dumps(r['when'], ensure_ascii=False)}\n")
        block.append(f"{ind}  destination: "
                     f"{json.dumps(r['destination'], ensure_ascii=False)}\n")
    if idx is None:
        lines += ["rules:\n"] + block
    else:
        if re.match(r"rules:\s*\[\]", lines[idx]):
            lines[idx] = "rules:\n"
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if re.match(r"[A-Za-z_\"']", lines[j]):
                end = j
                break
        lines[end:end] = block
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ------------------------------------------------------------------ learn ----
def ngrams(text, lo=NGRAM_MIN, hi=NGRAM_MAX):
    words = re.findall(r"[\w'’-]+", text or "")
    return {" ".join(words[i:i + n])
            for n in range(lo, hi + 1) for i in range(len(words) - n + 1)}


def score_shadows(entries, cfg):
    """Every shadow prediction against where the file actually ENDED UP.

    Judged on the final path, not on whether the file moved: a rule naming the
    folder a document already sits in is right, and counting that as a
    divergence would mark every correct rule wrong on a tidy corpus — the
    normal state after the first pass — so no rule could ever be promoted.
    """
    hits, agreed, disagreed, diverging = {}, {}, {}, {}
    for e in entries:
        for s in e.get("shadow") or []:
            rid = s["rule"]
            hits[rid] = hits.get(rid, 0) + 1
            if not e.get("result") or e.get("result") == "failed":
                continue                  # the pass never settled this file
            final = rel_to_root(e.get("final") or e["path"], cfg)
            if s.get("destination"):
                ok = norm(final).startswith(norm(rel_dest(s["destination"], cfg)))
            elif s.get("tags"):
                ok = set(s["tags"]) <= set(e.get("tags") or [])
            else:
                continue                  # matched, but proposed nothing checkable
            if ok:
                agreed[rid] = agreed.get(rid, 0) + 1
            else:
                disagreed[rid] = disagreed.get(rid, 0) + 1
                diverging.setdefault(rid, []).append(
                    f"{final} (rule wanted {s.get('destination') or s.get('tags')})")
    return hits, agreed, disagreed, diverging


def mine_rules(entries, mem, cfg, pass_name):
    """Rule candidates from this pass's moves — simplest form first, kept only
    with zero counterexamples.

    A phrase that merely sounds discriminating is how a rule quietly swallows
    the wrong documents; every form here is measured against every other file
    of the pass — and, for identifiers, against memory — before it is born.
    """
    groups = {}
    for e in entries:
        if e.get("result") == "moved" and e.get("final"):
            groups.setdefault(os.path.dirname(rel_to_root(e["final"], cfg)),
                              []).append(e)

    existing = {r.get("id") for r in cfg.get("rules") or []}
    born = []
    for dest, group in sorted(groups.items()):
        if len(group) < 3 or not dest:
            continue
        member = {g["path"] for g in group}
        others = [o for o in entries if o.get("path") not in member]
        texts = [norm(g.get("text") or "") for g in group]
        common = (set.intersection(*(ngrams(t) for t in texts))
                  if texts and all(texts) else set())
        exts = {g.get("ext") for g in group}
        srcs = {os.path.dirname(rel_to_root(g["path"], cfg)) for g in group}
        when = None

        # ① one ext out of one folder, nothing else of that kind went elsewhere
        if len(exts) == 1 and len(srcs) == 1 and next(iter(srcs)):
            ext, src = next(iter(exts)), next(iter(srcs))
            if not any(o.get("ext") == ext
                       and os.path.dirname(rel_to_root(o["path"], cfg)) == src
                       for o in others):
                when = {"ext": [ext], "path_under": [src]}

        # ② a filename prefix no other file of the pass carries
        if when is None:
            names = [norm(os.path.basename(g["path"])) for g in group]
            p = os.path.commonprefix(names)
            if len(p) >= 6 and not any(
                    norm(os.path.basename(o["path"])).startswith(p) for o in others):
                when = {"name_matches": "^" + re.escape(p)}

        # ③ a phrase in every file that went here, in no other file of the pass
        elsewhere = set()
        for o in others:
            if o.get("text"):
                elsewhere |= ngrams(norm(o["text"]))
        if when is None and common:
            unique = common - elsewhere
            if unique:
                when = {"text_contains_any": [min(unique, key=lambda s: (len(s), s))]}

        # ④ an identifier every file carries, seen nowhere else — pass AND memory
        if when is None and all(g.get("ids") for g in group):
            shared = set.intersection(*(_id_values(g) for g in group))
            for v in sorted(shared):
                if any(v in _id_values(o) for o in others):
                    continue
                if any(v in _id_values(r)
                       and not norm(r.get("path", "")).startswith(norm(dest + os.sep))
                       for r in mem.by_path.values()):
                    continue
                when = {"ids_any": [v]}
                break

        # ⑤ ext + phrase: the phrase only has to beat files of the same ext
        if when is None and common and len(exts) == 1:
            ext = next(iter(exts))
            same_ext = set()
            for o in others:
                if o.get("ext") == ext and o.get("text"):
                    same_ext |= ngrams(norm(o["text"]))
            unique = common - same_ext
            if unique:
                when = {"ext": [ext],
                        "text_contains_any": [min(unique, key=lambda s: (len(s), s))]}

        if when is None:
            continue

        # generalise a year segment so next year's files still match
        template = dest
        years = {g.get("doc_year") for g in group}
        if len(years) == 1 and None not in years:
            y = str(next(iter(years)))
            head, _, last = dest.rpartition(os.sep)
            if y in last:
                template = (head + os.sep if head else "") \
                    + last.replace(y, "{doc_year}", 1)

        base = re.sub(r"[^a-z0-9]+", "-",
                      norm(re.sub(r"\{[^}]*\}", "",
                                  dest.split(os.sep)[-1]))).strip("-") or "rule"
        rid, n = base, 2
        while rid in existing:
            rid, n = f"{base}-{n}", n + 1
        existing.add(rid)
        born.append({"id": rid, "status": "shadow", "cycle": 1, "passes": 0,
                     "hits": 0, "agreed": 0, "disagreed": 0,
                     "learned_from": f"{pass_name}: {len(group)} moves to {dest}",
                     "when": when, "destination": template})
    return born


def rule_report(cfg, hits, agreed, disagreed):
    """Shadow rules ripe for promotion, and the ones the lifecycle should bury.

    Counters here are pre-bump values plus this pass's increments — the same
    totals bump_counter just wrote, without re-reading the file.
    """
    th = {"min_passes": 5, "min_hits": 5, "max_cycles": 3,
          "dead_after_passes": 10, **(cfg.get("learning") or {})}
    ripe, retire = [], []
    for r in cfg.get("rules") or []:
        if r.get("status", "shadow") != "shadow":
            continue
        rid = r.get("id")
        passes = int(r.get("passes") or 0) + (1 if rid in hits else 0)
        h = int(r.get("hits") or 0) + hits.get(rid, 0)
        good = int(r.get("agreed") or 0) + agreed.get(rid, 0)
        bad = int(r.get("disagreed") or 0) + disagreed.get(rid, 0)
        if passes >= th["min_passes"] and h >= th["min_hits"] and bad == 0:
            ripe.append({"id": rid, "passes": passes, "hits": h, "agreed": good,
                         "when": r.get("when"), "destination": r.get("destination")})
        if (int(r.get("cycle") or 1) > th["max_cycles"]
                or (passes >= th["dead_after_passes"] and h == 0)):
            retire.append({"id": rid, "cycle": r.get("cycle"),
                           "passes": passes, "hits": h})
    return ripe, retire


def pass_of(entries, mem, cfg):
    """The pass being closed, read off the memory lines apply just wrote."""
    votes = {}
    for e in entries:
        rec = mem.by_path.get(nfc(rel_to_root(e.get("final") or e["path"], cfg)))
        if rec and rec.get("pass"):
            votes[rec["pass"]] = votes.get(rec["pass"], 0) + 1
    return max(votes, key=votes.get) if votes else pass_id(mem)


def check_anchors(cfg):
    """Every backticked path in the instruction files must still resolve — a
    dead pointer is a fact that left the instructions and arrived nowhere."""
    warnings = []
    for anchor in cfg.get("anchors") or []:
        ap = os.path.expanduser(anchor)
        if not os.path.isabs(ap):
            ap = os.path.join(cfg["root"], ap)
        try:
            with open(ap, encoding="utf-8") as f:
                body = f.read()
        except FileNotFoundError:
            warnings.append(f"{anchor}: the anchor file itself does not resolve")
            continue
        except OSError as err:
            # "does not resolve" reads as "was deleted", and a deleted anchor
            # is a very plausible wrong conclusion when the truth is that the
            # OS refused this process — say which one it is.
            warnings.append(f"{anchor}: the anchor file cannot be read "
                            f"({err.strerror or err}) — it exists, this process"
                            " is not allowed to open it")
            continue
        for tok in re.findall(r"`([^`\n]+)`", body):
            if not (tok.startswith(("~", "/")) or os.sep in tok):
                continue                  # backticked code, not a path
            if re.search(r"[\s*?{$]", tok):
                continue                  # globs and variables are not checkable
            p = os.path.expanduser(tok)
            if not os.path.isabs(p):
                p = os.path.join(cfg["root"], p)
            if not os.path.lexists(p):
                warnings.append(f"{anchor}: `{tok}` no longer resolves")
    return warnings


def archive_bench(ws, pass_name):
    """bench/ moves whole to logs/<pass>/ — archived, never deleted."""
    bench = os.path.join(ws, "bench")
    dest, n = os.path.join(ws, "logs", pass_name), 2
    while os.path.exists(dest):
        dest, n = os.path.join(ws, "logs", f"{pass_name}-{n}"), n + 1
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(bench, dest)
    return dest


def cmd_learn(cfg):
    ws = cfg["workspace"]
    data, entries = load_routing(os.path.join(ws, "bench"))
    # A result stamped without a triage is the answered-withdrawal path: the
    # user decided about a file nothing could read, apply performed it. The
    # decision stands in for the triage (a human judgement IS `propose`);
    # refusing to close the pass over it would wedge every such answer.
    if any("triage" not in e and e.get("decision") != "unanswered"
           and not e.get("result") for e in entries):
        sys.exit("routing.json has entries without a triage — run route.py first"
                 " (withdrawn and already-applied entries are the exceptions)")
    answered = [nfc(rel_to_root(e["path"], cfg)) for e in entries
                if "triage" not in e and e.get("result")]
    mem = Memory(root=cfg["root"])
    pass_name = pass_of(entries, mem, cfg)
    cfg_path = os.path.join(ws, "config.yaml")

    hits, agreed, disagreed, diverging = score_shadows(entries, cfg)
    bump_counter(cfg_path, "hits", hits)
    bump_counter(cfg_path, "agreed", agreed)
    bump_counter(cfg_path, "disagreed", disagreed)
    bump_counter(cfg_path, "passes", {rid: 1 for rid in hits})

    born = mine_rules(entries, mem, cfg, pass_name)
    if born:
        append_rules(cfg_path, born)
    ripe, retire = rule_report(cfg, hits, agreed, disagreed)

    # unanswered BY NAME, and their memory lines so selection re-picks them first
    named = []
    for e in entries:
        if e.get("triage") == "skip":
            continue                      # "already recorded" is an answer
        if e.get("result") and e.get("decision") != "unanswered":
            continue
        rp = nfc(rel_to_root(e["path"], cfg))
        named.append(rp)
        prev = mem.by_path.get(rp)
        if prev and prev.get("pass") == pass_name \
                and prev.get("decision") == "unanswered":
            continue                      # apply already wrote this line
        mem.append(mem.record(
            e["path"], pass_id=pass_name,
            triage=e.get("triage") or "residual", decision="unanswered",
            # a withdrawal carries its read attempts in `reason` — archived
            # here so the next pass knows what was already tried
            reason=e.get("reason") or e.get("why")
            or "reached the end of the pass without a decision",
            size=e.get("size"), mtime=e.get("mtime"), md5=e.get("md5"),
            provenance="pass"))

    warnings = check_anchors(cfg)
    archived = archive_bench(ws, pass_name)

    print(json.dumps({"pass": pass_name, "entries": len(entries),
                      "shadow_rules_scored": sorted(hits),
                      "born": [r["id"] for r in born],
                      "ripe": [r["id"] for r in ripe],
                      "retire": retire, "unanswered": named,
                      "answered_withdrawals": answered,
                      "anchor_warnings": warnings, "archived": archived},
                     ensure_ascii=False, indent=1))

    if diverging:
        print("\nshadow rules that diverged from what was decided:")
        for rid, exs in sorted(diverging.items(), key=lambda kv: -len(kv[1])):
            print(f"  {rid}: {len(exs)} disagreement(s), {agreed.get(rid, 0)} agreement(s)")
            for ex in exs[:3]:
                print(f"      {ex}")
        print("  a rule that keeps diverging is wrong about these documents, not "
              "the other way round — rewrite it or drop it.")
    if ripe:
        print("\nready for a decision — these ran in shadow and never diverged:")
        for r in ripe:
            print(f"  {r['id']}  ·  {r['passes']} pass(es), {r['hits']} hit(s), "
                  f"{r['agreed']} agreed, 0 disagreed")
            print(f"    when         {json.dumps(r['when'], ensure_ascii=False)}")
            print(f"    destination  {r['destination']}")
        print("  promotion is the user's act, after --full-audit of each.")
    if born:
        print("\nborn in shadow this pass:")
        for r in born:
            print(f"  {r['id']}: {json.dumps(r['when'], ensure_ascii=False)}"
                  f" -> {r['destination']}   ({r['learned_from']})")


# ------------------------------------------------------------------ audit ----
def find_rule(cfg, rid):
    for r in cfg.get("rules") or []:
        if r.get("id") == rid:
            return r
    sys.exit(f"no rule {rid!r} in config.yaml")


def audit_entry(rp, rec, cfg):
    """A memory line dressed as a routing entry. The recorded description
    stands in for the text: the bytes left the bench long ago, the reading the
    pass kept of them is the evidence that remains."""
    y = str(rec.get("date_doc") or "")[:4]
    ap = os.path.expanduser(rp)                # `~/…` keys: inbox files
    return {"path": ap if os.path.isabs(ap) else os.path.join(cfg["root"], ap),
            "_rel": rp, "ext": os.path.splitext(rp)[1].lower(),
            "size": rec.get("size") or 0, "text": rec.get("desc") or "",
            "ids": rec.get("ids") or {},
            "doc_year": int(y) if y.isdigit() else None}


def cmd_audit(cfg, rid):
    rule = find_rule(cfg, rid)
    mem = Memory(root=cfg["root"])
    matched = agreed = disagreed = unjudged = 0
    diverging = []
    for rp, rec in sorted(mem.by_path.items()):
        if rec.get("decision") == "unanswered":
            continue                      # no ground truth there yet
        e = audit_entry(rp, rec, cfg)
        if not match_strength(rule.get("when") or {}, e):
            continue
        matched += 1
        dest = None
        if rule.get("destination"):
            dest, missing = render_destination(rule["destination"], e)
            if missing:
                unjudged += 1
                continue
            ok = norm(rp).startswith(norm(rel_dest(dest, cfg)))
        elif rule.get("tags"):
            ok = set(rule["tags"]) <= set(rec.get("tags") or [])
        else:
            unjudged += 1
            continue
        if ok:
            agreed += 1
        else:
            disagreed += 1
            diverging.append(f"{rp} -> {rec.get('decision')}"
                             + (f" (rule wanted {dest})" if dest else ""))
    print(json.dumps({"rule": rid, "records": len(mem.by_path),
                      "matched": matched, "agreed": agreed,
                      "disagreed": disagreed, "unjudged": unjudged,
                      "note": "text conditions ran against recorded descriptions"
                              " — the bytes are gone; --full-audit reads the disk"},
                     ensure_ascii=False, indent=1))
    for d in diverging:
        print("  " + d)


# ------------------------------------------------------------- full audit ----
def content_text(path, ext):
    """First-page text, the same evidence collect.py reads.

    None means unreadable here (minimal mode, broken file) — distinct from "",
    which means read and empty.
    """
    if ext == ".pdf":
        return extract.pdf_text(path)
    if ext in extract.ZIP_XML:
        return extract.zip_text(path, extract.ZIP_XML[ext])
    if ext == ".rtf":
        return extract.rtf_text(path)
    if ext in TEXT_EXT:
        return extract.read_text_file(path)
    return ""


def walk_candidates(cfg, when):
    """Path-level pass over the whole root: everything the rule COULD touch,
    counted before a single byte of content is read."""
    root, pats = cfg["root"], cfg.get("exclude") or []
    guard = self_ingestion_guard(cfg)

    def excluded(rel):
        return any(fnmatch(norm(rel), norm(p))
                   or fnmatch(norm(os.path.basename(rel)), norm(p))
                   for p in pats)

    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".")
            and not any(is_inside(os.path.join(dirpath, d), g) for g in guard)
            and not excluded(os.path.relpath(os.path.join(dirpath, d), root)))
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            if excluded(rel):
                continue
            try:
                size = os.stat(p).st_size
            except OSError:
                continue          # dangling symlink or unreadable: no candidate
            e = {"path": p, "_rel": rel,
                 "ext": os.path.splitext(fn)[1].lower(), "size": size}
            if cheap_match(when, e):
                out.append(e)
    return out


def cmd_full_audit(cfg, rid):
    rule = find_rule(cfg, rid)
    when = rule.get("when") or {}
    cands = sorted(walk_candidates(cfg, when), key=lambda e: e["path"])
    total = len(cands)
    print(f"{total} candidate file(s) before reading any content")
    if len(cands) > FULL_AUDIT_CAP:
        print(f"more than {FULL_AUDIT_CAP} — auditing the first {FULL_AUDIT_CAP} "
              "by path; narrow the rule or sample the rest by hand")
        cands = cands[:FULL_AUDIT_CAP]

    touched, unreadable = [], 0
    for e in cands:
        text = content_text(e["path"], e["ext"])
        if text is None:
            unreadable += 1
            text = ""
        e["text"] = text
        if text:
            e["ids"] = extract.extract_ids(text, cfg)
            e["doc_year"] = extract.doc_year(extract.extract_dates(text))
        s = match_strength(when, e)
        if not s:
            continue
        dest = missing = None
        if rule.get("destination"):
            dest, missing = render_destination(rule["destination"], e)
        touched.append({"path": e["_rel"], "strength": s,
                        "destination": dest
                        or (f"unresolved: {missing}" if missing else None)})

    out = {"rule": rid, "candidates": total, "audited": len(cands),
           "unreadable": unreadable, "touched": touched}
    log_dir = os.path.join(cfg["workspace"], "logs")
    os.makedirs(log_dir, exist_ok=True)
    log = os.path.join(log_dir, f"full-audit-{rid}.json")
    with open(log, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"written to {log} — no ground truth here: the list is for human "
          "judgement, and promotion needs the user's yes on it")


# ------------------------------------------------------------------- main ----
def main():
    args = sys.argv[1:]
    cfg = require_config()
    if not args:
        cmd_route(cfg)
    elif args[0] == "--learn" and len(args) == 1:
        cmd_learn(cfg)
    elif args[0] == "--audit" and len(args) == 2:
        cmd_audit(cfg, args[1])
    elif args[0] == "--full-audit" and len(args) == 2:
        cmd_full_audit(cfg, args[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
