#!/usr/bin/env python3
"""The library of memory.jsonl — the only door to state.

Everything that persists between passes goes through this module: the workspace
location, the config, and the memory itself. No other script opens
`memory.jsonl` directly. It also owns the shared helpers (`nfc`, `norm`,
`flatten`, `file_md5`, `is_inside`, …) that every other module imports — one
normaliser, one hash, one containment check, defined once.

`memory.jsonl` is append-only: one JSON object per line, the durable record of
what the tool knows about every document and why. **by_path is the primary
index** — the last line for a given path wins. by_md5 is secondary: one md5 may
map to a LIST of records (byte-identical duplicates live at several paths).

v1 records carried a `level` field; v2 calls it `triage`. Readers accept both,
writers emit only `triage`.

CLI (read-only — memory.py chooses nothing):
    memory.py stats                 counts, passes, triage, provenance
    memory.py show <path|md5>       the resolved record(s)
    memory.py find <term>           grep descriptions and paths, accent-blind
"""
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime

DEFAULT_WORKSPACE = "~/.wikidoc"
MD5_MAX = 1 << 30           # no hashing past 1 GB (videos)
CHUNK = 1 << 20


# ------------------------------------------------------------------ text ----
def nfc(s):
    return unicodedata.normalize("NFC", s)


def norm(s):
    """THE one text normaliser. Every text comparison in the codebase goes
    through it: NFC -> casefold -> strip combining marks -> collapse
    whitespace. macOS spells `é` two ways and users type it zero ways; a
    pattern that matches "reçu" must also match "RECU" and "reçu".
    """
    s = nfc(s or "").casefold()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def flatten(s):
    """One line, single spaces — the form every pattern is matched against.

    Extractors hand back layout, not prose: pypdf routinely returns a newline
    between every fragment, so a SIREN printed as `123 456 782` arrives with a
    newline inside it and a two-word phrase never matches. The readable text
    keeps its shape for the agent; matching happens on this.
    """
    return re.sub(r"\s+", " ", s or "").strip()


def _tokens(s):
    return {t for t in re.split(r"[^a-z0-9]+", norm(s)) if len(t) > 2}


# ----------------------------------------------------------------- paths ----
def workspace():
    """Where this installation keeps its state. Env wins, then the default."""
    return os.path.abspath(os.path.expanduser(
        os.environ.get("WIKIDOC_HOME", DEFAULT_WORKSPACE)))


def skill_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def segments(path):
    """Comparable segments: realpath, NFC, case-folded. Symlinks resolved."""
    real = os.path.realpath(os.path.expanduser(path))
    return [nfc(s).casefold() for s in real.split(os.sep) if s]


def is_inside(path, container):
    """True if `path` is `container` or lives under it.

    Segment-wise so that `/a/bc` is not read as inside `/a/b`; case-folded and
    NFC-normalised so that macOS's two ways of spelling `é` and its
    case-insensitive volumes cannot slip a file past the guard.
    """
    p, c = segments(path), segments(container)
    return len(p) >= len(c) and p[:len(c)] == c


def self_ingestion_guard(config):
    """Directories the tool must never ingest — derived, never a config list.

    Derived from `workspace:` and from where the skill itself lives, so no one
    can forget the exclusion line and have the tool eat its own memory.
    """
    return [config["workspace"], skill_dir()]


# ---------------------------------------------------------------- config ----
def load_config(ws=None):
    """Read config.yaml. Returns None when the installation is not set up yet."""
    ws = ws or workspace()
    path = os.path.join(ws, "config.yaml")
    if not os.path.exists(path):
        return None
    try:
        import yaml
    except ImportError:
        sys.exit("wikidoc needs PyYAML: python3 -m pip install --user pyyaml")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg["workspace"] = ws
    cfg["root"] = os.path.abspath(os.path.expanduser(cfg.get("root", "~/Documents")))
    cfg.setdefault("batch_size", 500)
    cfg.setdefault("exclude", [])
    cfg.setdefault("inboxes", [])
    cfg.setdefault("anchors", [])
    cfg.setdefault("identifiers", [])
    cfg.setdefault("entities", [])
    cfg.setdefault("rules", [])
    cfg.setdefault("sensitive", {})
    cfg.setdefault("tags", {})
    cfg.setdefault("learning", {})
    cfg.setdefault("language", "en")
    return cfg


def require_config(ws=None):
    cfg = load_config(ws)
    if cfg is None:
        sys.exit("no config.yaml in %s — read SETUP.md and run the bootstrap"
                 % (ws or workspace()))
    return cfg


# ------------------------------------------------------------------- md5 ----
def file_md5(path, size=None):
    """Content hash. None past MD5_MAX or on a read error — never raises."""
    try:
        if size is None:
            size = os.stat(path).st_size
        if size > MD5_MAX:
            return None
        h = hashlib.md5()
        with open(path, "rb") as f:
            for blk in iter(lambda: f.read(CHUNK), b""):
                h.update(blk)
        return h.hexdigest()
    except OSError:
        return None


def pass_id(mem):
    """`YYYY-MM-DD-N` — N counts the passes already recorded today."""
    today = datetime.now().strftime("%Y-%m-%d")
    n = len({r["pass"] for r in mem.by_path.values()
             if str(r.get("pass", "")).startswith(today)}) + 1
    return f"{today}-{n}"


def write_json_atomic(path, data):
    """Temp file + rename: the working file is never half-written."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- memory ----
class Memory:
    """Append-only JSONL. Last line per path is the truth."""

    def __init__(self, ws=None, root=None):
        self.ws = ws or workspace()
        self.path = os.path.join(self.ws, "memory.jsonl")
        self.root = root
        self.by_path = {}      # relpath -> last record, the primary index
        self.by_md5 = {}       # md5 -> [records], duplicates live at many paths
        self.by_stat = {}      # (relpath, size, mtime) -> md5, the no-hash fast path
        self.lines = 0
        self.load()

    # -- reading -------------------------------------------------------------
    def load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                self.lines += 1
                if "triage" not in rec and "level" in rec:
                    rec["triage"] = rec["level"]     # v1 vocabulary, read-side only
                self._index(rec)

    def _index(self, rec):
        path = nfc(rec.get("path", ""))
        self.by_path[path] = rec
        md5 = rec.get("md5")
        if md5:
            group = self.by_md5.setdefault(md5, [])
            # one entry per path in the group; a re-record replaces its own line
            self.by_md5[md5] = [r for r in group if nfc(r.get("path", "")) != path]
            self.by_md5[md5].append(rec)
        if rec.get("size") is not None and rec.get("mtime") is not None:
            # a file whose bytes could not be read (TCC, permissions) still gets
            # its stat line, so the fast path keeps recognising it
            self.by_stat[(path, rec["size"], int(rec["mtime"]))] = md5 or True

    def rel(self, path):
        """Path recorded relative to root — the memory survives a root move."""
        if not os.path.isabs(path):
            return nfc(path)          # already root-relative (apply.py's finals)
        if self.root and is_inside(path, self.root):
            return nfc(os.path.relpath(os.path.realpath(path),
                                       os.path.realpath(self.root)))
        return nfc(os.path.abspath(path))

    def abs(self, relpath):
        if os.path.isabs(relpath):
            return relpath
        return os.path.join(self.root or self.ws, relpath)

    def seen_stat(self, path, size, mtime):
        """Cheap check: same path, same size, same mtime as a recorded pass."""
        return self.by_stat.get((self.rel(path), size, int(mtime)))

    def seen_md5(self, md5):
        """All records sharing this content — survives renames and moves."""
        return self.by_md5.get(md5)

    def resolve(self, key):
        """A path (exact, relative, or suffix) or an md5 -> record(s)."""
        if key in self.by_md5:
            return self.by_md5[key]
        key = nfc(key)
        rec = self.by_path.get(key)
        if rec:
            return rec
        for p, r in self.by_path.items():
            if p.endswith(key):
                return r
        return None

    # -- writing -------------------------------------------------------------
    def record(self, path, *, pass_id, triage, decision, reason, size=None,
               mtime=None, md5=None, desc=None, ids=None, tags=None,
               date_doc=None, provenance="pass"):
        """Build one memory line. Raises ValueError on a desc whose token set
        is a subset of the filename's tokens — a description that only
        rearranges the filename read nothing, and that lie must die where the
        data enters, not three passes later.
        """
        if desc:
            name_t = _tokens(os.path.splitext(os.path.basename(path))[0])
            desc_t = _tokens(desc)
            if name_t and desc_t and desc_t <= name_t:
                raise ValueError(
                    "desc paraphrases the filename, adds nothing: %r" % desc)
        if size is None or mtime is None:
            try:
                st = os.stat(path)
                size = st.st_size if size is None else size
                mtime = int(st.st_mtime) if mtime is None else mtime
            except OSError:
                size, mtime = size or 0, mtime or 0
        if md5 is None:
            md5 = file_md5(path, size)
        rec = {"path": self.rel(path), "pass": pass_id, "triage": triage,
               "decision": decision, "reason": reason, "size": size,
               "mtime": int(mtime), "md5": md5, "provenance": provenance}
        if desc:
            rec["desc"] = desc
        if ids:
            rec["ids"] = ids
        if tags:
            rec["tags"] = tags
        if date_doc:
            rec["date_doc"] = date_doc
        return rec

    def append(self, rec):
        """Write ONE line immediately — a crash loses nothing already done."""
        self.append_many([rec])

    def append_many(self, recs):
        recs = [r for r in recs if r]
        if not recs:
            return 0
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        for r in recs:
            self.lines += 1
            self._index(r)
        return len(recs)


# ------------------------------------------------------------------- CLI ----
def _cli():
    cfg = load_config()
    mem = Memory(root=cfg["root"] if cfg else None)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        passes, prov, dec, tri = {}, {}, {}, {}
        for r in mem.by_path.values():
            passes[r.get("pass")] = passes.get(r.get("pass"), 0) + 1
            prov[r.get("provenance")] = prov.get(r.get("provenance"), 0) + 1
            dec[r.get("decision")] = dec.get(r.get("decision"), 0) + 1
            tri[r.get("triage")] = tri.get(r.get("triage"), 0) + 1
        print(json.dumps({"file": mem.path, "lines": mem.lines,
                          "distinct_files": len(mem.by_path),
                          "with_desc": sum(1 for r in mem.by_path.values()
                                           if r.get("desc")),
                          "passes": dict(sorted(passes.items(),
                                                key=lambda k: str(k[0]))),
                          "triage": tri, "decisions": dec, "provenance": prov},
                         ensure_ascii=False, indent=1))
    elif cmd == "show":
        if len(sys.argv) < 3:
            sys.exit("usage: memory.py show <path|md5>")
        rec = mem.resolve(sys.argv[2])
        print(json.dumps(rec, ensure_ascii=False, indent=1) if rec else "not found")
    elif cmd == "find":
        needle = norm(" ".join(sys.argv[2:]))
        for p, r in mem.by_path.items():
            if needle in norm(f"{r.get('desc', '')} {p}"):
                print(f"{p}\n    {r.get('desc', '')}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    _cli()
