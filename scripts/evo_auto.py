"""Automatic refresh of the exact evolution ranking (GitHub Actions, see
.github/workflows/evolutions.yml; the workflow commits the data afterwards).

1. Reads FUT.GG's active evolutions; stops if the set has not changed
   (unless --force).
2. Refreshes the card list when it is more than 7 days old.
3. Retrains the GG model on every exact result collected so far.
4. Ranks every card with the engine + model (evo_scan.py search).
5. Runs FUT.GG's generator (gen_headless.py) on the best estimates and on
   the cards of the previous ranking, for the new set of evolutions.
6. Exports the ranking (data/evo_best.json).

    py scripts/evo_auto.py [--force]
Log: data/cache/evo_auto.log
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evo_scan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "cache" / "auto_state.json"
LOG = ROOT / "data" / "cache" / "evo_auto.log"
QUEUE = ROOT / "data" / "cache" / "gen_queue_auto.json"
NEW_TOP = 250      # best estimates sent to the generator
KEEP_TOP = 150     # previous best cards checked again with the new evolutions
WORKERS = int(os.environ.get("FT_WORKERS", "12"))


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def step(*args, env=None):
    log("lance " + " ".join(args))
    r = subprocess.run([sys.executable, "-u", *args], cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
                       capture_output=True, text=True, encoding="utf-8")
    tail = (r.stdout or "").strip().splitlines()[-3:]
    for t in tail:
        log("  " + t)
    if r.returncode:
        log("  ERREUR " + (r.stderr or "").strip()[-500:])
        raise SystemExit(1)


def main(force=False):
    evos = evo_scan.active_evolutions()
    if not evos:
        log("FUT.GG injoignable, rien fait")
        return
    sig = hashlib.sha1(",".join(str(i) for i in sorted(e["id"] for e in evos)).encode()).hexdigest()[:12]
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if sig == state.get("sig") and not force:
        log(f"évolutions inchangées ({len(evos)} actives), rien à faire")
        return
    log(f"nouvel ensemble d'évolutions ({len(evos)} actives, {sig}), mise à jour")
    t0 = time.time()

    if time.time() - state.get("cards_at", 0) > 7 * 86400:
        step("scripts/evo_scan.py", "cards")
        state["cards_at"] = time.time()
    step("scripts/gg_pos_model.py", "fit2")
    step("scripts/evo_scan.py", "search")

    best = {}
    for r in json.loads((ROOT / "data" / "evo_scan.json").read_text(encoding="utf-8")):
        if r["eaId"] not in best or r["gg_est"] > best[r["eaId"]]:
            best[r["eaId"]] = r["gg_est"]
    queue = sorted(best, key=lambda k: -best[k])[:NEW_TOP]
    prev = ROOT / "data" / "evo_best.json"
    if prev.exists():
        queue += [r["id"] for r in json.loads(prev.read_text(encoding="utf-8"))["rows"][:KEEP_TOP] if r["id"] not in queue]
    QUEUE.write_text(json.dumps(queue), encoding="utf-8")
    step("scripts/gen_headless.py", str(QUEUE), str(WORKERS), env={"FT_SIG": sig})
    step("scripts/export_evo_best.py", "400")  # data/evo_best.json, read by the FT panel
    state.update(sig=sig, at=time.time(), active=len(evos))
    STATE.write_text(json.dumps(state), encoding="utf-8")
    log(f"terminé en {(time.time() - t0) / 60:.0f} min")


if __name__ == "__main__":
    main("--force" in sys.argv)
