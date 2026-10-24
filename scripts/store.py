"""Exact results storage: data/cache/futgg_gen.json.gz, gzipped and compact
(the plain file would pass GitHub's 100 MB limit once every card is computed).

Keys: card id (best position, older runs) or "id@POS". Values: sig, full, none,
error, and top = the 2 best paths [{gg, ggPos, coins, points, expires, startGg,
steps: [{evo, letters, gg}]}].
"""

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "data" / "cache" / "futgg_gen.json.gz"
TOP_KEYS = ("gg", "ggPos", "coins", "points", "expires", "startGg", "name")


def compact(r):
    out = {k: r[k] for k in ("sig", "full", "none", "error", "pos") if r.get(k) not in (None, "")}
    if r.get("top"):
        out["top"] = [{**{k: t.get(k) for k in TOP_KEYS if t.get(k) is not None},
                       "steps": [{"evo": s["evo"], "letters": s.get("letters", ""), "gg": s.get("gg")} for s in t.get("steps", [])]}
                      for t in r["top"][:2]]
    return out


def load(path=RESULTS):
    path = Path(path)
    if path.suffix == ".gz":
        if path.exists():
            return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        legacy = path.with_suffix("")  # older plain file
        return json.loads(legacy.read_text(encoding="utf-8")) if legacy.exists() else {}
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(results, path=RESULTS):
    path = Path(path)
    data = json.dumps({k: compact(v) for k, v in results.items()}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(data, 6))
    else:
        path.write_bytes(data)
