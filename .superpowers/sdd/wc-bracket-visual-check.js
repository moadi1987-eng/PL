const { chromium } = require('playwright');
const assert = require('assert');
const path = require('path');

const url = process.argv[2] || 'http://127.0.0.1:8765/index.html';
const output = process.argv[3] || '.superpowers/sdd';
const viewports = [{ name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 }];

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || undefined });
  const results = [];
  try {
    for (const viewport of viewports) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto(url, { waitUntil: 'networkidle' });
      await page.click('.lb[data-l="wc"]');
      await page.waitForSelector('.wcb-card');
      const before = await page.evaluate(() => {
        const scroll = document.querySelector('.wcb-scroll');
        const cards = Array.from(document.querySelectorAll('.wcb-card'));
        const rects = cards.map((card) => card.getBoundingClientRect());
        let overlap = false;
        for (let i = 0; i < rects.length; i += 1) for (let j = i + 1; j < rects.length; j += 1) {
          const x = Math.min(rects[i].right, rects[j].right) - Math.max(rects[i].left, rects[j].left);
          const y = Math.min(rects[i].bottom, rects[j].bottom) - Math.max(rects[i].top, rects[j].top);
          if (x > 1 && y > 1) overlap = true;
        }
        return {
          cards: cards.length,
          paths: document.querySelectorAll('.wcb-line').length,
          loserPaths: document.querySelectorAll('.wcb-line.loser').length,
          overlap,
          pageOverflow: document.documentElement.scrollWidth > innerWidth + 1,
          scrollLeft: scroll.scrollLeft,
          maxScroll: scroll.scrollWidth - scroll.clientWidth,
          badText: /undefined|NaN|Infinity/.test(scroll.innerText),
        };
      });
      assert.strictEqual(before.cards, 32);
      assert.strictEqual(before.paths, 32);
      assert.strictEqual(before.loserPaths, 2);
      assert.strictEqual(before.overlap, false);
      assert.strictEqual(before.pageOverflow, false);
      assert.strictEqual(before.badText, false);
      if (viewport.name === 'mobile') {
        assert(before.maxScroll > 0);
        assert(before.scrollLeft > before.maxScroll * 0.35 && before.scrollLeft < before.maxScroll * 0.65);
        await page.evaluate(() => { const el = document.querySelector('.wcb-scroll'); el.scrollLeft = 140; el.dispatchEvent(new Event('scroll')); window.rRes(); });
        await page.waitForTimeout(50);
        const restored = await page.locator('.wcb-scroll').evaluate((el) => el.scrollLeft);
        assert(Math.abs(restored - 140) < 3);
      }
      await page.screenshot({ path: path.join(output, `wc-bracket-${viewport.name}.png`), fullPage: true });
      await page.click('.lb[data-l="laliga"]');
      assert.strictEqual(await page.locator('.wcb-card').count(), 0);
      await page.click('.lb[data-l="pl"]');
      assert.strictEqual(await page.locator('.wcb-card').count(), 0);
      results.push({ viewport: viewport.name, ...before });
      assert.deepStrictEqual(errors, []);
      await page.close();
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(results));
})().catch((error) => { console.error(error); process.exit(1); });
