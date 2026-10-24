"""Evolution engine: apply FUT.GG's active evolutions to a card, step by step.

Validated against FUT.GG's own published paths: same start card + same
evolution + same choices must give the same card (attributes, overall, face
stats, PlayStyles, roles, rarity).

    py scripts/evo_engine.py collect   # raw FUT.GG paths -> data/cache/evo_paths.json
    py scripts/evo_engine.py validate  # replays every transition and reports mismatches
"""

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "cache" / "evo_paths.json"
EVOS = ROOT / "scratch" / "activeevos.json"
API = "https://www.fut.gg/api/fut/evolutions/v2/27"
H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}

FACE = ("facePace", "faceShooting", "facePassing", "faceDribbling", "faceDefending", "facePhysicality")


def camel(s):
    """attribute_sprint_speed -> attributeSprintSpeed"""
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


def get(url):
    for _ in range(3):
        try:
            r = requests.get(url, headers=H, timeout=30)
            if r.ok:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(1)
    return {}


def collect(n_cards=250):
    seeds = []
    for q in (
        "sorts=-overall&overall_delta__gte=6&collapse_by_base_player=true",
        "sorts=-gg_rating",
        "sorts=-overall_delta&collapse_by_base_player=true",
    ):
        for page in range(1, 5):
            d = get(f"{API}/v2/players/?all_evolutions_combinations=true&{q}&page={page}")
            for x in d.get("data", []):
                if x["eaId"] not in seeds:
                    seeds.append(x["eaId"])
            time.sleep(0.2)
    out = {}
    for k, eid in enumerate(seeds[:n_cards]):
        paths = []
        for page in range(1, 4):
            d = get(f"{API}/paths/v2/{eid}/?player_item_ea_ids={eid}" + (f"&page={page}" if page > 1 else ""))
            paths += d.get("data", [])
            if not d.get("next"):
                break
            time.sleep(0.15)
        out[eid] = paths
        if k % 50 == 0:
            print(f"{k} cartes")
        time.sleep(0.15)
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps(out), encoding="utf-8")
    print(f"{len(out)} cartes, {sum(len(v) for v in out.values())} parcours")


# ---------- engine ----------

def state_of(step):
    st = {k: v for k, v in step.items() if k.startswith("attribute") and isinstance(v, (int, float))}
    for k in ("overall", "skillMoves", "weakFoot", "rarityEaId", "position", "dateOfBirth") + FACE:
        st[k] = step.get(k)
    st["alternativePositionIds"] = list(step.get("alternativePositionIds") or [])
    for k in ("playstyles", "playstylesPlus", "rolesPlus", "rolesPlusPlus"):
        st[k] = list(step.get(k) or [])
    return st


# Attributes behind each face stat (an upgrade of a face stat raises each one).
FACE_GROUPS = {
    "face_pace": ["attributeAcceleration", "attributeSprintSpeed"],
    "face_shooting": ["attributePositioning", "attributeFinishing", "attributeShotPower", "attributeLongShots", "attributeVolleys", "attributePenalties"],
    "face_passing": ["attributeVision", "attributeCrossing", "attributeFkAccuracy", "attributeShortPassing", "attributeLongPassing", "attributeCurve"],
    "face_dribbling": ["attributeAgility", "attributeBalance", "attributeReactions", "attributeBallControl", "attributeDribbling", "attributeComposure"],
    "face_defending": ["attributeInterceptions", "attributeHeadingAccuracy", "attributeDefensiveAwareness", "attributeStandingTackle", "attributeSlidingTackle"],
    "face_physicality": ["attributeJumping", "attributeStamina", "attributeStrength", "attributeAggression"],
}


def apply_upgrade(st, u):
    kind, val, cap = u["upgrade"], u["value"], u.get("maxValue")
    if kind.startswith("attribute_") or kind == "overall" or kind in ("skill_moves", "weak_foot"):
        key = "overall" if kind == "overall" else camel(kind)
        cur = st.get(key) or 0
        if cap is None or cur < cap:
            st[key] = min(cur + val, cap) if cap is not None else cur + val
    elif kind in FACE_GROUPS:
        for key in FACE_GROUPS[kind]:
            cur = st.get(key) or 0
            if cap is None or cur < cap:
                st[key] = min(cur + val, cap) if cap is not None else cur + val
    elif kind == "play_style":
        # maxValue = maximum number of PlayStyles on the card
        have = len(st["playstyles"]) + len(st["playstylesPlus"])
        if val not in st["playstyles"] and val not in st["playstylesPlus"] and (cap is None or have < cap):
            st["playstyles"].append(val)
    elif kind == "play_style_plus":
        have = len(st["playstylesPlus"])
        if val not in st["playstylesPlus"] and (cap is None or have < cap):
            if val in st["playstyles"]:
                st["playstyles"].remove(val)
            st["playstylesPlus"].append(val)
    elif kind == "role_plus":
        if val not in st["rolesPlus"] and val + 100 not in st["rolesPlusPlus"]:
            st["rolesPlus"].append(val)
    elif kind == "role_plus_plus":
        if val not in st["rolesPlusPlus"]:
            st["rolesPlusPlus"].append(val)
        if val - 100 in st["rolesPlus"]:
            st["rolesPlus"].remove(val - 100)
    elif kind == "rarity_id":
        st["rarityEaId"] = val
    elif kind.startswith("gk_face_"):
        key = "attributeGk" + camel(kind[len("gk_face_"):]).capitalize()
        cur = st.get(key) or 0
        if cap is None or cur < cap:
            st[key] = min(cur + val, cap) if cap is not None else cur + val
    # cosmetic upgrades do not change the player


def level_upgrades(level, choice):
    opts = level.get("upgradeOptions") or []
    if opts:
        o = next((x for x in opts if x["idx"] == choice), opts[0])
        return o["upgrades"]
    return level["upgrades"]


def apply_evolution(st, evo, choices):
    st = json.loads(json.dumps(st))
    for i, level in enumerate(evo["levels"] or []):
        choice = choices[i] if i < len(choices) else 1
        for u in level_upgrades(level, choice):
            apply_upgrade(st, u)
    return st


FACE_WEIGHTS = {
    "facePace": {"attributeAcceleration": .45, "attributeSprintSpeed": .55},
    "faceShooting": {"attributePositioning": .05, "attributeFinishing": .45, "attributeShotPower": .20, "attributeLongShots": .20, "attributeVolleys": .05, "attributePenalties": .05},
    "facePassing": {"attributeVision": .20, "attributeCrossing": .20, "attributeFkAccuracy": .05, "attributeShortPassing": .35, "attributeLongPassing": .15, "attributeCurve": .05},
    "faceDribbling": {"attributeAgility": .10, "attributeBalance": .05, "attributeReactions": .05, "attributeBallControl": .30, "attributeDribbling": .45, "attributeComposure": .05},
    "faceDefending": {"attributeInterceptions": .20, "attributeHeadingAccuracy": .10, "attributeDefensiveAwareness": .30, "attributeStandingTackle": .30, "attributeSlidingTackle": .10},
    "facePhysicality": {"attributeJumping": .05, "attributeStamina": .25, "attributeStrength": .50, "attributeAggression": .20},
}


def face(st, name):
    return round(sum(st.get(a, 0) * w for a, w in FACE_WEIGHTS[name].items()) + 1e-9)


def eligible(st, evo, pos_mode="all", ps_mode="all"):
    """Can this card start this evolution? Returns (ok, reason)."""
    r = evo.get("requirements") or {}
    positions = [st["position"]] + (st["alternativePositionIds"] if pos_mode == "all" else [])
    if r.get("positions") and not any(p in r["positions"] for p in positions):
        return False, "poste"
    if r.get("excludedPositions") and st["position"] in r["excludedPositions"]:
        return False, "poste exclu"
    if r.get("maxOverall") is not None and st["overall"] > r["maxOverall"]:
        return False, "note max"
    if r.get("minOverall") is not None and st["overall"] < r["minOverall"]:
        return False, "note min"
    if r.get("rarities") and st["rarityEaId"] not in r["rarities"]:
        return False, "rareté"
    if r.get("excludedRarities") and st["rarityEaId"] in r["excludedRarities"]:
        return False, "rareté exclue"
    n_ps = len(st["playstyles"]) + (len(st["playstylesPlus"]) if ps_mode == "all" else 0)
    if r.get("maxPlaystyles") is not None and n_ps > r["maxPlaystyles"]:
        return False, "PlayStyles"
    if r.get("maxPlaystylesPlus") is not None and len(st["playstylesPlus"]) > r["maxPlaystylesPlus"]:
        return False, "PlayStyles+"
    if r.get("maxFacePace") is not None and face(st, "facePace") > r["maxFacePace"]:
        return False, "vitesse max"
    if r.get("maxFaceDefending") is not None and face(st, "faceDefending") > r["maxFaceDefending"]:
        return False, "défense max"
    if r.get("totalNumberOfPositions") is not None and 1 + len(st["alternativePositionIds"]) > r["totalNumberOfPositions"]:
        return False, "nombre de postes"
    if r.get("minBirthDate") and st.get("dateOfBirth"):
        if int(str(st["dateOfBirth"]).replace("-", "")[:8]) < int(r["minBirthDate"]):
            return False, "âge"
    if r.get("maxSkillMoves") is not None and (st.get("skillMoves") or 0) > r["maxSkillMoves"]:
        return False, "gestes techniques"
    for k, v in r.items():
        if k.startswith("maxAttribute") and v is not None:
            if st.get("attribute" + k[len("maxAttribute"):], 0) > v:
                return False, k
    return True, ""


def validate_eligibility():
    evos = {e["id"]: e for e in json.loads(EVOS.read_text(encoding="utf-8"))}
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    for pos_mode in ("main", "all"):
        for ps_mode in ("basic", "all"):
            n = bad = 0
            why = Counter()
            for paths in raw.values():
                for pth in paths:
                    steps = pth["path"]
                    for k, s in enumerate(steps[1:]):
                        e = evos.get(s.get("evolutionId"))
                        if not e:
                            continue
                        n += 1
                        ok, reason = eligible(state_of(steps[k]), e, pos_mode, ps_mode)
                        if not ok:
                            bad += 1
                            why[(e["name"], reason)] += 1
            print(f"postes={pos_mode:4} PlayStyles={ps_mode:5} : {n - bad}/{n} transitions FUT.GG acceptées", why.most_common(3))


# ---------- validation ----------

COMPARE = ["overall", "skillMoves", "weakFoot", "rarityEaId", "playstyles", "playstylesPlus", "rolesPlus", "rolesPlusPlus"]


def diff(a, b):
    bad = []
    keys = set(k for k in b if k.startswith("attribute")) | set(COMPARE)
    for k in sorted(keys):
        va, vb = a.get(k), b.get(k)
        if isinstance(vb, list):
            va, vb = sorted(va or []), sorted(vb or [])
        if va != vb:
            bad.append((k, va, vb))
    return bad


def validate():
    evos = {e["id"]: e for e in json.loads(EVOS.read_text(encoding="utf-8"))}
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    ok = n = 0
    why = Counter()
    examples = {}
    for eid, paths in raw.items():
        for pth in paths:
            steps = pth["path"]
            evo_steps = [s for s in steps[1:]]
            choices = pth.get("evolutionPathChoices") or []
            prev = state_of(steps[0])
            for k, s in enumerate(evo_steps):
                e = evos.get(s.get("evolutionId"))
                if not e:
                    prev = state_of(s)
                    continue
                got = apply_evolution(prev, e, choices[k] if k < len(choices) else [])
                want = state_of(s)
                d = diff(got, want)
                n += 1
                if not d:
                    ok += 1
                else:
                    for item in d:
                        why[item[0]] += 1
                        examples.setdefault(item[0], (e["name"], item))
                prev = want
    print(f"{ok} / {n} transitions identiques à FUT.GG ({100 * ok / max(n, 1):.1f} %)")
    for k, c in why.most_common(12):
        print(f"  écart sur {k} : {c} fois, ex. {examples[k]}")


if __name__ == "__main__":
    {"collect": collect, "validate": validate, "eligibility": validate_eligibility}[sys.argv[1]]()
