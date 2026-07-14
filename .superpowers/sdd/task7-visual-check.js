const { chromium } = require("playwright");
const path = require("path");

const baseUrl = process.argv[2] || "http://127.0.0.1:8765/index.html";
const outputDir = process.argv[3] || path.resolve(".superpowers/sdd");
const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];
const expectedSeasons = [
  { key: "2026-27", label: "2026/27" },
  { key: "2025-26", label: "2025/26" },
];

function assertState(condition, message) {
  if (!condition) throw new Error(message);
}

async function archiveState(page) {
  return page.evaluate(() => {
    const root = document.querySelector("#t1");
    const text = root ? root.innerText : "";
    const metricBoxes = Array.from(document.querySelectorAll(".aimetric"))
      .filter((element) => {
        const style = getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden";
      })
      .map((element) => element.getBoundingClientRect());
    let metricOverlap = false;
    for (let i = 0; i < metricBoxes.length; i += 1) {
      for (let j = i + 1; j < metricBoxes.length; j += 1) {
        const a = metricBoxes[i];
        const b = metricBoxes[j];
        const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (width > 1 && height > 1) metricOverlap = true;
      }
    }
    const guesses = window.gG ? window.gG() : {};
    const matchdayGuesses = guesses[29] || {};
    const visibleGuesses = Array.from(document.querySelectorAll("#t1 .gc.has")).length;
    const scoreControls = Array.from(document.querySelectorAll("#t1 .wn, #t1 .si input"));
    return {
      league: window.D && window.D.league,
      season: window.D && window.D.llSeason,
      archive: Boolean(window.D && window.D.arch),
      selectedMatchday: window.D && window.D.sel,
      fixtureCount: window.D && window.D.fx ? window.D.fx.length : 0,
      historicalGuessCount: Object.values(matchdayGuesses).filter(
        (guess) => guess && (guess.w || guess.hs != null || guess.as != null),
      ).length,
      visibleGuesses,
      fillControls: document.querySelectorAll("#t1 .af-bar, #t1 .af-btn").length,
      scoreControlsDisabled: scoreControls.every((control) => control.disabled),
      scoreControlCount: scoreControls.length,
      textHasBadNumber: /\b(?:NaN|undefined|Infinity)\b/.test(text),
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      metricOverlap,
    };
  });
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.BROWSER_EXECUTABLE || undefined,
  });
  const results = [];
  const failures = [];
  try {
    for (const viewport of viewports) {
      const page = await browser.newPage({ viewport });
      const consoleErrors = [];
      const pageErrors = [];
      const httpErrors = [];
      page.on("console", (message) => {
        if (message.type() === "error") consoleErrors.push(message.text());
      });
      page.on("pageerror", (error) => pageErrors.push(error.message));
      page.on("response", (response) => {
        if (response.status() >= 400) httpErrors.push({ status: response.status(), url: response.url() });
      });

      try {
        await page.goto(baseUrl, { waitUntil: "networkidle" });
        await page.click('.lb[data-l="laliga"]');
        await page.waitForSelector("#seasonSel", { state: "visible" });
        const selector = await page.locator("#seasonSel").evaluate((element) => ({
          value: element.value,
          options: Array.from(element.options).map((option) => ({ key: option.value, label: option.text })),
        }));
        assertState(JSON.stringify(selector.options) === JSON.stringify(expectedSeasons), `${viewport.name}: La Liga selector options were ${JSON.stringify(selector.options)}`);
        assertState(selector.value === "2026-27", `${viewport.name}: La Liga did not begin on 2026-27`);

        await page.click("#n1");
        await page.screenshot({ path: path.join(outputDir, `task7-laliga-current-${viewport.name}.png`), fullPage: true });
        const current = await archiveState(page);
        assertState(current.league === "laliga" && current.season === "2026-27" && !current.archive, `${viewport.name}: current La Liga state was not active`);
        assertState(current.fixtureCount === 380, `${viewport.name}: current La Liga fixture count was ${current.fixtureCount}`);

        await page.selectOption("#seasonSel", "2025-26");
        await page.waitForFunction(() => window.D && window.D.llSeason === "2025-26" && window.D.arch);
        await page.evaluate(() => window.sGW(29));
        await page.waitForFunction(() => window.D && window.D.sel === 29);
        const archive = await archiveState(page);
        await page.screenshot({ path: path.join(outputDir, `task7-laliga-archive-${viewport.name}.png`), fullPage: true });
        assertState(archive.league === "laliga" && archive.season === "2025-26" && archive.archive, `${viewport.name}: archive state was not active`);
        assertState(archive.fixtureCount === 380, `${viewport.name}: archive La Liga fixture count was ${archive.fixtureCount}`);
        assertState(archive.selectedMatchday === 29, `${viewport.name}: archive did not show matchday 29`);
        assertState(archive.historicalGuessCount === 8 && archive.visibleGuesses === 8, `${viewport.name}: archive guesses were ${archive.historicalGuessCount}/${archive.visibleGuesses}, expected 8/8`);
        assertState(archive.fillControls === 0, `${viewport.name}: archive exposed fill controls`);
        assertState(archive.scoreControlCount > 0 && archive.scoreControlsDisabled, `${viewport.name}: archive score controls were not disabled`);
        assertState(!archive.textHasBadNumber, `${viewport.name}: archive rendered a bad numeric value`);
        assertState(!archive.horizontalOverflow, `${viewport.name}: archive has horizontal overflow`);
        assertState(!archive.metricOverlap, `${viewport.name}: archive has overlapping metrics`);

        await page.selectOption("#seasonSel", "2026-27");
        await page.waitForFunction(() => window.D && window.D.llSeason === "2026-27" && !window.D.arch);
        const restored = await archiveState(page);
        assertState(restored.fixtureCount === 380, `${viewport.name}: current La Liga fixtures did not restore`);
        results.push({ viewport: viewport.name, selector, current, archive, restored });
      } catch (error) {
        failures.push({ viewport: viewport.name, error: error.message });
      }

      const significantHttp = httpErrors.filter((entry) => !/\/favicon\.ico(?:\?|$)/.test(entry.url));
      const significantConsole = significantHttp.length
        ? consoleErrors
        : consoleErrors.filter((message) => !/status of 404/.test(message));
      if (significantConsole.length || pageErrors.length || significantHttp.length) {
        failures.push({ viewport: viewport.name, consoleErrors: significantConsole, pageErrors, httpErrors: significantHttp });
      }
      await page.close();
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ results, failures }, null, 2));
  if (failures.length) process.exitCode = 1;
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
