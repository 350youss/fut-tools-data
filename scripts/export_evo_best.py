"""Builds data/evo_best.json for the FT panel from FUT.GG's generator results
(data/cache/futgg_gen.json and futgg_gen_club.json): best path of each card,
ranked by exact GG Rating, with every step (evolution, options, GG after it).

    py scripts/export_evo_best.py [count]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evo_engine as E  # noqa: E402
import gg_pos_model as M  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "evo_best.json"
POS_NAME = {v: k for k, v in M.POS.items()}


def main(count=400):
    cards = json.loads(M.CARDS.read_text(encoding="utf-8"))
    evos = M.all_evolutions()
    rows = []
    gen = json.loads((ROOT / "data" / "cache" / "futgg_gen.json").read_text(encoding="utf-8"))
    for ea, r in gen.items():
        if r.get("top") and ea in cards:
            rows.append((ea, r["top"]))
    club = json.loads(M.GEN.read_text(encoding="utf-8"))
    seen = {ea for ea, _ in rows}
    for ea, paths in club["rows"]:
        if str(ea) in seen or str(ea) not in cards:
            continue
        top = []
        for gg, pos, pts, cr, exp, steps in paths:
            st = []
            for tok in steps.split():
                name_i, gg_i = tok.split("=")
                idx = int("".join(ch for ch in name_i if ch.isdigit()))
                st.append({"evo": club["D"][idx], "letters": "".join(ch for ch in name_i if ch.isalpha()), "gg": float(gg_i)})
            top.append({"gg": gg, "ggPos": pos, "points": pts, "coins": cr, "expires": exp, "steps": st})
        rows.append((str(ea), top))

    # a path is only shown while all its evolutions are still active
    import evo_scan
    m = evo_scan.get("https://r2.fut.gg/27/manifest.json")
    live = evo_scan.get(f"https://r2.fut.gg/27/active-evolutions.v{m['_version']}.{m['active-evolutions']}.json")
    active = {e["name"] for e in live if not e.get("isExpired")}
    out = []
    for ea, top in rows:
        c = cards[ea]
        top = [t for t in top if all(s["evo"] in active for s in t["steps"])]
        if not top:
            continue
        best = top[0]
        st = c["state"]
        steps = []
        for s in best["steps"]:
            evo = evos.get(s["evo"])
            if evo:
                st = E.apply_evolution(st, evo, M.letters_to_choices(evo, s["letters"]))
            steps.append({
                "n": s["evo"], "o": s["letters"], "gg": s["gg"],
                "u": 1 if evo and evo.get("repeatabilityCount") == 1 else 0,
                "r": 1 if evo and evo.get("isRewardEvolution") else 0,
            })
        out.append({
            "id": int(ea), "name": c["name"], "base": c["state"]["overall"], "pos0": POS_NAME.get(c["state"]["position"], "?"),
            "url": c["url"], "sbc": c["sbc"], "obj": c["objective"],
            "gg": best["gg"], "pos": best["ggPos"], "ovr": st["overall"],
            "faces": [E.face(st, f) for f in ("facePace", "faceShooting", "facePassing", "faceDribbling", "faceDefending", "facePhysicality")]
            if c["state"]["position"] != 0 else None,
            "coins": best["coins"], "points": best["points"], "exp": best["expires"], "steps": steps,
            "alt": [{"gg": t["gg"], "pos": t["ggPos"], "steps": [s["evo"] + (" " + s["letters"] if s["letters"] else "") for s in t["steps"]]}
                    for t in top[1:3]],
        })
    out.sort(key=lambda r: -r["gg"])
    data = {"at": json.loads(json.dumps(__import__("datetime").datetime.now().isoformat(timespec="minutes"))),
            "cards": len(out), "rows": out[:count]}
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out)} cartes calculées, {min(count, len(out))} exportées, meilleure : {out[0]['name']} {out[0]['gg']} {out[0]['pos']}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 400)
