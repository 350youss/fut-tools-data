"""Exports the exact results for the FT panel.

data/evo_best.json : top cards (best position) and hidden gems, shown first.
data/evo_all.json  : every card and every position computed, compact, for
                     the panel's filters (nation, league, club, position).

A result is kept only while all the evolutions of its path are still active.
Results "id@POS" are ranked at that position; plain "id" ones (older runs)
at the card's best position.

    py scripts/export_evo_best.py [count]
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evo_engine as E  # noqa: E402
import evo_scan  # noqa: E402
import gg_pos_model as M  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "evo_best.json"
OUT_ALL = ROOT / "data" / "evo_all.json"
POS_NAME = {v: k for k, v in M.POS.items()}
FACES = ("facePace", "faceShooting", "facePassing", "faceDribbling", "faceDefending", "facePhysicality")


def club_rows():
    """Paths found by the generator in the browser (first runs), same shape as the headless ones."""
    club = json.loads(M.GEN.read_text(encoding="utf-8"))
    for ea, paths in club["rows"]:
        top = []
        for gg, pos, pts, cr, exp, steps in paths:
            st = []
            for tok in steps.split():
                name_i, gg_i = tok.split("=")
                idx = int("".join(ch for ch in name_i if ch.isdigit()))
                st.append({"evo": club["D"][idx], "letters": "".join(ch for ch in name_i if ch.isalpha()), "gg": float(gg_i)})
            top.append({"gg": gg, "ggPos": pos, "points": pts, "coins": cr, "expires": exp, "steps": st})
        yield str(ea), {"top": top}


def main(count=400):
    cards = json.loads(M.CARDS.read_text(encoding="utf-8"))
    meta = json.loads((ROOT / "data" / "cache" / "card_meta.json").read_text(encoding="utf-8")) if (ROOT / "data" / "cache" / "card_meta.json").exists() else {}
    evos = M.all_evolutions()
    m = evo_scan.get("https://r2.fut.gg/27/manifest.json")
    live = evo_scan.get(f"https://r2.fut.gg/27/active-evolutions.v{m['_version']}.{m['active-evolutions']}.json")
    active = {e["name"] for e in live if not e.get("isExpired")}
    g = evo_scan.get(f"https://r2.fut.gg/27/player-gg-ratings.v{m['_version']}.{m['player-gg-ratings']}.json") or {}
    base_gg, cur = {}, g.get("id0", 0)
    for i, sc in enumerate(g.get("sc", [])):
        if i:
            cur += g["d"][i - 1]
        if sc:
            base_gg[cur] = sc / 100

    results = json.loads((ROOT / "data" / "cache" / "futgg_gen.json").read_text(encoding="utf-8"))
    entries = list(results.items()) + [(k, v) for k, v in club_rows() if k not in results]
    names, name_idx = [], {}

    def evo_i(n):
        if n not in name_idx:
            name_idx[n] = len(names)
            names.append(n)
        return name_idx[n]

    per_card = {}  # id -> {pos: result}
    for key, r in entries:
        ea, _, pos_key = key.partition("@")
        if ea not in cards or not r.get("top"):
            continue
        top = [t for t in r["top"] if all(s["evo"] in active for s in t["steps"])]
        if pos_key:
            top = [t for t in top if t["ggPos"] == pos_key]
        if not top:
            continue
        t = top[0]
        pos = t["ggPos"]
        old = per_card.setdefault(ea, {}).get(pos)
        if old and (old["gg"] > t["gg"] or (old.get("exact_pos") and not pos_key)):
            continue
        st = cards[ea]["state"]
        for s in t["steps"]:
            ev = evos.get(s["evo"])
            if ev:
                st = E.apply_evolution(st, ev, M.letters_to_choices(ev, s["letters"]))
        per_card[ea][pos] = {
            "gg": t["gg"], "ovr": st["overall"], "exact_pos": bool(pos_key),
            "faces": [E.face(st, f) for f in FACES] if cards[ea]["state"]["position"] != 0 else None,
            "coins": t["coins"], "points": t["points"], "exp": t["expires"],
            "steps": [[s["evo"], s["letters"], s["gg"], 1 if evos.get(s["evo"], {}).get("repeatabilityCount") == 1 else 0] for s in t["steps"]],
        }

    def card_row(ea, pos):
        c, p = cards[ea], per_card[ea][pos]
        n, lg, cl = (meta.get(ea) or [None, None, None])
        return {
            "id": int(ea), "name": c["name"], "base": c["state"]["overall"], "pos0": POS_NAME.get(c["state"]["position"], "?"),
            "url": c["url"], "sbc": c["sbc"], "obj": c["objective"], "nation": n, "league": lg, "club": cl,
            "gg": p["gg"], "pos": pos, "ovr": p["ovr"], "gg0": base_gg.get(int(ea)), "faces": p["faces"],
            "coins": p["coins"], "points": p["points"], "exp": p["exp"],
            "steps": [{"n": s[0], "o": s[1], "gg": s[2], "u": s[3]} for s in p["steps"]],
            "alt": [{"gg": q["gg"], "pos": ps, "steps": [s[0] + (" " + s[1] if s[1] else "") for s in q["steps"]]}
                    for ps, q in sorted(per_card[ea].items(), key=lambda x: -x[1]["gg"]) if ps != pos][:3],
        }

    best = sorted(((max(per_card[ea].values(), key=lambda q: q["gg"])["gg"], ea) for ea in per_card), reverse=True)
    rows = [card_row(ea, max(per_card[ea], key=lambda ps: per_card[ea][ps]["gg"])) for _, ea in best]
    gems = sorted((r for r in rows if r["base"] <= 74 and r["gg0"]), key=lambda r: -(r["gg"] - r["gg0"]))[:60]
    now = datetime.now().isoformat(timespec="minutes")
    OUT.write_text(json.dumps({"at": now, "cards": len(per_card), "rows": rows[:count], "gems": gems}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # compact list for the filters: one line per card, one entry per position
    nat, lea, clu = {}, {}, {}

    def idx(d, v):
        return d.setdefault(v, len(d)) if v else -1

    lines = []
    for ea, byp in per_card.items():
        c = cards[ea]
        n, lg, cl = (meta.get(ea) or [None, None, None])
        lines.append([int(ea), c["name"], c["state"]["overall"], idx(nat, n), idx(lea, lg), idx(clu, cl), base_gg.get(int(ea)),
                      [[ps, q["gg"], q["ovr"], q["faces"], q["coins"], q["points"], q["exp"], [[evo_i(s[0]), s[1], s[2], s[3]] for s in q["steps"]]]
                       for ps, q in sorted(byp.items(), key=lambda x: -x[1]["gg"])], c["url"]])
    OUT_ALL.write_text(json.dumps({"at": now, "evos": names, "nations": list(nat), "leagues": list(lea), "clubs": list(clu), "cards": lines},
                                  ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(per_card)} cartes, {sum(len(v) for v in per_card.values())} couples carte-poste ; meilleure : {rows[0]['name']} {rows[0]['gg']} {rows[0]['pos']}"
          f" ; evo_all.json {OUT_ALL.stat().st_size // 1024} Ko")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 400)
