#!/usr/bin/env python3
"""The only door to state.

Everything that persists between passes goes through this module: the workspace
location, the config, and the ledger itself. No other script opens
`ledger.jsonl` directly.

The ledger is append-only. One line per file per pass; the last line for a given
md5 wins. It does four jobs at once: knowing what has been processed, caching
md5s, finding a document by what the agent understood of it (`grep`), and saying
why a decision was taken.

CLI:
    ledger.py stats                 counts, passes, provenance
    ledger.py find <substring>      grep the descriptions
    ledger.py show <path|md5>       the resolved record
"""
import hashlib
import json
import os
import sys
import unicodedata
from datetime import datetime, timezone

DEFAULT_WORKSPACE = "~/.wikidoc"
MD5_MAX = 1 << 30           # no hashing past 1 GB (videos)
CHUNK = 1 << 20

FIELDS = ("pass", "at", "md5", "size", "path", "level", "decision", "reason",
          "desc", "ids", "tags", "provenance")


# ---------------------------------------------------------------- paths -----
def nfc(s):
    return unicodedata.normalize("NFC", s)


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
    can forget the exclusion line and have the tool eat its own ledger.
    """
    return [config["workspace"], skill_dir()]


# --------------------------------------------------------------- config -----
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
    cfg.setdefault("exclude", [])
    cfg.setdefault("entities", [])
    cfg.setdefault("rules", [])
    cfg.setdefault("sensitive", {})
    cfg.setdefault("tags", {})
    cfg.setdefault("review", {})
    cfg.setdefault("language", "fr")
    return cfg


def require_config(ws=None):
    cfg = load_config(ws)
    if cfg is None:
        sys.exit("no config.yaml in %s — read SETUP.md and run the bootstrap"
                 % (ws or workspace()))
    return cfg


# ------------------------------------------------------------------ md5 -----
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


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def pass_id(led):
    """`YYYY-MM-DD-N` — N counts the passes already recorded today."""
    today = datetime.now().strftime("%Y-%m-%d")
    n = len({r["pass"] for r in led.by_md5.values()
             if str(r.get("pass", "")).startswith(today)}) + 1
    return f"{today}-{n}"


# --------------------------------------------------------------- ledger -----
class Ledger:
    """Append-only JSONL. Last line per md5 is the truth."""

    def __init__(self, ws=None, root=None):
        self.ws = ws or workspace()
        self.path = os.path.join(self.ws, "ledger.jsonl")
        self.root = root
        self.by_md5 = {}
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
                md5 = rec.get("md5")
                if md5:
                    self.by_md5[md5] = rec
                stat = rec.get("stat")
                if stat:
                    # a file whose bytes could not be read (TCC, permissions) still
                    # gets its stat line, so the fast path keeps recognising it
                    self.by_stat[(nfc(rec.get("path", "")), stat[0], stat[1])] = md5 or True

    def rel(self, path):
        """Path recorded relative to root — the ledger survives a root move."""
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
        """Identity is (size, md5) — survives renames and moves."""
        return self.by_md5.get(md5)

    def is_seen(self, path, size, mtime, md5=None):
        return bool(self.seen_stat(path, size, mtime)
                    or (md5 and self.seen_md5(md5)))

    def resolve(self, key):
        rec = self.by_md5.get(key)
        if rec:
            return rec
        key = nfc(key)
        for r in self.by_md5.values():
            if r.get("path") == key or r.get("path", "").endswith(key):
                return r
        return None

    # -- writing -------------------------------------------------------------
    def record(self, path, *, pass_id, level, decision, reason, size=None,
               mtime=None, md5=None, desc=None, ids=None, tags=None,
               provenance="pipeline", at=None):
        if size is None or mtime is None:
            try:
                st = os.stat(path)
                size = st.st_size if size is None else size
                mtime = int(st.st_mtime) if mtime is None else mtime
            except OSError:
                size, mtime = size or 0, mtime or 0
        if md5 is None:
            md5 = file_md5(path, size)
        rec = {"pass": pass_id, "at": at or now(), "md5": md5, "size": size,
               "path": self.rel(path), "level": level, "decision": decision,
               "reason": reason, "provenance": provenance,
               "stat": [size, int(mtime)]}
        if desc:
            rec["desc"] = desc
        if ids:
            rec["ids"] = ids
        if tags:
            rec["tags"] = tags
        return rec

    def append(self, rec):
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
            if r.get("md5"):
                self.by_md5[r["md5"]] = r
            if r.get("stat"):
                self.by_stat[(r.get("path", ""), r["stat"][0], r["stat"][1])] = \
                    r.get("md5") or True
        return len(recs)


# ------------------------------------------------------------------ CLI -----
def _cli():
    cfg = load_config()
    led = Ledger(root=cfg["root"] if cfg else None)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        passes, prov, dec = {}, {}, {}
        for r in led.by_md5.values():
            passes[r.get("pass")] = passes.get(r.get("pass"), 0) + 1
            prov[r.get("provenance")] = prov.get(r.get("provenance"), 0) + 1
            dec[r.get("decision")] = dec.get(r.get("decision"), 0) + 1
        print(json.dumps({"file": led.path, "lines": led.lines,
                          "distinct_files": len(led.by_md5),
                          "with_desc": sum(1 for r in led.by_md5.values() if r.get("desc")),
                          "passes": dict(sorted(passes.items(), key=lambda k: str(k[0]))),
                          "provenance": prov, "decisions": dec},
                         ensure_ascii=False, indent=1))
    elif cmd == "find":
        needle = nfc(" ".join(sys.argv[2:])).casefold()
        for r in led.by_md5.values():
            hay = nfc(f"{r.get('desc', '')} {r.get('path', '')}").casefold()
            if needle in hay:
                print(f"{r.get('path')}\n    {r.get('desc', '')}")
    elif cmd == "show":
        rec = led.resolve(sys.argv[2])
        print(json.dumps(rec, ensure_ascii=False, indent=1) if rec else "not found")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    _cli()
