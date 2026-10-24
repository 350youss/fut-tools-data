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
import store  # noqa: E402
RUN = ROOT / "data" / "cache" / "run_state.json"  # evolutions seen by the check job, used by the whole run
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
    # weekly full recompute (FUT.GG re-tunes its GG Rating now and then): started on
    # Sunday night, carried on by the next runs until every pair is done
    full = state.get("full", "")
    if not full and time.time() - state.get("full_at", time.time()) > 7 * 86400 and datetime.now().weekday() == 6:
        full = datetime.now().strftime("%Y%m%d")
        state["full"] = full
        STATE.write_text(json.dumps(state), encoding="utf-8")
    if sig == state.get("sig") and not force and not full:
        log(f"évolutions inchangées ({len(evos)} actives), rien à faire")
        return output(run="false")
    log(f"évolutions à calculer ({len(evos)} actives, {sig}){' + recalcul hebdomadaire ' + full if full else ''}")
    RUN.write_text(json.dumps({"sig": sig, "ids": sorted(e["id"] for e in evos), "full": full}), encoding="utf-8")
    output(run="true", sig=sig, full=full, cards="true" if stale_cards else "false")


def merge(folder):
    # the run's own evolution set: FUT.GG may have changed it since (next run handles that)
    run = json.loads(RUN.read_text(encoding="utf-8"))
    sig, ids = run["sig"], run["ids"]
    results = store.load()
    n = 0
    for f in sorted(Path(folder).rglob("futgg_gen.json*")):
        for ea, r in store.load(f).items():
            if r.get("sig") == sig and (ea not in results or results[ea].get("sig") != sig or "error" in results[ea]):
                results[ea] = r
                n += 1
    for f in sorted(Path(folder).rglob("*.ok.json")):
        for ea in json.loads(f.read_text(encoding="utf-8")):
            if ea in results and "error" not in results[ea]:
                results[ea]["sig"] = sig
    store.save(results)
    cards = ROOT / "data" / "cache" / "scan_cards.json"
    all_items = [it for ea, c in json.loads(cards.read_text(encoding="utf-8")).items() for it in affected.items_of(ea, c["state"])]
    full = run.get("full", "")
    missing = [it for it in all_items if results.get(it, {}).get("sig") != sig or "error" in results.get(it, {})
               or (full and results.get(it, {}).get("full") != full)]
    log(f"{n} résultats exacts ajoutés, {len(all_items) - len(missing)} / {len(all_items)} couples carte-poste exacts pour les évolutions actuelles")
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if missing:  # some shard did not finish: the next run takes the rest
        log(f"{len(missing)} cartes restent à calculer, repris au prochain passage")
        state.pop("sig", None)
        STATE.write_text(json.dumps(state), encoding="utf-8")
        return
    if full:  # weekly recompute finished
        state.pop("full", None)
        state["full_at"] = time.time()
    if os.environ.get("FT_CARDS_REFRESHED") == "true":
        state["cards_at"] = time.time()
    state.update(sig=sig, ids=ids, at=time.time(), active=len(ids),
                 cards=len(json.loads(cards.read_text(encoding="utf-8"))))
    STATE.write_text(json.dumps(state), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "check":
        check("--force" in sys.argv)
    else:
        merge(sys.argv[2])
