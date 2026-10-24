"""Cards whose exact result must be recomputed for the current evolutions.

Work items are "card@POS": every position the card can play is computed
(FUT.GG generator ranked by the best role at that position).
A stored result stays exact when no new evolution can be used anywhere in the
card's evolution tree and no evolution of its best path has expired (removing
other evolutions cannot beat the stored best). The tree is explored with the
engine's beam search; every state reached is tested against the new
evolutions.

    python scripts/affected.py <shard> <shards> <queue.json>
Every card without a result for the current evolutions is taken as well.
"""

import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evo_engine as E  # noqa: E402
import evo_scan  # noqa: E402
import gg_pos_model  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
import store  # noqa: E402
POS_CODE = {0: "GK", 3: "RB", 5: "CB", 7: "LB", 10: "CDM", 12: "RM", 14: "CM", 16: "LM", 18: "CAM", 23: "RW", 25: "ST", 27: "LW"}


def items_of(ea, st):
    return [f"{ea}@{POS_CODE[p]}" for p in dict.fromkeys([st["position"], *st["alternativePositionIds"]]) if p in POS_CODE]
STATE = ROOT / "data" / "cache" / "auto_state.json"


def signature(evos):
    return hashlib.sha1(",".join(str(i) for i in sorted(e["id"] for e in evos)).encode()).hexdigest()[:12]


_W = {}


def _init(new_ids, run_ids):
    raw = {e["id"]: e for e in evo_scan.all_evolutions_raw()}
    _W["evos"] = [raw[i] for i in run_ids if i in raw] or evo_scan.active_evolutions()
    _W["new"] = [e for e in _W["evos"] if e["id"] in new_ids]
    _W["gg"] = gg_pos_model.Model2()


def _touched(start):
    hit = []

    def visit(st):
        if not hit and any(E.eligible(st, e)[0] for e in _W["new"]):
            hit.append(1)

    # prudence: the new evolution's other requirements (position, pace, PlayStyles...) met
    # at some overall the card can reach, even on a branch the search did not keep
    for ovr in (start["overall"], 60, 65, 70, 75, 80, 83, 84, 85):
        if not hit and any(E.eligible({**start, "overall": ovr}, e)[0] for e in _W["new"]):
            hit.append(1)
    if not hit:
        evo_scan.search_card(start, _W["evos"], _W["gg"], visit)
    return bool(hit)


def main(shard, shards, out):
    evos = evo_scan.active_evolutions()
    run = ROOT / "data" / "cache" / "run_state.json"
    if run.exists():  # same evolution set as the check job, even if FUT.GG changed it meanwhile
        ids = set(json.loads(run.read_text(encoding="utf-8"))["ids"])
        evos = [e for e in evo_scan.all_evolutions_raw() if e["id"] in ids] or evos
    sig, names = signature(evos), {e["name"] for e in evos}
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    prev_ids = set(state.get("ids") or [])
    new_ids = {e["id"] for e in evos} - prev_ids if prev_ids else {e["id"] for e in evos}
    results = store.load()
    cards = json.loads(evo_scan.CARDS.read_text(encoding="utf-8"))
    ids = sorted(cards, key=int)[shard::shards]
    full = os.environ.get("FT_FULL", "")  # id of the weekly full recompute, if one is running
    queue, check = [], []
    why = {"jamais calculée": 0, "évolution expirée": 0, "nouvelle évolution": 0}
    for ea in ids:
        todo = []
        for it in items_of(ea, cards[ea]["state"]):
            r = results.get(it)
            if full and not (r and r.get("full") == full):
                todo.append(it); why["recalcul hebdomadaire"] = why.get("recalcul hebdomadaire", 0) + 1
                continue
            if r and r.get("sig") == sig and "error" not in r:
                continue  # already exact for these evolutions
            if not r or "error" in r or (not r.get("top") and not r.get("none")):
                todo.append(it); why["jamais calculée"] += 1
            elif r.get("top") and any(s["evo"] not in names for s in r["top"][0]["steps"]):
                todo.append(it); why["évolution expirée"] += 1
            elif new_ids:
                check.append(it)
        queue += todo
    unaffected = []  # stored result still exact: stamped with the new signature at merge
    if check:
        with ProcessPoolExecutor(os.cpu_count() or 2, initializer=_init, initargs=(new_ids, [e["id"] for e in evos])) as pool:
            uniq = list(dict.fromkeys(it.split("@")[0] for it in check))  # the tree does not depend on the position
            touched = dict(zip(uniq, pool.map(_touched, [cards[ea]["state"] for ea in uniq], chunksize=8)))
            for it in check:
                if touched[it.split("@")[0]]:
                    queue.append(it); why["nouvelle évolution"] += 1
                else:
                    unaffected.append(it)
    Path(out).write_text(json.dumps(queue), encoding="utf-8")
    Path(out).with_suffix(".ok.json").write_text(json.dumps(unaffected), encoding="utf-8")
    print(f"part {shard + 1}/{shards} : {len(ids)} cartes, {len(queue)} à recalculer {why} ({len(new_ids)} évolutions nouvelles)")


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
