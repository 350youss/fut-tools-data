"""Steps of the GitHub Actions workflow (.github/workflows/evolutions.yml).

    python scripts/evo_cloud.py check [--force]   # writes run=true|false and sig=... to $GITHUB_OUTPUT
    python scripts/evo_cloud.py merge <dir>       # merges the shards' results, exports, saves the state

The shards in between run affected.py then gen_headless.py on their part of
the cards (one GitHub machine each).
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import affected  # noqa: E402
import evo_scan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "cache" / "auto_state.json"
RESULTS = ROOT / "data" / "cache" / "futgg_gen.json"
LOG = ROOT / "data" / "cache" / "evo_auto.log"


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def output(**kv):
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


def check(force):
    evos = evo_scan.active_evolutions()
    if not evos:
        log("FUT.GG injoignable, rien fait")
        return output(run="false")
    sig = affected.signature(evos)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    stale_cards = time.time() - state.get("cards_at", 0) > 7 * 86400
    if sig == state.get("sig") and not force:
        log(f"évolutions inchangées ({len(evos)} actives), rien à faire")
        return output(run="false")
    log(f"évolutions à calculer ({len(evos)} actives, {sig})")
    output(run="true", sig=sig, cards="true" if stale_cards else "false")


def merge(folder):
    evos = evo_scan.active_evolutions()
    sig = affected.signature(evos)
    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    n = 0
    for f in sorted(Path(folder).rglob("futgg_gen.json")):
        for ea, r in json.loads(f.read_text(encoding="utf-8")).items():
            if r.get("sig") == sig and (ea not in results or results[ea].get("sig") != sig or "error" in results[ea]):
                results[ea] = r
                n += 1
    for f in sorted(Path(folder).rglob("*.ok.json")):
        for ea in json.loads(f.read_text(encoding="utf-8")):
            if ea in results and "error" not in results[ea]:
                results[ea]["sig"] = sig
    RESULTS.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    cards = ROOT / "data" / "cache" / "scan_cards.json"
    all_items = [it for ea, c in json.loads(cards.read_text(encoding="utf-8")).items() for it in affected.items_of(ea, c["state"])]
    missing = [it for it in all_items if results.get(it, {}).get("sig") != sig or "error" in results.get(it, {})]
    log(f"{n} résultats exacts ajoutés, {len(all_items) - len(missing)} / {len(all_items)} couples carte-poste exacts pour les évolutions actuelles")
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if missing:  # some shard did not finish: the next run takes the rest
        log(f"{len(missing)} cartes restent à calculer, repris au prochain passage")
        state.pop("sig", None)
        STATE.write_text(json.dumps(state), encoding="utf-8")
        return
    if os.environ.get("FT_CARDS_REFRESHED") == "true":
        state["cards_at"] = time.time()
    state.update(sig=sig, ids=sorted(e["id"] for e in evos), at=time.time(), active=len(evos),
                 cards=len(json.loads(cards.read_text(encoding="utf-8"))))
    STATE.write_text(json.dumps(state), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "check":
        check("--force" in sys.argv)
    else:
        merge(sys.argv[2])
