"""Full evolution scan: every candidate card, every chain of active evolutions,
every choice. Engine validated against FUT.GG (scripts/evo_engine.py).

    py scripts/evo_scan.py cards     # candidates + detailed attributes (all-versions)
    py scripts/evo_scan.py search    # all chains per card -> data/evo_scan.json
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evo_engine as E  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "data" / "cache" / "scan_cards.json"
OUT = ROOT / "data" / "evo_scan.json"
GG_MODEL = ROOT / "data" / "gg_model.json"
ROLE_POS = ROOT / "data" / "role_positions.json"
API = "https://www.fut.gg/api/fut"
MAX_START = 83          # every rating-raising evolution needs <= 83
BEAM = 80               # states kept per depth for one card


def get(url):
    for _ in range(3):
        try:
            r = requests.get(url, headers=E.H, timeout=30)
            if r.ok:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(1)
    return {}


def active_evolutions():
    m = get("https://r2.fut.gg/27/manifest.json")
    evos = get(f"https://r2.fut.gg/27/active-evolutions.v{m['_version']}.{m['active-evolutions']}.json")
    keep = [e for e in evos if not e.get("isExpired") and not e.get("isCosmeticEvolution") and not e.get("doesNotUpgradePlayer") and e.get("levels")]
    return keep


def all_evolutions_raw():
    """Every evolution FUT.GG knows (expired ones included), same filter as active_evolutions."""
    m = get("https://r2.fut.gg/27/manifest.json")
    allv = get(f"https://r2.fut.gg/27/all-evolutions.v{m['_version']}.{m['all-evolutions']}.json") or []
    return [e for e in allv if not e.get("isCosmeticEvolution") and not e.get("doesNotUpgradePlayer") and e.get("levels")]


def cards():
    # 1. base players with a card rated <= 83 (collapsed: one row per base player).
    # The listing stops at 10 000 rows, so it is read one rating at a time.
    bases = {}
    for ovr in range(40, MAX_START + 1):
        page = 1
        while True:
            d = get(f"{API}/players/v2/27/?overall__gte={ovr}&overall__lte={ovr}&collapse_by_base_player=true&page={page}")
            for x in d.get("data", []):
                bases[x["basePlayerEaId"]] = 1
            if not d.get("next"):
                break
            page += 1
            time.sleep(0.1)
        if page >= 334:
            print(f"ATTENTION : note {ovr} tronquée à 10 000 lignes")
        print(f"note {ovr} : {page} pages, {len(bases)} joueurs")
    print(f"{len(bases)} joueurs de base")
    todo = list(bases)

    # 2. every version of each, with its 34 detailed attributes
    def one(bid):
        d = get(f"{API}/players/v2/all-versions/{bid}/")
        return d.get("data", []) if isinstance(d, dict) else d

    out = {}
    with ThreadPoolExecutor(6) as pool:
        for k, items in enumerate(pool.map(one, todo)):
            for it in items or []:
                # all-versions also lists older games, whose base cards reuse the same eaId
                if "/27-" in (it.get("url") or "") and (it.get("overall") or 99) <= MAX_START and sum(1 for a in it if a.startswith("attribute")) >= 30:
                    out[it["eaId"]] = {
                        "state": E.state_of(it),
                        "name": it.get("commonName") or " ".join(x for x in (it.get("firstName"), it.get("lastName")) if x),
                        "base": it.get("basePlayerEaId"),
                        "url": it.get("url"),
                        "sbc": bool(it.get("isSbcItem") or it.get("isSbc")),
                        "objective": bool(it.get("isObjectiveItem") or it.get("isObjective")),
                    }
            if k % 500 == 0:
                print(f"{k}/{len(todo)} joueurs détaillés, {len(out)} cartes")
    CARDS.parent.mkdir(parents=True, exist_ok=True)
    CARDS.write_text(json.dumps(out), encoding="utf-8")
    print(f"{len(out)} cartes candidates enregistrées")


META = ROOT / "data" / "cache" / "card_meta.json"


def meta():
    """Nation, league and club of every FC 27 card rated <= 83 (for the panel's filters)."""
    out = {}
    for ovr in range(40, MAX_START + 1):
        page = 1
        while True:
            d = get(f"{API}/players/v2/27/?overall__gte={ovr}&overall__lte={ovr}&page={page}")
            for x in d.get("data", []):
                out[x["eaId"]] = [(x.get("nation") or {}).get("name"), (x.get("league") or {}).get("name"), (x.get("club") or {}).get("name")]
            if not d.get("next"):
                break
            page += 1
            time.sleep(0.1)
    META.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"{len(out)} cartes avec nationalité, championnat et club")


# ---------- GG estimate (absolute model, for ranking inside the search) ----------

class GG:
    def __init__(self):
        m = json.loads(GG_MODEL.read_text(encoding="utf-8"))
        self.attrs, self.ps, self.w = m["attrs"], m["playstyles"], {r: np.array(v) for r, v in m["roles"].items()}
        self.maps = m.get("maps", {})
        self.role_pos = json.loads(ROLE_POS.read_text(encoding="utf-8"))

    def feats(self, st, role):
        r = int(role)
        a = [st.get(n, 0) for n in self.attrs]
        ps = [1.0 if i in st["playstyles"] else 0.0 for i in self.ps]
        psp = [1.0 if i in st["playstylesPlus"] else 0.0 for i in self.ps]
        extra = [st.get("skillMoves") or 0, st.get("weakFoot") or 0, 0.0, 1.0 if r in st["rolesPlus"] or (r + 100) in st["rolesPlus"] else 0.0,
                 1.0 if (r + 100) in st["rolesPlusPlus"] or r in st["rolesPlusPlus"] else 0.0, 1.0]
        return np.array(a + ps + psp + extra)

    def __call__(self, st):
        poss = {st["position"], *st["alternativePositionIds"]}
        best, best_role = -1e9, None
        for role, w in self.w.items():
            if self.role_pos.get(role) not in poss:
                continue
            s = float(self.feats(st, role) @ w)
            mp = self.maps.get(role)
            if mp:
                # beyond the learnt range, extend linearly instead of following the cubic
                lo, hi = mp["lo"], mp["hi"]
                cl = min(max(s, lo), hi)
                v = float(np.polyval(mp["c"], cl)) + (s - cl) * float(np.polyval(np.polyder(mp["c"]), cl))
            else:
                v = s
            if v > best:
                best, best_role = v, role
        return best, best_role


def choice_sets(evo):
    per_level = [[o["idx"] for o in lv["upgradeOptions"]] if lv.get("upgradeOptions") else [1] for lv in evo["levels"]]
    return [list(c) for c in product(*per_level)]


def key(st):
    return json.dumps({k: st[k] for k in sorted(st) if k.startswith("attribute") or k in ("overall", "rarityEaId", "skillMoves", "weakFoot")}, sort_keys=True) + \
        str(sorted(st["playstyles"])) + str(sorted(st["playstylesPlus"])) + str(sorted(st["rolesPlus"])) + str(sorted(st["rolesPlusPlus"]))


def search_card(start, evos, gg, visit=None):
    """Beam search over chains of evolutions (each evolution once per player).
    visit(state) is called on the start and on every state reached."""
    if visit:
        visit(start)
    frontier = {key(start): (start, [])}
    best = []
    seen = set(frontier)
    for depth in range(14):
        nxt = {}
        for st, path in frontier.values():
            used = {p[0] for p in path}
            for e in evos:
                if e["id"] in used:
                    continue
                prior = e.get("allowedPriorEvolutionIds") or []
                if prior and not set(prior) & used:
                    continue
                ok, _ = E.eligible(st, e)
                if not ok:
                    continue
                for ch in choice_sets(e):
                    ns = E.apply_evolution(st, e, ch)
                    k = key(ns)
                    if k in seen:
                        continue
                    seen.add(k)
                    if visit:
                        visit(ns)
                    nxt[k] = (ns, path + [(e["id"], ch)])
        if not nxt:
            break
        scored = sorted(((gg(s)[0], k) for k, (s, _) in nxt.items()), reverse=True)
        keep = [k for _, k in scored[:BEAM]]
        frontier = {k: nxt[k] for k in keep}
        best.extend((v, nxt[k]) for v, k in scored[:10])
    best.sort(key=lambda x: -x[0])
    return best[:3]


_W = {}


def _init():
    _W["evos"] = active_evolutions()
    import gg_pos_model
    _W["gg"] = gg_pos_model.Model2()  # GG per position with chemistry styles, see gg_pos_model.py
    _W["names"] = {e["id"]: e for e in _W["evos"]}


def _one(item):
    eid, c = item
    names, out = _W["names"], []
    for v, (st, path) in search_card(c["state"], _W["evos"], _W["gg"]):
        out.append({
            "eaId": int(eid), "name": c["name"], "url": c["url"], "base": c["base"], "sbc": c["sbc"], "objective": c["objective"],
            "start": c["state"]["overall"], "position": c["state"]["position"], "overall": st["overall"], "gg_est": round(v, 2),
            "faces": {f: E.face(st, f) for f in E.FACE_WEIGHTS},
            "playstyles": st["playstyles"], "playstylesPlus": st["playstylesPlus"],
            "path": [{"id": i, "name": names[i]["name"], "choices": ch, "reward": names[i]["isRewardEvolution"],
                      "unique": names[i]["repeatabilityCount"] == 1} for i, ch in path],
        })
    return out


def search():
    from concurrent.futures import ProcessPoolExecutor
    # all-versions also returns the cards of older games: keep FC 27 only
    cards_ = {k: v for k, v in json.loads(CARDS.read_text(encoding="utf-8")).items() if "/27-" in (v["url"] or "")}
    print(f"{len(cards_)} cartes FC 27")
    results, t0 = [], time.time()
    with ProcessPoolExecutor(os.cpu_count() or 2, initializer=_init) as pool:
        for k, out in enumerate(pool.map(_one, cards_.items(), chunksize=8)):
            results.extend(out)
            if k % 500 == 0:
                print(f"{k}/{len(cards_)} cartes, {time.time() - t0:.0f} s")
    results.sort(key=lambda r: -r["gg_est"])
    OUT.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print(f"{len(results)} parcours, meilleurs :")
    for r in results[:15]:
        print(f"  {r['name']:28} {r['start']} -> {r['overall']}  GG≈{r['gg_est']:.1f}  {len(r['path'])} évos")


if __name__ == "__main__":
    {"cards": cards, "search": search, "meta": meta}[sys.argv[1]]()
