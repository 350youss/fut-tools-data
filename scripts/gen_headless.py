"""Runs FUT.GG's evolution generator (Evo Lab, GENERATE tab) in a headless
Chrome, card after card, and saves each result to disk right away.

Season Pass, Premium Season Pass and reward evolutions are on, up to 20
evolutions per path; the GG Rating is the one FUT.GG computes.

    py scripts/gen_headless.py data/cache/gen_queue_sample.json [workers]

Results: data/cache/futgg_gen.json ({eaId: {found, complete, seconds, top, sig}}).
sig identifies the set of active evolutions (env FT_SIG, see evo_auto.py):
cards already computed with the same set are skipped, so a run can be
stopped and resumed.
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("FT_OUT") or ROOT / "data" / "cache" / "futgg_gen.json")
LAB = "https://www.fut.gg/evo-lab/"
KEY = "FUTGG_EVO_LAB_APP_27"
TIMEOUT_S = 300
SIG = os.environ.get("FT_SIG")
# stop taking new cards after FT_DEADLINE seconds (GitHub jobs are capped at 6 h)
# Queue items are card ids (best position) or "id@POS" (results ranked by the best role
# at that position, e.g. "276976@ST"); FT_POS applies one position to plain ids.
POS = os.environ.get("FT_POS")
DEADLINE = time.time() + float(os.environ.get("FT_DEADLINE", "1e9"))

SELECT = """(eaId) => {
  const s = JSON.parse(localStorage[%r]);
  s.selectedPlayerEaId = eaId;
  s.activeTab = "DISCOVER";
  Object.assign(s.discoverSettings, { includeSeasonPass: true, includePremiumSeasonPass: true, includeRewardEvolutions: true,
    calculateGgRating: true, maxEvolutionsInPath: 20, mustIncludeEvolutionIds: [] });
  localStorage[%r] = JSON.stringify(s);
}""" % (KEY, KEY)

CLICK = """(eaId) => {
  if (JSON.parse(localStorage[%r]).selectedPlayerEaId !== eaId) return false;
  const ev = [...document.querySelectorAll("button")].find((b) => b.innerText.trim() === "Evolve");
  if (!ev) return false;
  ev.click();
  return true;
}""" % KEY

STATE = """() => {
  const t = document.querySelector("main")?.innerText || "";
  const m = t.match(/Stop · ([\\d,]+) found/);
  const n = [...document.querySelectorAll("button")].filter((b) => b.innerText.trim() === "Add to Compare").length;
  return { running: !!m, found: m ? +m[1].replace(/,/g, "") : 0, results: n };
}"""

# reads the first n results: per step the evolution, its options and the GG after it
READ = """(n) => {
  const btns = [...document.querySelectorAll("button")].filter((b) => b.innerText.trim() === "Add to Compare");
  return btns.slice(0, n).map((b) => {
    const tok = (b.closest(".rounded-md.bg-gray-800") || b.parentElement).innerText.split("\\n").map((x) => x.trim()).filter(Boolean);
    const isCard = (i) => /^\\d+$/.test(tok[i + 1] || "") && (tok[i + 3] === "PAC" || tok[i + 3] === "DIV");
    const nameAt = tok.findIndex((x, i) => isCard(i));
    const name = tok[nameAt];
    const nums = tok.slice(0, nameAt).filter((x) => /^[\\d,]+$/.test(x));
    const cards = [];
    for (let i = nameAt; i < tok.length - 1; i++) {
      if (tok[i] === name && isCard(i)) {
        const labels = tok.slice(i + 3, i + 15).filter((x, k) => k % 2 === 0);
        const c = { start: i, overall: +tok[i + 1], faces: {} };
        labels.forEach((f, k) => (c.faces[f] = +tok[i + 4 + 2 * k]));
        let j = i + 15;
        while (j < tok.length && !/^\\d+\\.\\d$/.test(tok[j])) j++;
        c.ggPos = tok[j - 1]; c.gg = +tok[j]; c.end = j;
        cards.push(c);
        i = j;
      }
    }
    const steps = [];
    for (let k = 1; k < cards.length; k++) {
      const lab = tok.slice(cards[k].end + 1, k + 1 < cards.length ? cards[k + 1].start : tok.length);
      if (!lab.length) continue;
      const letters = lab[1] && /^[A-E](\\s·\\s[A-E])*$/.test(lab[1]) ? lab[1].replace(/\\s/g, "") : "";
      steps.push({ evo: lab[0], letters, gg: cards[k].gg, overall: cards[k].overall, ggPos: cards[k].ggPos });
    }
    const last = cards[cards.length - 1] || {};
    return { name, points: nums.length >= 2 ? +nums[0].replace(/,/g, "") : 0, coins: nums.length ? +nums[nums.length - 1].replace(/,/g, "") : 0,
      expires: (tok[0] || "").replace("Expires on ", ""), startGg: cards[0]?.gg, overall: last.overall, gg: last.gg, ggPos: last.ggPos,
      faces: last.faces, steps };
  });
}"""


async def ev(page, js, arg=None, timeout=60):
    return await asyncio.wait_for(page.evaluate(js, arg), timeout)


async def one(page, item):
    ea, _, pos = str(item).partition("@")
    ea, pos = int(ea), pos or POS
    await ev(page, SELECT, ea)
    await page.goto(f"{LAB}?ft={ea}-{int(time.time())}", wait_until="domcontentloaded")
    if pos:
        # the menu keeps its last choice: its label is "GG Rating: ..." or the chosen role
        trig = "() => { const b = [...document.querySelectorAll('button[data-slot=dropdown-menu-trigger]')].find((b) => /GG Rating|Role|position/i.test(b.innerText)); return b ? b.innerText : null; }"
        label = None
        for _ in range(200):
            await asyncio.sleep(0.3)
            label = await ev(page, trig)
            if label:
                break
        if label and f"Best {pos} Role" not in label:
            await ev(page, "() => document.getElementById('uniccmp')?.remove()")  # cookie banner over the page (no consent given)
            await page.locator("button[data-slot=dropdown-menu-trigger]", has_text=re.compile("GG Rating|Role|position", re.I)).first.click(timeout=10000, force=True)
            choice = page.get_by_role("menuitem", name=f"Best {pos} Role", exact=True)
            if not await choice.count():
                await page.keyboard.press("Escape")
                return {"found": 0, "complete": True, "seconds": 0, "top": [], "none": True, "pos": pos}
            await choice.first.click(timeout=10000, force=True)
    for _ in range(100):
        await asyncio.sleep(0.3)
        if await ev(page, CLICK, ea):
            break
    else:
        raise RuntimeError("bouton Evolve introuvable")
    t0, started, found, clicks = time.time(), False, 0, 1
    while time.time() - t0 < TIMEOUT_S:
        await asyncio.sleep(0.7)
        try:
            s = await ev(page, STATE, timeout=90)
        except asyncio.TimeoutError:
            continue  # the page is busy computing
        if s["running"]:
            started, found = True, s["found"]
            continue
        el = time.time() - t0
        if started or (el > 1.5 and s["results"]):
            return {"found": found, "complete": True, "seconds": round(el), "top": await ev(page, READ, 4)}
        # a click sent before the page is ready does nothing: click again
        if not started and el > clicks * 4 and clicks < 6:
            await ev(page, CLICK, ea)
            clicks += 1
        if el > 45:  # six clicks and still nothing: no evolution path for this card
            return {"found": 0, "complete": True, "seconds": round(el), "top": [], "none": True}
    return {"found": found, "complete": False, "seconds": TIMEOUT_S, "top": await ev(page, READ, 4)}


async def worker(k, browser, queue, results, lock):
    ctx = await browser.new_context(viewport={"width": 1400, "height": 1000})
    page = await ctx.new_page()
    await page.goto(LAB, wait_until="domcontentloaded")
    for _ in range(60):  # the app writes its state on first load
        await asyncio.sleep(0.5)
        if await ev(page, f"() => !!localStorage[{KEY!r}]"):
            break
    while queue and time.time() < DEADLINE:
        ea = queue.pop(0)
        try:
            r = await one(page, ea)
        except Exception as e:  # noqa: BLE001
            r = {"error": str(e)[:200]}
        r["sig"] = SIG
        async with lock:
            results[str(ea)] = r
            OUT.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
            best = (r.get("top") or [{}])[0]
            print(f"[{k}] {ea} {best.get('name')} GG {best.get('gg')} {best.get('ggPos')} ({r.get('seconds')} s) | reste {len(queue)}", flush=True)
    await ctx.close()


async def main(queue_file, workers=3):
    results = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    queue = [ea for ea in json.loads(Path(queue_file).read_text(encoding="utf-8")) if str(ea) not in results or "error" in results[str(ea)] or (SIG and results[str(ea)].get("sig") != SIG)]
    print(f"{len(queue)} cartes à calculer, {workers} navigateurs")
    lock = asyncio.Lock()
    async with async_playwright() as p:
        # installed Chrome on the PC, Playwright's Chromium on GitHub Actions
        browser = await p.chromium.launch(channel=None if os.environ.get("CI") else "chrome", headless=True)
        await asyncio.gather(*(worker(k, browser, queue, results, lock) for k in range(workers)))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3))
