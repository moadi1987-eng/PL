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
assert.strictEqual((html.match(/class="wcb-trophy"/g) || []).length, 1);
assert.match(html, /<img class="wcb-trophy" src="static\/world-cup-trophy\.png" alt="" aria-hidden="true">/);
assert.strictEqual((html.match(/class="wcb-card/g) || []).length, 32);
assert.match(html, /Knockout Bracket/);
assert.match(html, /Round of 32/);
assert.match(html, /Final/);
assert.match(html, /3rd Place/);
assert.match(html, /67'/);
assert.match(html, /<div class="wcb-team placeholder"><span><\/span><span title="Semifinal 1 Winner">Semifinal 1 Winner<\/span><b><\/b><\/div>/);
assert.match(html, /wcb-team winner/);
assert.match(html, /wcb-card finished/);
assert.doesNotMatch(html, /undefined|NaN|Infinity/);

context.WC_BRACKET_RUNTIME = { build: () => ({ ready: false }) };
assert.doesNotMatch(context.rWCBracket(), /wcb-trophy/);

const resultBody = template.slice(template.indexOf('function rRes'), template.indexOf('/*', template.indexOf('function rRes')));
assert.match(resultBody, /rWCGroups\(\)/);
assert.match(resultBody, /rWCBracket\(\)/);
assert.match(resultBody, /wcBracketMount/);

console.log('wc bracket renderer tests passed');
