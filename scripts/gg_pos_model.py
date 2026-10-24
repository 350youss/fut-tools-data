"""GG Rating per position, learnt on exact FUT.GG values.

Data:
- data/cache/gg_train.json: card states from FUT.GG's published evolution
  paths, with the GG of every role (target per position = its best role);
- data/cache/futgg_gen_club.json: paths found by FUT.GG's generator, with the
  exact GG after every step. Each path is replayed with the exact engine
  (evo_engine.py) to rebuild the card state; that covers the high ratings
  the published paths rarely reach.

One ridge regression per position. Checked on cards left out of training.

    py scripts/gg_pos_model.py fit      # trains data/gg_pos_model.json
"""

import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evo_engine as E  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TRAIN = ROOT / "data" / "cache" / "gg_train.json"
GEN = ROOT / "data" / "cache" / "futgg_gen_club.json"
CARDS = ROOT / "data" / "cache" / "scan_cards.json"
ROLE_POS = ROOT / "data" / "role_positions.json"
MODEL = ROOT / "data" / "gg_pos_model.json"
POS = {"GK": 0, "RB": 3, "CB": 5, "LB": 7, "CDM": 10, "RM": 12, "CM": 14, "LM": 16, "CAM": 18, "RW": 23, "ST": 25, "LW": 27}
# mirrored positions share one formula (more examples for each)
GROUP = {12: 16, 23: 27, 3: 7}
ATTRS = sorted(a for g in E.FACE_GROUPS.values() for a in g) + ["attributeGkDiving", "attributeGkHandling", "attributeGkKicking", "attributeGkReflexes", "attributeGkPositioning"]


def all_evolutions():
    """Every evolution FUT.GG knows, expired ones included (to replay old paths)."""
    import evo_scan
    cache = ROOT / "data" / "cache" / "all_evolutions.json"
    m = evo_scan.get("https://r2.fut.gg/27/manifest.json")
    allv = evo_scan.get(f"https://r2.fut.gg/27/all-evolutions.v{m['_version']}.{m['all-evolutions']}.json") if m else None
    if allv:
        cache.write_text(json.dumps(allv), encoding="utf-8")
    else:
        allv = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else []
    evos = {e["name"]: e for e in allv}
    evos.update({e["name"]: e for e in evo_scan.active_evolutions()})
    return evos


def letters_to_choices(evo, letters):
    seq = [ord(c) - 64 for c in letters if c.isalpha()]
    return [seq.pop(0) if lv.get("upgradeOptions") and seq else 1 for lv in evo["levels"] or []]


def replay():
    """(state, position id, exact GG) for every step of the generator paths."""
    gen = json.loads(GEN.read_text(encoding="utf-8"))
    cards = json.loads(CARDS.read_text(encoding="utf-8"))
    evos = all_evolutions()
    out, missing = [], set()
    for ea, paths in gen["rows"]:
        c = cards.get(str(ea))
        if not c:
            continue
        for gg, pos, _pts, _cr, _exp, steps in paths:
            st = c["state"]
            for tok in steps.split():
                name_i, gg_i = tok.split("=")
                idx = int("".join(ch for ch in name_i if ch.isdigit()))
                letters = "".join(ch for ch in name_i if ch.isalpha())
                evo = evos.get(gen["D"][idx])
                if not evo:
                    missing.add(gen["D"][idx])
                    break
                st = E.apply_evolution(st, evo, letters_to_choices(evo, letters))
                out.append((st, None, float(gg_i), ea))
            else:
                out[-1] = (out[-1][0], POS[pos], out[-1][2], ea)
    # headless runs (gen_headless.py): GG after every step; "id@POS" results are ranked at POS
    import store
    done = {ea for ea, _ in gen["rows"]}
    for key, r in store.load().items():
        ea, _, pos_key = key.partition("@")
        c = cards.get(ea)
        if not c or int(ea) in done or not r.get("top"):
            continue
        for path in r["top"][:2]:
            st, steps = c["state"], path["steps"]
            for k, step in enumerate(steps):
                evo = evos.get(step["evo"])
                if not evo:
                    missing.add(step["evo"])
                    break
                st = E.apply_evolution(st, evo, letters_to_choices(evo, step["letters"]))
                pos = pos_key or (path.get("ggPos") if k == len(steps) - 1 else None)
                if pos in POS and step.get("gg"):
                    out.append((st, POS[pos], float(step["gg"]), int(ea)))
    if missing:
        print("évolutions inconnues :", sorted(missing))
    return out


class Model:
    def __init__(self, m=None):
        m = m or json.loads(MODEL.read_text(encoding="utf-8"))
        self.ps, self.w = m["ps"], {int(k): np.array(v) for k, v in m["w"].items()}
        self.role_pos = {int(k): v for k, v in json.loads(ROLE_POS.read_text(encoding="utf-8")).items()}

    def feats(self, st, pos):
        plus = sum(1 for r in st["rolesPlus"] if self.role_pos.get(r % 100) == pos)
        pp = sum(1 for r in st["rolesPlusPlus"] if self.role_pos.get(r % 100) == pos)
        return np.array([st.get(a) or 0 for a in ATTRS] + [1.0 if i in st["playstyles"] else 0.0 for i in self.ps]
                        + [1.0 if i in st["playstylesPlus"] else 0.0 for i in self.ps]
                        + [st.get("skillMoves") or 0, st.get("weakFoot") or 0, plus, pp, 1.0], float)

    def pos_gg(self, st, pos):
        w = self.w.get(GROUP.get(pos, pos))
        return float(self.feats(st, pos) @ w) if w is not None else None

    def __call__(self, st):
        best, best_pos = -1e9, None
        for p in {st["position"], *st["alternativePositionIds"]}:
            v = self.pos_gg(st, p)
            if v is not None and v > best:
                best, best_pos = v, p
        return best, best_pos


def to_state(s):
    st = dict(s["attrs"])
    st.update(skillMoves=s["skillMoves"], weakFoot=s["weakFoot"], playstyles=s["playstyles"], playstylesPlus=s["playstylesPlus"],
              rolesPlus=s["rolesPlus"], rolesPlusPlus=s["rolesPlusPlus"])
    return st


def fit(exclude=(), model_path=MODEL):
    role_pos = {int(k): v for k, v in json.loads(ROLE_POS.read_text(encoding="utf-8")).items()}
    rows = []  # (state, pos, gg, card, weight)
    for s in json.loads(TRAIN.read_text(encoding="utf-8")):
        best = {}
        for r, g in s["roles"].items():
            p = role_pos.get(int(r))
            if p is not None:
                best[p] = max(best.get(p, 0), g)
        st = to_state(s)
        for p, g in best.items():
            rows.append((st, p, g, s["card"], 1.0))
    gen = [g for g in replay() if g[3] not in set(exclude)]
    # each step gives the best position GG, but only the last one names the position:
    # earlier steps are kept for the position of the final card
    final_pos = {}
    for st, p, g, ea in gen:
        if p is not None:
            final_pos[ea] = p
    n_gen = 0
    for st, p, g, ea in gen:
        p = p if p is not None else final_pos.get(ea)
        if p is not None:
            rows.append((st, p, g, ea, 4.0))
            n_gen += 1
    print(f"{len(rows)} exemples dont {n_gen} rejoués depuis le générateur FUT.GG")
    ps = sorted({i for r in rows for i in r[0]["playstyles"] + r[0]["playstylesPlus"]})
    model = Model({"ps": ps, "w": {}})
    cards = sorted({r[3] for r in rows})
    random.seed(2)
    test = set(random.sample(cards, len(cards) // 5))
    errs = []
    out = {"ps": ps, "w": {}}
    for p in sorted({GROUP.get(r[1], r[1]) for r in rows}):
        sub = [r for r in rows if GROUP.get(r[1], r[1]) == p]
        X = np.array([model.feats(r[0], r[1]) for r in sub])
        y = np.array([r[2] for r in sub])
        wt = np.array([r[4] for r in sub])
        tr = np.array([r[3] not in test for r in sub])

        def solve(m):
            A = X[m] * np.sqrt(wt[m])[:, None]
            return np.linalg.solve(A.T @ A + 3.0 * np.eye(X.shape[1]), A.T @ (y[m] * np.sqrt(wt[m])))

        w = solve(tr)
        e = np.abs(X[~tr] @ w - y[~tr])
        hi = (y[~tr] >= 82)
        errs.extend(e.tolist())
        print(f"poste {p:2d} : {len(sub):5d} ex., erreur {e.mean():.2f}, sur GG >= 82 : {e[hi].mean() if hi.any() else float('nan'):.2f}")
        out["w"][p] = solve(np.ones(len(sub), bool)).tolist()
    print(f"global cartes jamais vues : erreur moyenne {np.mean(errs):.2f}")
    Path(model_path).write_text(json.dumps(out), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "fit":
        fit()


# ---------- version 2: chemistry styles (FUT.GG scores every style and keeps the best) ----------

CHEM = json.loads((ROOT / "data" / "chem_styles.json").read_text(encoding="utf-8"))
GK_STYLES = [s for s, v in CHEM.items() if "attributeGkDiving" in v]
OUT_STYLES = [s for s in CHEM if s not in GK_STYLES]
MODEL2 = ROOT / "data" / "gg_pos_model2.json"
NA = len(ATTRS)


class Model2(Model):
    """GG(pos) = max over chemistry styles of a linear score on the boosted
    attributes (3 chemistry, capped at 99)."""

    def __init__(self, m=None):
        m = m or json.loads(MODEL2.read_text(encoding="utf-8"))
        super().__init__(m)
        # one row of attribute boosts per style, outfield and keeper styles apart
        self.boosts = {gk: np.array([[CHEM[s].get(a, 0) for a in ATTRS] for s in (GK_STYLES if gk else OUT_STYLES)], float)
                       for gk in (True, False)}

    def styles(self, pos):
        return GK_STYLES if pos == 0 else OUT_STYLES

    def style_feats(self, st, pos):
        f = self.feats(st, pos)
        b = self.boosts[pos == 0]
        out = np.repeat(f[None, :], len(b), axis=0)
        out[:, :NA] = np.minimum(f[:NA] + b, np.maximum(f[:NA], 99))
        return out

    def pos_gg(self, st, pos):
        w = self.w.get(GROUP.get(pos, pos))
        return float((self.style_feats(st, pos) @ w).max()) if w is not None else None


def fit2(exclude=(), model_path=MODEL2, verbose=True):
    role_pos = {int(k): v for k, v in json.loads(ROLE_POS.read_text(encoding="utf-8")).items()}
    rows = []
    for s in json.loads(TRAIN.read_text(encoding="utf-8")):
        best = {}
        for r, g in s["roles"].items():
            p = role_pos.get(int(r))
            if p is not None:
                best[p] = max(best.get(p, 0), g)
        st = to_state(s)
        for p, g in best.items():
            rows.append((st, p, g, s["card"], 1.0))
    ex = set(exclude)
    gen = [g for g in replay() if g[3] not in ex]
    final_pos = {ea: p for st, p, g, ea in gen if p is not None}
    for st, p, g, ea in gen:
        p = p if p is not None else final_pos.get(ea)
        if p is not None:
            rows.append((st, p, g, ea, 4.0))
    ps = sorted({i for r in rows for i in r[0]["playstyles"] + r[0]["playstylesPlus"]})
    model = Model2({"ps": ps, "w": {}})
    out = {"ps": ps, "w": {}}
    for p in sorted({GROUP.get(r[1], r[1]) for r in rows}):
        sub = [r for r in rows if GROUP.get(r[1], r[1]) == p]
        A = np.array([model.style_feats(r[0], r[1]) for r in sub])  # n x styles x features
        y = np.array([r[2] for r in sub])
        wt = np.sqrt(np.array([r[4] for r in sub]))
        pick = np.zeros(len(sub), int)  # Basic for everyone at first
        basic = model.styles(p).index("GK Basic" if p == 0 else "Basic")
        pick[:] = basic
        for _ in range(8):
            X = A[np.arange(len(sub)), pick]
            Xw = X * wt[:, None]
            w = np.linalg.solve(Xw.T @ Xw + 3.0 * np.eye(X.shape[1]), Xw.T @ (y * wt))
            pick = (A @ w).argmax(1)
        err = np.abs((A @ w).max(1) - y)
        if verbose:
            print(f"poste {p:2d} : {len(sub):5d} ex., erreur (entraînement) {err.mean():.2f}")
        out["w"][p] = w.tolist()
    Path(model_path).write_text(json.dumps(out), encoding="utf-8")


if __name__ == "__main__" and sys.argv[1] == "fit2":
    fit2()
