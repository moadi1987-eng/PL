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

const inconsistentCases = [
  {
    name: 'unmatched destination participant',
    fixtures: fixtures.map((fixture, index) => index === 16 ? { ...fixture, h: 99999 } : fixture),
    teams,
  },
  {
    name: 'duplicate source and slot evidence',
    fixtures: fixtures.map((fixture, index) => index === 16 ? { ...fixture, a: fixture.h } : fixture),
    teams,
  },
  {
    name: 'malformed placeholder source label',
    fixtures,
    teams: { ...teams, 9003: { ...teams[9003], n: 'Semifinal One Winner' } },
  },
];
const inconsistentFailures = [];
inconsistentCases.forEach(({ name, fixtures: caseFixtures, teams: caseTeams }) => {
  try {
    const result = runtime.build(caseFixtures, caseTeams);
    assert.deepStrictEqual(
      JSON.parse(JSON.stringify({ ready: result.ready, reason: result.reason })),
      { ready: false, reason: 'inconsistent-knockout-link-graph' },
    );
  } catch (error) {
    inconsistentFailures.push(`${name}: ${error.message}`);
  }
});
assert.deepStrictEqual(inconsistentFailures, []);

console.log('wc bracket runtime tests passed');
