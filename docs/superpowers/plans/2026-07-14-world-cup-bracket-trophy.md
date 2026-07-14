# World Cup Bracket Trophy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a realistic transparent gold trophy image between the World Cup final and third-place cards.

**Architecture:** Generate one project-local PNG asset and render it as a decorative grid item inside the existing ready-state bracket. Extend the existing renderer test and verify the generated static site at desktop and mobile widths without changing bracket data or connector logic.

**Tech Stack:** Vanilla HTML/CSS/JavaScript, Node.js assertions, Python static-site generator, Playwright browser verification, PNG alpha asset.

## Global Constraints

- The asset path is exactly `static/world-cup-trophy.png`.
- Render width is approximately 52px on desktop and 46px on mobile.
- The image has no text, branding, frame, interaction, or external host dependency.
- The trophy renders only in a ready World Cup knockout bracket.
- Existing card positions, connectors, scrolling, live refresh, PL, and La Liga behavior remain unchanged.

---

### Task 1: Transparent Trophy Asset

**Files:**
- Create: `static/world-cup-trophy.png`

**Interfaces:**
- Produces: a transparent PNG consumed by the `.wcb-trophy` image in Task 2.

- [ ] **Step 1: Generate the chroma-key source image**

Use the built-in image generation tool with this exact prompt:

```text
Use case: product-mockup
Asset type: decorative World Cup knockout bracket asset
Primary request: a realistic front-facing gold football championship trophy, elegant and recognizable, isolated and centered
Scene/backdrop: perfectly flat solid #00ff00 chroma-key background
Composition/framing: full trophy visible with generous even padding, vertical and symmetrical
Lighting/mood: polished gold with restrained studio highlights
Constraints: one trophy only; crisp silhouette; no branding; no FIFA logo; no text; no watermark; no base shadow; no reflection; do not use #00ff00 in the trophy
Avoid: people, footballs, flags, confetti, pedestal, scenery, gradients, texture, lighting variation in the background
```

Expected: one centered trophy on a uniform green background.

- [ ] **Step 2: Remove the chroma key**

Copy the generated source to `tmp/imagegen/world-cup-trophy-source.png`, then run:

```powershell
python "$env:USERPROFILE\.codex\skills\.system\imagegen\scripts\remove_chroma_key.py" --input tmp/imagegen/world-cup-trophy-source.png --out static/world-cup-trophy.png --auto-key border --soft-matte --transparent-threshold 12 --opaque-threshold 220 --despill
```

Expected: `static/world-cup-trophy.png` has an alpha channel and transparent corners.

- [ ] **Step 3: Validate and commit the asset**

Inspect the PNG and verify the trophy is centered, fully visible, has no green fringe, and contains no text or branding. Then run:

```powershell
git add static/world-cup-trophy.png
git commit -m "assets: add World Cup bracket trophy"
```

Expected: one asset commit with no unrelated files.

### Task 2: Trophy Rendering And Styling

**Files:**
- Modify: `tests/test_wc_bracket_render.js`
- Modify: `website/pl_mobile_template.html:54-69`
- Modify: `website/pl_mobile_template.html:1613-1622`

**Interfaces:**
- Consumes: `static/world-cup-trophy.png` from Task 1.
- Produces: one `.wcb-trophy` decorative image in ready World Cup bracket HTML.

- [ ] **Step 1: Write the failing renderer assertions**

Add after `const html = context.rWCBracket();`:

```javascript
assert.strictEqual((html.match(/class="wcb-trophy"/g) || []).length, 1);
assert.match(html, /<img class="wcb-trophy" src="static\/world-cup-trophy\.png" alt="" aria-hidden="true">/);

context.WC_BRACKET_RUNTIME = { build: () => ({ ready: false }) };
assert.doesNotMatch(context.rWCBracket(), /wcb-trophy/);
```

- [ ] **Step 2: Run the test and verify failure**

Run: `node tests/test_wc_bracket_render.js`

Expected: FAIL because `wcb-trophy` is absent.

- [ ] **Step 3: Add minimal CSS and markup**

Add the trophy grid item styles beside the existing bracket CSS:

```css
.wcb-trophy{grid-column:5;grid-row:6;align-self:center;justify-self:center;width:52px;height:auto;z-index:2;pointer-events:none;filter:drop-shadow(0 0 8px rgba(255,197,61,.38))}
@media(max-width:700px){.wcb-trophy{width:46px}}
```

In `rWCBracket()`, after the final card and before the third-place card, append:

```javascript
h+=wcBracketPlace(model.final,5,3,3,'Final');
h+='<img class="wcb-trophy" src="static/world-cup-trophy.png" alt="" aria-hidden="true">';
h+=wcBracketPlace(model.third,5,7,2,'3rd Place');
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
node tests/test_wc_bracket_render.js
node tests/test_wc_bracket_runtime.js
python -m unittest tests.test_publish_contract
```

Expected: all commands PASS.

- [ ] **Step 5: Build generated pages and commit**

Run:

```powershell
python website/update_pl_mobile.py
git add tests/test_wc_bracket_render.js website/pl_mobile_template.html website/pl_mobile.html index.html
git commit -m "feat: add trophy to World Cup bracket"
```

Expected: generated pages contain the same trophy markup and CSS as the template.

### Task 3: Browser Verification And Publication

**Files:**
- Verify: `index.html`
- Verify: `static/world-cup-trophy.png`

**Interfaces:**
- Consumes: the generated site from Task 2.
- Produces: a visually verified and published GitHub Pages update.

- [ ] **Step 1: Run the complete automated suite**

Run:

```powershell
python -m unittest discover -s tests -p "test_*.py"
node tests/test_learning_runtime.js
node tests/test_season_runtime.js
node tests/test_wc_bracket_runtime.js
node tests/test_wc_bracket_render.js
```

Expected: all Python and Node tests PASS.

- [ ] **Step 2: Verify desktop and mobile in Playwright**

At 1440x900 and 390x844, open the World Cup Results bracket and assert:

```javascript
const trophy = page.locator('.wcb-trophy');
await expect(trophy).toHaveCount(1);
await expect(trophy).toBeVisible();
expect(await trophy.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
```

Also confirm the trophy does not intersect `.wcb-card`, the page has no horizontal overflow outside `.wcb-scroll`, the mobile initial position still centers the final, and resize/live re-render preserve bracket behavior.

- [ ] **Step 3: Publish and verify GitHub Pages**

Run:

```powershell
git push origin HEAD:main
```

Then open `https://moadi1987-eng.github.io/PL/`, wait for the published commit, and repeat the image-load and overlap checks at both viewports.

Expected: the public WC bracket displays one loaded trophy between the final and third-place cards; PL and La Liga display none.
