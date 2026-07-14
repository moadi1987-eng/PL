# World Cup Knockout Bracket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live, two-sided 2026 World Cup knockout bracket below the group tables on the Results tab, covering the Round of 32 through the final and third-place match.

**Architecture:** Put deterministic round partitioning and source-link resolution in a small browser runtime module with no DOM dependency. Inject that module into the generated single-file site, then let the existing Results renderer build a fixed-width HTML grid and draw decorative SVG connectors from the pure model. Existing live refreshes already update `D.fx`, so normal `render()` calls update the bracket without another network path.

**Tech Stack:** Vanilla JavaScript, HTML/CSS Grid, inline SVG connectors, Python static-site builder, Node `assert`/`vm` tests, Python `unittest`, Playwright with local Chrome.

## Global Constraints

- Render the bracket only for World Cup on the Results tab, below all Group A-L tables.
- Use the existing `D.fx` and `D.tm`; add no API call, backend, package, or persistent bracket state.
- Require the official 16/8/4/2/1/1 knockout fixture partition and never invent participants or results.
- Preserve unresolved placeholder names until a real team appears in live data.
- Use a fixed inner width between 1,180 and 1,260 pixels; mobile scrolls horizontally and initially centers the final.
- Preserve the user's bracket scroll position during later live renders.
- Keep page-level horizontal overflow disabled; only the bracket container may scroll horizontally.
- Preserve all PL, La Liga, group table, guessing, comparison, AI, and publication behavior.
- The third-place route is visually distinct from the championship route.
- Source changes and generated artifacts must be committed separately.

---

### Task 1: Pure Knockout Graph Runtime

**Files:**
- Create: `website/wc_bracket_runtime.js`
- Create: `tests/test_wc_bracket_runtime.js`

**Interfaces:**
- Consumes: fixture objects with `id`, `grp`, `ko`, `h`, and `a`; team objects keyed by id with `n` and `ph`.
- Produces: `WC_BRACKET_RUNTIME.build(fixtures, teams) -> BracketModel`.
- Produces: `BracketModel.ready`, `reason`, `count`, `rounds`, `sides`, and `links`.
- Produces: card refs shaped as `{key, round, index, match}`.
- Produces: links shaped as `{fromKey, toKey, type, confirmed, teamId, placeholder}`.

- [ ] **Step 1: Write the failing runtime test**

Create `tests/test_wc_bracket_runtime.js` with deterministic synthetic rounds and the validated 2026 source map:

```javascript
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const source = fs.readFileSync('website/wc_bracket_runtime.js', 'utf8');
const context = {};
vm.createContext(context);
vm.runInContext(source, context);
const runtime = context.WC_BRACKET_RUNTIME;

function match(id, h, a, day, extra = {}) {
  return { id, h, a, ko: `2026-07-${String(day).padStart(2, '0')}T19:00:00Z`, grp: null, ...extra };
}

const maps = {
  r32ToR16: [[0, 3], [2, 5], [1, 4], [6, 7], [11, 10], [9, 8], [14, 13], [12, 15]],
  r16ToQf: [[1, 0], [4, 5], [2, 3], [6, 7]],
  qfToSf: [[0, 1], [2, 3]],
};
const teams = {};
const r32 = Array.from({ length: 16 }, (_, index) => {
  const home = 1000 + index * 2;
  const away = home + 1;
  teams[home] = { id: home, n: `Home ${index}`, s: `H${index}`, ph: false };
  teams[away] = { id: away, n: `Away ${index}`, s: `A${index}`, ph: false };
  return match(100 + index, home, away, 1 + index, { fin: true, hs: index === 2 ? 1 : 2, as: index === 2 ? 1 : 0 });
});
const r16 = maps.r32ToR16.map((pair, index) => match(
  200 + index,
  r32[pair[0]].h,
  index === 1 ? r32[pair[1]].a : r32[pair[1]].h,
  17 + index,
  { fin: true, hs: 2, as: 0 },
));
const qf = maps.r16ToQf.map((pair, index) => match(300 + index, r16[pair[0]].h, r16[pair[1]].h, 25 + index, { fin: true, hs: 1, as: 0 }));
const sf = maps.qfToSf.map((pair, index) => match(400 + index, qf[pair[0]].h, qf[pair[1]].h, 29 + index));
teams[9001] = { id: 9001, n: 'Semifinal 1 Loser', s: 'SF L1', ph: true };
teams[9002] = { id: 9002, n: 'Semifinal 2 Loser', s: 'SF L2', ph: true };
teams[9003] = { id: 9003, n: 'Semifinal 1 Winner', s: 'SFW1', ph: true };
teams[9004] = { id: 9004, n: 'Semifinal 2 Winner', s: 'SFW2', ph: true };
const third = match(500, 9001, 9002, 31);
const final = match(501, 9003, 9004, 32);
const fixtures = [...r32, ...r16, ...qf, ...sf, third, final];

const model = runtime.build(fixtures, teams);
assert.strictEqual(model.ready, true);
assert.deepStrictEqual(
  JSON.parse(JSON.stringify({ r32: model.rounds.r32.length, r16: model.rounds.r16.length, qf: model.rounds.qf.length, sf: model.rounds.sf.length })),
  { r32: 16, r16: 8, qf: 4, sf: 2 },
);
assert.strictEqual(model.links.length, 32);
assert.strictEqual(model.sides.left.r32.length, 8);
assert.strictEqual(model.sides.right.r32.length, 8);
assert.strictEqual(new Set([...model.sides.left.r32, ...model.sides.right.r32].map((ref) => ref.key)).size, 16);

const tiedSourceLink = model.links.find((link) => link.fromKey === 'r32-2' && link.toKey === 'r16-1');
assert.strictEqual(tiedSourceLink.confirmed, true);
assert.strictEqual(tiedSourceLink.teamId, r32[2].h);
assert.strictEqual(model.links.filter((link) => link.type === 'loser').length, 2);
assert.strictEqual(model.links.filter((link) => link.placeholder).length, 4);

const incomplete = runtime.build(fixtures.slice(0, 31), teams);
assert.strictEqual(incomplete.ready, false);
assert.strictEqual(incomplete.reason, 'expected-32-knockout-fixtures');

const duplicate = fixtures.slice();
duplicate[31] = { ...duplicate[31], id: duplicate[30].id };
assert.strictEqual(runtime.build(duplicate, teams).reason, 'duplicate-knockout-fixture-id');

console.log('wc bracket runtime tests passed');
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```powershell
node tests\test_wc_bracket_runtime.js
```

Expected: FAIL because `website/wc_bracket_runtime.js` does not exist.

- [ ] **Step 3: Implement the pure runtime**

Create `website/wc_bracket_runtime.js` with the validated maps, deterministic partitioning, mirrored side order, and evidence-aware link states:

```javascript
(function(root){
  'use strict';
  var MAPS={
    r32ToR16:[[0,3],[2,5],[1,4],[6,7],[11,10],[9,8],[14,13],[12,15]],
    r16ToQf:[[1,0],[4,5],[2,3],[6,7]],
    qfToSf:[[0,1],[2,3]]
  };
  var SIDE_ORDER={
    left:{r32:[2,5,0,3,11,10,9,8],r16:[1,0,4,5],qf:[0,1],sf:[0]},
    right:{r32:[1,4,6,7,14,13,12,15],r16:[2,3,6,7],qf:[2,3],sf:[1]}
  };
  var ROUND_WORDS={'round of 32':'r32','round of 16':'r16','quarterfinal':'qf','semifinal':'sf'};

  function fixtureSort(a,b){
    var ak=new Date(a&&a.ko||'').getTime(),bk=new Date(b&&b.ko||'').getTime();
    if(!isFinite(ak))ak=Number.MAX_SAFE_INTEGER;
    if(!isFinite(bk))bk=Number.MAX_SAFE_INTEGER;
    return ak-bk||(+a.id||0)-(+b.id||0);
  }
  function teamFor(teams,id){return teams&&(teams[id]||teams[String(id)])||{}}
  function ref(round,index,match){return{key:round+'-'+index,round:round,index:index,match:match}}
  function refs(rounds,round,indexes){return indexes.map(function(index){return ref(round,index,rounds[round][index])})}
  function participant(match,id){return!!(match&&(+match.h===+id||+match.a===+id))}
  function placeholderMeta(team){
    var m=String(team&&team.n||'').match(/^(Round of 32|Round of 16|Quarterfinal|Semifinal)\s+(\d+)\s+(Winner|Loser)$/i);
    if(!m)return null;
    return{round:ROUND_WORDS[m[1].toLowerCase()],index:+m[2]-1,type:m[3].toLowerCase()};
  }
  function partition(fixtures){
    var knockout=(fixtures||[]).filter(function(match){return match&&!match.grp}).slice().sort(fixtureSort);
    if(knockout.length!==32)return{ready:false,reason:'expected-32-knockout-fixtures',count:knockout.length};
    var ids={};
    for(var i=0;i<knockout.length;i++){
      var id=String(knockout[i].id);
      if(ids[id])return{ready:false,reason:'duplicate-knockout-fixture-id',count:knockout.length};
      ids[id]=true;
    }
    return{ready:true,count:32,r32:knockout.slice(0,16),r16:knockout.slice(16,24),qf:knockout.slice(24,28),sf:knockout.slice(28,30),third:knockout[30],final:knockout[31]};
  }
  function link(fromRound,fromIndex,toRound,toIndex,type){
    return{fromRound:fromRound,fromIndex:fromIndex,fromKey:fromRound+'-'+fromIndex,toRound:toRound,toIndex:toIndex,toKey:toRound+'-'+toIndex,type:type||'winner'};
  }
  function mappedLinks(){
    var out=[];
    function add(fromRound,toRound,pairs){pairs.forEach(function(pair,toIndex){pair.forEach(function(fromIndex){out.push(link(fromRound,fromIndex,toRound,toIndex,'winner'))})})}
    add('r32','r16',MAPS.r32ToR16);
    add('r16','qf',MAPS.r16ToQf);
    add('qf','sf',MAPS.qfToSf);
    out.push(link('sf',0,'final',0,'winner'),link('sf',1,'final',0,'winner'));
    out.push(link('sf',0,'third',0,'loser'),link('sf',1,'third',0,'loser'));
    return out;
  }
  function matchFor(rounds,round,index){
    if(round==='final'||round==='third')return rounds[round];
    return rounds[round]&&rounds[round][index];
  }
  function resolveLink(raw,rounds,teams){
    var source=matchFor(rounds,raw.fromRound,raw.fromIndex),destination=matchFor(rounds,raw.toRound,raw.toIndex);
    var ids=destination?[destination.h,destination.a]:[],teamId=null,placeholder=false;
    ids.forEach(function(id){
      if(teamId!=null)return;
      if(participant(source,id)){teamId=+id;return}
      var meta=placeholderMeta(teamFor(teams,id));
      if(meta&&meta.round===raw.fromRound&&meta.index===raw.fromIndex&&meta.type===raw.type)placeholder=true;
    });
    return{fromKey:raw.fromKey,toKey:raw.toKey,type:raw.type,confirmed:teamId!=null,teamId:teamId,placeholder:placeholder};
  }
  function build(fixtures,teams){
    var rounds=partition(fixtures);
    if(!rounds.ready)return rounds;
    var links=mappedLinks().map(function(item){return resolveLink(item,rounds,teams)});
    return{
      ready:true,count:32,rounds:rounds,links:links,
      sides:{
        left:{r32:refs(rounds,'r32',SIDE_ORDER.left.r32),r16:refs(rounds,'r16',SIDE_ORDER.left.r16),qf:refs(rounds,'qf',SIDE_ORDER.left.qf),sf:refs(rounds,'sf',SIDE_ORDER.left.sf)},
        right:{sf:refs(rounds,'sf',SIDE_ORDER.right.sf),qf:refs(rounds,'qf',SIDE_ORDER.right.qf),r16:refs(rounds,'r16',SIDE_ORDER.right.r16),r32:refs(rounds,'r32',SIDE_ORDER.right.r32)}
      },
      final:ref('final',0,rounds.final),third:ref('third',0,rounds.third)
    };
  }
  root.WC_BRACKET_RUNTIME={build:build,partition:partition,maps:MAPS};
})(typeof globalThis!=='undefined'?globalThis:this);
```

- [ ] **Step 4: Run the runtime test to verify GREEN**

Run:

```powershell
node tests\test_wc_bracket_runtime.js
```

Expected: `wc bracket runtime tests passed`.

- [ ] **Step 5: Run existing JavaScript tests**

Run:

```powershell
node tests\test_season_runtime.js
node tests\test_learning_runtime.js
```

Expected: both commands exit 0 with their existing success messages.

- [ ] **Step 6: Commit the pure runtime**

```powershell
git add website/wc_bracket_runtime.js tests/test_wc_bracket_runtime.js
git commit -m "feat: model World Cup knockout bracket"
```

---

### Task 2: Static Builder Runtime Injection

**Files:**
- Modify: `website/pl_mobile_template.html:224-240`
- Modify: `website/update_pl_mobile.py:2251-2268`
- Modify: `tests/test_publish_contract.py:955-967`

**Interfaces:**
- Consumes: `website/wc_bracket_runtime.js` from Task 1.
- Produces: exactly one `WC_BRACKET_RUNTIME` definition in generated `index.html` and `website/pl_mobile.html`, before `init()`.

- [ ] **Step 1: Write the failing publication contract test**

Add this test to `PublishContractTests` in `tests/test_publish_contract.py`:

```python
def test_wc_bracket_runtime_is_embedded_once_after_data_and_before_init(self):
    template = (ROOT / "website" / "pl_mobile_template.html").read_text(encoding="utf-8")
    runtime = (ROOT / "website" / "wc_bracket_runtime.js").read_text(encoding="utf-8")

    self.assertEqual(template.count("/*__WC_BRACKET_RUNTIME__*/"), 1)
    self.assertEqual(self.source.count("wc_bracket_runtime.js"), 1)
    self.assertIn(
        'html.replace("/*__WC_BRACKET_RUNTIME__*/", wc_bracket_runtime)',
        self.source,
    )
    self.assertNotIn("/*__WC_BRACKET_RUNTIME__*/", runtime)
    generated = template.replace("/*__WC_BRACKET_RUNTIME__*/", runtime)
    self.assertNotIn("/*__WC_BRACKET_RUNTIME__*/", generated)
    self.assertEqual(generated.count("root.WC_BRACKET_RUNTIME="), 1)
    self.assertLess(generated.index("WC_BRACKET_RUNTIME"), generated.index("function init()"))
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```powershell
python -m unittest tests.test_publish_contract.PublishContractTests.test_wc_bracket_runtime_is_embedded_once_after_data_and_before_init -v
```

Expected: FAIL because the template marker and builder replacement do not exist.

- [ ] **Step 3: Add the template marker and builder replacement**

Add the marker immediately after `/*__SEASON_RUNTIME__*/` in `website/pl_mobile_template.html`:

```html
/*__SEASON_RUNTIME__*/
/*__WC_BRACKET_RUNTIME__*/
```

Read and embed the runtime beside the existing season runtime in `website/update_pl_mobile.py`:

```python
season_runtime = open(os.path.join(HERE, "season_runtime.js"), "r", encoding="utf-8").read()
wc_bracket_runtime = open(
    os.path.join(HERE, "wc_bracket_runtime.js"), "r", encoding="utf-8"
).read()
# Existing data replacements remain unchanged.
html = html.replace("/*__SEASON_RUNTIME__*/", season_runtime)
html = html.replace("/*__WC_BRACKET_RUNTIME__*/", wc_bracket_runtime)
```

- [ ] **Step 4: Run the focused and full publication tests**

Run:

```powershell
python -m unittest tests.test_publish_contract.PublishContractTests.test_wc_bracket_runtime_is_embedded_once_after_data_and_before_init -v
python -m unittest tests.test_publish_contract -q
```

Expected: focused test passes; the full module exits 0.

- [ ] **Step 5: Commit runtime injection**

```powershell
git add website/pl_mobile_template.html website/update_pl_mobile.py tests/test_publish_contract.py
git commit -m "build: embed World Cup bracket runtime"
```

---

### Task 3: Bracket Cards And Results Integration

**Files:**
- Modify: `website/pl_mobile_template.html:35-75`
- Modify: `website/pl_mobile_template.html:1525-1590`
- Create: `tests/test_wc_bracket_render.js`

**Interfaces:**
- Consumes: `WC_BRACKET_RUNTIME.build(D.fx, D.tm)` from Task 1.
- Produces: `rWCBracket() -> string` containing exactly 32 `.wcb-card` elements for a ready model.
- Produces: `wcBracketMount(savedScroll)` and `drawWCBracketLines()` for Task 4.
- Integrates: `rRes()` appends `rWCGroups()` and `rWCBracket()` for World Cup only.

- [ ] **Step 1: Write the failing renderer test**

Create `tests/test_wc_bracket_render.js`. Extract only the bracket renderer block from the template, provide a ready model and browser-helper stubs, and assert real card output:

```javascript
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const template = fs.readFileSync('website/pl_mobile_template.html', 'utf8');
const start = template.indexOf('var _wcBracketModel');
const end = template.indexOf('\nfunction rRes', start);
assert.notStrictEqual(start, -1, 'bracket renderer start is missing');
assert.notStrictEqual(end, -1, 'bracket renderer end is missing');

function ref(round, index, extra = {}) {
  return { key: `${round}-${index}`, round, index, match: { id: `${round}-${index}`, h: 1, a: 2, hs: null, as: null, st: false, fin: false, ko: '2026-07-19T19:00:00Z', ...extra } };
}
const model = {
  ready: true,
  links: [{ fromKey: 'r32-0', toKey: 'r16-0', type: 'winner', confirmed: true, teamId: 1 }],
  sides: {
    left: { r32: Array.from({ length: 8 }, (_, i) => ref('r32', i)), r16: Array.from({ length: 4 }, (_, i) => ref('r16', i)), qf: Array.from({ length: 2 }, (_, i) => ref('qf', i)), sf: [ref('sf', 0, { st: true, hs: 1, as: 0 })] },
    right: { sf: [ref('sf', 1)], qf: Array.from({ length: 2 }, (_, i) => ref('qf', i + 2)), r16: Array.from({ length: 4 }, (_, i) => ref('r16', i + 4)), r32: Array.from({ length: 8 }, (_, i) => ref('r32', i + 8)) },
  },
  final: ref('final', 0, { h: 3, a: 4 }),
  third: ref('third', 0, { h: 5, a: 6, fin: true, hs: 2, as: 1 }),
};
const teams = {
  1: { id: 1, n: 'France', s: 'FRA', b: 'fra.png' },
  2: { id: 2, n: 'Spain', s: 'ESP', b: 'esp.png' },
  3: { id: 3, n: 'Semifinal 1 Winner', s: 'SFW1', ph: true, b: '' },
  4: { id: 4, n: 'Semifinal 2 Winner', s: 'SFW2', ph: true, b: '' },
  5: { id: 5, n: 'Team Five', s: 'T5', b: '' },
  6: { id: 6, n: 'Team Six', s: 'T6', b: '' },
};
const context = {
  D: { league: 'wc', fx: [], tm: teams },
  T: (id) => teams[id] || teams[1],
  fKO: () => 'Sun 19 Jul, 22:00',
  liveText: () => '67\'',
  WC_BRACKET_RUNTIME: { build: () => model },
  requestAnimationFrame: () => {},
};
vm.createContext(context);
vm.runInContext(template.slice(start, end), context);
const html = context.rWCBracket();
assert.strictEqual((html.match(/class="wcb-card/g) || []).length, 32);
assert.match(html, /Knockout Bracket/);
assert.match(html, /Round of 32/);
assert.match(html, /Final/);
assert.match(html, /3rd Place/);
assert.match(html, /67'/);
assert.match(html, /Semifinal 1 Winner/);
assert.match(html, /wcb-team winner/);
assert.match(html, /wcb-card finished/);
assert.doesNotMatch(html, /undefined|NaN|Infinity/);

const resultBody = template.slice(template.indexOf('function rRes'), template.indexOf('/*', template.indexOf('function rRes')));
assert.match(resultBody, /rWCGroups\(\)/);
assert.match(resultBody, /rWCBracket\(\)/);
assert.match(resultBody, /wcBracketMount/);

console.log('wc bracket renderer tests passed');
```

- [ ] **Step 2: Run the renderer test to verify RED**

Run:

```powershell
node tests\test_wc_bracket_render.js
```

Expected: FAIL with `bracket renderer start is missing`.

- [ ] **Step 3: Add stable bracket CSS**

Add this focused CSS block near the existing Results/table styles in `website/pl_mobile_template.html`:

```css
.wcb-scroll{position:relative;overflow-x:auto;overflow-y:hidden;overscroll-behavior-x:contain;margin:.3rem 0 4.5rem;padding:.2rem 0 .55rem;max-width:100%;border:1px solid var(--bd);border-radius:6px;background:var(--dk)}
.wcb-canvas{position:relative;width:1240px;min-width:1240px;height:680px;padding:.45rem;box-sizing:border-box}
.wcb-lines{position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}
.wcb-line{fill:none;stroke:rgba(255,255,255,.18);stroke-width:2;vector-effect:non-scaling-stroke}
.wcb-line.confirmed{stroke:var(--g);stroke-width:2.5}.wcb-line.loser{stroke:var(--cy);stroke-dasharray:5 5}
.wcb-grid{position:relative;z-index:1;display:grid;grid-template-columns:repeat(4,125px) 140px repeat(4,125px);grid-template-rows:28px repeat(8,70px);column-gap:10px;row-gap:7px;height:100%}
.wcb-head{align-self:center;text-align:center;color:var(--tm);font-size:.5rem;font-weight:800;text-transform:uppercase;white-space:nowrap}
.wcb-slot{align-self:center;min-width:0}.wcb-card{height:58px;min-width:0;background:var(--cd);border:1px solid var(--bd);border-radius:6px;padding:.25rem .3rem;box-sizing:border-box;overflow:hidden}
.wcb-card.live{border-color:var(--pk);box-shadow:inset 3px 0 var(--pk)}.wcb-card.finished{border-color:rgba(42,223,127,.35)}
.wcb-stage{display:flex;justify-content:space-between;gap:.25rem;color:var(--tm);font-size:.42rem;line-height:1;margin-bottom:.2rem}
.wcb-team{display:grid;grid-template-columns:15px minmax(0,1fr) auto;align-items:center;gap:.22rem;height:19px;font-size:.52rem;font-weight:700;color:var(--tx)}
.wcb-team img{width:15px;height:15px;object-fit:contain}.wcb-team span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.wcb-team b{font-size:.58rem}
.wcb-team.winner{color:var(--g)}.wcb-team.placeholder{color:var(--tm);font-weight:600}
.wcb-empty{padding:.75rem;border:1px solid var(--bd);border-radius:6px;color:var(--tm);text-align:center;font-size:.62rem}
@media(max-width:700px){.wcb-scroll{margin-left:0;margin-right:0}.wcb-canvas{height:660px}.wcb-card{height:56px}}
```

- [ ] **Step 4: Add card and grid rendering**

Add a bracket renderer block immediately before `rRes()`. Keep these exact globals so scroll and SVG mounting can be added without changing interfaces:

```javascript
var _wcBracketModel=null,_wcBracketScrollLeft=null,_wcBracketCentered=false,_wcBracketResizeBound=false;
function wcBracketEsc(value){return String(value==null?'':value).replace(/[&<>"']/g,function(ch){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]})}
function wcBracketWinner(model,key,match){
  for(var i=0;i<model.links.length;i++){var link=model.links[i];if(link.fromKey===key&&link.type==='winner'&&link.confirmed)return link.teamId}
  if(match&&match.fin&&match.hs!=null&&match.as!=null&&match.hs!==match.as)return match.hs>match.as?match.h:match.a;
  return null;
}
function wcBracketCard(ref,label){
  var model=_wcBracketModel,m=ref.match||{},home=T(m.h),away=T(m.a),winner=wcBracketWinner(model,ref.key,m);
  var live=m.st&&!m.fin,status=live?liveText(m.ko,m.mn,m.sx||''):m.fin?'FT':fKO(m.ko||'');
  function row(team,id,score){
    var cls='wcb-team'+(winner===id?' winner':'')+(team.ph?' placeholder':'');
    var img=team.b?'<img src="'+wcBracketEsc(team.b)+'" alt="'+wcBracketEsc(team.n||team.s||'Team')+'" onerror="this.style.display=\'none\'">':'<span></span>';
    return'<div class="'+cls+'">'+img+'<span title="'+wcBracketEsc(team.n||team.s||'TBD')+'">'+wcBracketEsc(team.s||team.n||'TBD')+'</span><b>'+(score==null?'':score)+'</b></div>';
  }
  return'<div class="wcb-card'+(live?' live':'')+(m.fin?' finished':'')+'" data-bracket-key="'+ref.key+'"><div class="wcb-stage"><span>'+label+'</span><span>'+wcBracketEsc(status)+'</span></div>'+row(home,m.h,m.hs)+row(away,m.a,m.as)+'</div>';
}
function wcBracketPlace(ref,column,row,span,label){return'<div class="wcb-slot" style="grid-column:'+column+';grid-row:'+row+' / span '+span+'">'+wcBracketCard(ref,label)+'</div>'}
function wcBracketRound(refs,column,span,label){var h='';refs.forEach(function(ref,index){h+=wcBracketPlace(ref,column,2+index*span,span,label)});return h}
function rWCBracket(){
  var model=WC_BRACKET_RUNTIME.build(D.fx,D.tm);_wcBracketModel=model;
  if(!model.ready)return'<div class="sec" style="margin-top:.7rem">Knockout Bracket</div><div class="wcb-empty">Knockout bracket will appear when all official fixtures are available.</div>';
  var h='<div class="sec" style="margin-top:.7rem">Knockout Bracket</div><div class="wcb-scroll" tabindex="0" aria-label="World Cup knockout bracket"><div class="wcb-canvas"><svg class="wcb-lines" aria-hidden="true"></svg><div class="wcb-grid">';
  var heads=[[1,'Round of 32'],[2,'Round of 16'],[3,'Quarterfinal'],[4,'Semifinal'],[5,'Final'],[6,'Semifinal'],[7,'Quarterfinal'],[8,'Round of 16'],[9,'Round of 32']];
  heads.forEach(function(item){h+='<div class="wcb-head" style="grid-column:'+item[0]+';grid-row:1">'+item[1]+'</div>'});
  h+=wcBracketRound(model.sides.left.r32,1,1,'R32')+wcBracketRound(model.sides.left.r16,2,2,'R16')+wcBracketRound(model.sides.left.qf,3,4,'QF')+wcBracketRound(model.sides.left.sf,4,8,'SF');
  h+=wcBracketPlace(model.final,5,3,3,'Final')+wcBracketPlace(model.third,5,7,2,'3rd Place');
  h+=wcBracketRound(model.sides.right.sf,6,8,'SF')+wcBracketRound(model.sides.right.qf,7,4,'QF')+wcBracketRound(model.sides.right.r16,8,2,'R16')+wcBracketRound(model.sides.right.r32,9,1,'R32');
  return h+'</div></div></div>';
}
```

- [ ] **Step 5: Integrate the bracket into World Cup Results**

Refactor only `rRes()` enough to preserve the old bracket scroll before replacing markup, append the group tables and bracket for World Cup, and queue mounting after assignment:

```javascript
function rRes(){
  var c=$('t0'),ms=gFix(),savedScroll=wcBracketCaptureScroll(),h='<div class="sec">Results - '+(D.gwl||'GW')+D.sel+'</div>';
  if(!ms.length)h+='<div class="emp">No fixtures</div>';
  ms.forEach(function(m){
    var hm=T(m.h),am=T(m.a),hs=m.hs!=null?m.hs:'-',as=m.as!=null?m.as:'-',isLive=m.st&&!m.fin;
    var tag=isLive?'<span class="labs">'+liveText(m.ko,m.mn,m.sx||'')+'</span>':m.fin?'<span class="fabs">FT</span>':'';
    var info=!m.st&&!m.fin&&m.ko?'<div class="mko">'+fKO(m.ko)+'</div>':'';
    var ph=D.league==='wc'?wcPhase(m):null,phTag=ph?'<div class="phase">'+ph.label+'</div>':'';
    h+='<div class="mc'+(isLive?' live':'')+'">'+tag+'<div class="mr"><div class="mt"><img src="'+hm.b+'" onerror="this.style.display=\'none\'"><span>'+hm.s+'</span></div><div class="msc">'+hs+' - '+as+'</div><div class="mt aw"><img src="'+am.b+'" onerror="this.style.display=\'none\'"><span>'+am.s+'</span></div></div>'+info+phTag+'</div>';
  });
  if(D.league==='wc'){
    h+=rWCGroups()+rWCBracket();c.innerHTML=h;wcBracketMount(savedScroll);return;
  }
  if(!ms.length){c.innerHTML=h;return}
  h+='<div class="sec" style="margin-top:.6rem">League Table</div><div class="tw"><table class="tbl"><thead><tr><th>#</th><th>Team</th><th>P</th><th>W</th><th>D</th><th>L</th><th>GF</th><th>GA</th><th>GD</th><th>Pts</th><th>Form</th></tr></thead><tbody>';
  D.st.forEach(function(r,i){var p=i+1,pc=p<=1?'pch':p<=4?'pcl':p>=18?'prl':'',fm='';r.fm.forEach(function(f){fm+='<span class="fd '+f+'">'+f+'</span>'});h+='<tr><td class="'+pc+'">'+p+'</td><td><div class="tc"><img src="'+r.t.b+'" onerror="this.style.display=\'none\'"><span>'+r.t.s+'</span></div></td><td>'+r.p+'</td><td>'+r.w+'</td><td>'+r.d+'</td><td>'+r.l+'</td><td>'+r.gf+'</td><td>'+r.ga+'</td><td>'+r.gd+'</td><td class="pts">'+r.pts+'</td><td>'+fm+'</td></tr>'});
  h+='</tbody></table></div>';c.innerHTML=h;
}
```

At this step, define `wcBracketCaptureScroll()` and `wcBracketMount()` as safe no-op-compatible functions so the renderer test can execute; Task 4 fills in connector behavior:

```javascript
function wcBracketCaptureScroll(){var el=document.querySelector('.wcb-scroll');return el?el.scrollLeft:null}
function wcBracketMount(savedScroll){requestAnimationFrame(function(){var el=document.querySelector('.wcb-scroll');if(el&&savedScroll!=null)el.scrollLeft=savedScroll})}
function drawWCBracketLines(){}
```

- [ ] **Step 6: Run renderer and existing tests**

Run:

```powershell
node tests\test_wc_bracket_render.js
node tests\test_wc_bracket_runtime.js
node tests\test_season_runtime.js
node tests\test_learning_runtime.js
python -m unittest discover -s tests -q
```

Expected: all commands exit 0; Python reports the full suite as `OK`.

- [ ] **Step 7: Commit bracket markup**

```powershell
git add website/pl_mobile_template.html tests/test_wc_bracket_render.js
git commit -m "feat: render World Cup knockout bracket"
```

---

### Task 4: Connectors, Mobile Scroll, And Browser Verification

**Files:**
- Modify: `website/pl_mobile_template.html` bracket mount block from Task 3
- Create: `.superpowers/sdd/wc-bracket-visual-check.js`

**Interfaces:**
- Consumes: `.wcb-card[data-bracket-key]`, `_wcBracketModel.links`, and `.wcb-scroll` from Task 3.
- Produces: one SVG path per graph link, confirmed and loser route classes, first-render mobile centering, and scroll preservation.

- [ ] **Step 1: Write the failing browser verifier**

Create `.superpowers/sdd/wc-bracket-visual-check.js` with desktop and mobile checks:

```javascript
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
```

- [ ] **Step 2: Run the verifier to confirm RED**

Build the Task 3 source locally without publishing, start a local server from the repository root, and run the verifier against that generated page. Before Task 4 implementation, expect FAIL because `.wcb-line` paths and initial centering are missing. Leave generated files uncommitted until Task 5.

```powershell
$env:PUBLISH_TO_GITHUB='0'
Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
python website\update_pl_mobile.py
python -m http.server 8765 --bind 127.0.0.1
node .superpowers\sdd\wc-bracket-visual-check.js http://127.0.0.1:8765/index.html .superpowers\sdd
```

- [ ] **Step 3: Implement SVG connectors and preserved scrolling**

Replace the Task 3 mount stubs with complete connector logic:

```javascript
function wcBracketCaptureScroll(){var el=document.querySelector('.wcb-scroll');return el?el.scrollLeft:_wcBracketScrollLeft}
function drawWCBracketLines(){
  var canvas=document.querySelector('.wcb-canvas'),svg=canvas&&canvas.querySelector('.wcb-lines');
  if(!canvas||!svg||!_wcBracketModel||!_wcBracketModel.ready)return;
  var box=canvas.getBoundingClientRect(),paths='';
  _wcBracketModel.links.forEach(function(link){
    var from=canvas.querySelector('[data-bracket-key="'+link.fromKey+'"]'),to=canvas.querySelector('[data-bracket-key="'+link.toKey+'"]');
    if(!from||!to)return;
    var a=from.getBoundingClientRect(),b=to.getBoundingClientRect(),leftToRight=a.left<b.left;
    var x1=(leftToRight?a.right:a.left)-box.left,x2=(leftToRight?b.left:b.right)-box.left,y1=a.top+a.height/2-box.top,y2=b.top+b.height/2-box.top,mid=(x1+x2)/2;
    var cls='wcb-line'+(link.confirmed?' confirmed':'')+(link.type==='loser'?' loser':'');
    paths+='<path class="'+cls+'" d="M '+x1+' '+y1+' H '+mid+' V '+y2+' H '+x2+'"></path>';
  });
  svg.setAttribute('viewBox','0 0 '+canvas.clientWidth+' '+canvas.clientHeight);svg.innerHTML=paths;
}
function wcBracketMount(savedScroll){
  requestAnimationFrame(function(){
    var el=document.querySelector('.wcb-scroll');if(!el)return;
    if(savedScroll!=null)el.scrollLeft=savedScroll;
    else if(!_wcBracketCentered&&el.scrollWidth>el.clientWidth){el.scrollLeft=(el.scrollWidth-el.clientWidth)/2;_wcBracketCentered=true}
    _wcBracketScrollLeft=el.scrollLeft;
    el.addEventListener('scroll',function(){_wcBracketScrollLeft=el.scrollLeft},{passive:true});
    drawWCBracketLines();
    if(!_wcBracketResizeBound){_wcBracketResizeBound=true;window.addEventListener('resize',function(){if(document.querySelector('.wcb-canvas'))requestAnimationFrame(drawWCBracketLines)},{passive:true})}
  });
}
```

- [ ] **Step 4: Build locally without publishing**

Run:

```powershell
$env:PUBLISH_TO_GITHUB='0'
Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
python website\update_pl_mobile.py
```

Expected: build exits 0, prints both generated HTML paths, and prints `GitHub upload disabled`.

- [ ] **Step 5: Run browser verification and inspect screenshots**

Run the local server and Playwright verifier with the bundled Node dependency path and Chrome executable. Inspect both generated screenshots manually with `view_image`:

```powershell
$env:NODE_PATH='C:\Users\amirmoa\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules;C:\Users\amirmoa\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\.pnpm\node_modules'
$env:BROWSER_EXECUTABLE='C:\Program Files\Google\Chrome\Application\chrome.exe'
node .superpowers\sdd\wc-bracket-visual-check.js http://127.0.0.1:8765/index.html .superpowers\sdd
```

Expected: JSON reports 32 cards, 32 paths, 2 loser paths, no overlap, and no page overflow for both viewports.

- [ ] **Step 6: Run the full source verification suite**

```powershell
python -m unittest discover -s tests -q
node tests\test_wc_bracket_runtime.js
node tests\test_wc_bracket_render.js
node tests\test_season_runtime.js
node tests\test_learning_runtime.js
git diff --check
```

Expected: all commands exit 0; Python reports `OK`; `git diff --check` prints nothing.

- [ ] **Step 7: Commit connector and browser verification source**

Stage only source and verification files, not generated artifacts:

```powershell
git add website/pl_mobile_template.html .superpowers/sdd/wc-bracket-visual-check.js
git commit -m "feat: connect responsive World Cup bracket"
```

---

### Task 5: Generated Artifacts, Review, And Publication

**Files:**
- Modify generated: `index.html`
- Modify generated: `website/pl_mobile.html`
- Modify generated if refreshed: `live.json`, `learning_history.json`, `ai_predictions*.json`, `ai_weights*.json`

**Interfaces:**
- Consumes: all verified source commits from Tasks 1-4.
- Produces: the published static dashboard on `main` with source and generated HTML synchronized.

- [ ] **Step 1: Check remote freshness before final generation**

```powershell
git fetch origin main
git log --oneline HEAD..origin/main
```

Expected: no output from the second command. If `main` advanced, rebase source commits before regenerating artifacts.

- [ ] **Step 2: Rebuild once from the final source**

```powershell
$env:PUBLISH_TO_GITHUB='0'
Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
python website\update_pl_mobile.py
```

Expected: 380 fixtures for each La Liga season, 104 World Cup fixtures, learning state embedded, and no automatic upload.

- [ ] **Step 3: Validate the generated publication contract**

Verify:

```powershell
$index=(Get-FileHash index.html -Algorithm SHA256).Hash
$mobile=(Get-FileHash website\pl_mobile.html -Algorithm SHA256).Hash
if($index-ne$mobile){throw 'Generated HTML mismatch'}
$html=Get-Content -Raw index.html
if(-not $html.Contains('root.WC_BRACKET_RUNTIME=')){throw 'Bracket runtime missing'}
if(-not $html.Contains('Knockout Bracket')){throw 'Bracket markup missing'}
git status --short
```

Expected: hashes match; status contains source commits already recorded plus only the normal generated publication paths.

- [ ] **Step 4: Run fresh final tests and browser checks**

Repeat the full suite and Playwright verifier from Task 4 after the final build. Stop the exact local server process afterward and verify port 8765 is free.

- [ ] **Step 5: Request final code review**

Dispatch a fresh read-only reviewer over `origin/main..HEAD`. Require findings ordered by severity and explicit checks for:

- Correct 16/8/4/2/1/1 partition and fallback map.
- No invented teams for unresolved slots.
- Tied knockout winner path resolution from later participation.
- Live render and scroll preservation.
- No PL/La Liga regression.
- Generated HTML equality and publication safety.

Fix every Critical or Important finding, rerun affected tests, rebuild, and request re-review.

- [ ] **Step 6: Commit only generated artifacts**

```powershell
git add -- ai_predictions.json ai_predictions_laliga.json ai_predictions_wc.json ai_weights.json ai_weights_laliga.json ai_weights_wc.json index.html learning_history.json live.json website/pl_mobile.html
git commit -m "build: publish World Cup knockout bracket"
```

Expected: the commit contains generated files only.

- [ ] **Step 7: Push the verified HEAD directly to main**

```powershell
git fetch origin main
git push origin HEAD:main
$local=(git rev-parse HEAD).Trim()
$remote=((git ls-remote origin refs/heads/main)-split '\s+')[0]
if($local-ne$remote){throw 'Remote main does not match local HEAD'}
git status --short
```

Expected: push succeeds, local and remote SHA values match, and worktree status is empty.

- [ ] **Step 8: Verify the public GitHub Pages output**

Fetch `https://moadi1987-eng.github.io/PL/` with a cache-busting query and confirm the response includes both `root.WC_BRACKET_RUNTIME=` and `Knockout Bracket`. Then run one public-page mobile Playwright check for 32 cards and no page overflow.
