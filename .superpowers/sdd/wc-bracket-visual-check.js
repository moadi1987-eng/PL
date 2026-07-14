const { chromium } = require('playwright');
const assert = require('assert');
const path = require('path');

const url = process.argv[2] || 'http://127.0.0.1:8765/index.html';
const output = process.argv[3] || '.superpowers/sdd';
const viewports = [{ name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 }];

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || undefined });
  const results = [];
  const finalFailures = [];
  function verify(name, check) {
    try { check(); } catch (error) { finalFailures.push(`${name}: ${error.message}`); }
  }
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
        const canvas = scroll.querySelector('.wcb-canvas');
        return {
          cards: cards.length,
          paths: document.querySelectorAll('.wcb-line').length,
          loserPaths: document.querySelectorAll('.wcb-line.loser').length,
          overlap,
          pageOverflow: document.documentElement.scrollWidth > innerWidth + 1,
          scrollLeft: scroll.scrollLeft,
          maxScroll: scroll.scrollWidth - scroll.clientWidth,
          canvasOffset: canvas.getBoundingClientRect().left - scroll.getBoundingClientRect().left,
          canvasGap: scroll.clientWidth - canvas.offsetWidth,
          badText: /undefined|NaN|Infinity/.test(scroll.innerText),
        };
      });
      assert.strictEqual(before.cards, 32);
      assert.strictEqual(before.paths, 32);
      assert.strictEqual(before.loserPaths, 2);
      assert.strictEqual(before.overlap, false);
      assert.strictEqual(before.pageOverflow, false);
      assert.strictEqual(before.badText, false);
      if (viewport.name === 'desktop') {
        verify('desktop canvas is centered', () => {
          assert(before.canvasGap > 0);
          assert(Math.abs(before.canvasOffset - before.canvasGap / 2) < 3);
        });
      }
      if (viewport.name === 'mobile') await page.locator('.wcb-scroll').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `wc-bracket-${viewport.name}.png`), fullPage: viewport.name === 'desktop' });
      if (viewport.name === 'mobile') {
        assert(before.maxScroll > 0);
        assert(before.scrollLeft > before.maxScroll * 0.35 && before.scrollLeft < before.maxScroll * 0.65);
        const liveBefore = await page.evaluate(() => {
          const scroll = document.querySelector('.wcb-scroll');
          scroll.scrollLeft = 140;
          scroll.dispatchEvent(new Event('scroll'));
          const card = document.querySelector('[data-bracket-key="r32-0"]');
          const rect = card.getBoundingClientRect();
          return { width: rect.width, height: rect.height, scrollLeft: scroll.scrollLeft };
        });
        await page.evaluate(() => {
          const source = window._wcBracketModel.rounds.r32[0];
          const fixture = window.D.fx.find((match) => String(match.id) === String(source.id));
          Object.assign(fixture, { st: true, fin: false, hs: 2, as: 1, mn: 67, sx: '' });
          window.rRes();
        });
        await page.waitForFunction(() => document.querySelector('[data-bracket-key="r32-0"]')?.classList.contains('live'));
        await page.waitForTimeout(50);
        const liveAfter = await page.evaluate(() => {
          const scroll = document.querySelector('.wcb-scroll');
          const card = document.querySelector('[data-bracket-key="r32-0"]');
          const rect = card.getBoundingClientRect();
          return {
            width: rect.width,
            height: rect.height,
            scrollLeft: scroll.scrollLeft,
            paths: document.querySelectorAll('.wcb-line').length,
            live: card.classList.contains('live'),
            status: card.querySelector('.wcb-stage span:last-child').innerText,
            scores: Array.from(card.querySelectorAll('.wcb-team b'), (score) => score.innerText),
          };
        });
        assert(Math.abs(liveAfter.scrollLeft - liveBefore.scrollLeft) < 3);
        assert(Math.abs(liveAfter.width - liveBefore.width) < 0.1);
        assert(Math.abs(liveAfter.height - liveBefore.height) < 0.1);
        assert.strictEqual(liveAfter.paths, 32);
        assert.strictEqual(liveAfter.live, true);
        assert.match(liveAfter.status, /LIVE/);
        assert.deepStrictEqual(liveAfter.scores, ['2', '1']);
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        await page.waitForTimeout(50);
        const navClearance = await page.evaluate(() => {
          const bracket = document.querySelector('.wcb-scroll').getBoundingClientRect();
          const nav = document.querySelector('.bnav').getBoundingClientRect();
          return { bracketBottom: bracket.bottom, navTop: nav.top, intersects: bracket.bottom > nav.top && bracket.top < nav.bottom };
        });
        assert.strictEqual(navClearance.intersects, false);
      }
      if (viewport.name === 'desktop') {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.waitForTimeout(100);
        const resized = await page.locator('.wcb-scroll').evaluate((scroll) => ({
          scrollLeft: scroll.scrollLeft,
          maxScroll: scroll.scrollWidth - scroll.clientWidth,
        }));
        verify('first overflow after desktop-to-mobile resize is centered', () => {
          assert(resized.maxScroll > 0);
          assert(resized.scrollLeft > resized.maxScroll * 0.35 && resized.scrollLeft < resized.maxScroll * 0.65);
        });
        results.push({ viewport: 'desktop-to-mobile', ...resized });
      }
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
  if (finalFailures.length) throw new Error(finalFailures.join('\n'));
  console.log(JSON.stringify(results));
})().catch((error) => { console.error(error); process.exit(1); });
