const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const source = fs.readFileSync('website/learning_runtime.js', 'utf8');
const models = {
  pl: {
    active_strategy: 'baseline',
    factors: { strength: 0.11 },
    calibration: { goal_mult: 1.01 },
  },
  laliga: {
    active_strategy: 'baseline',
    factors: { strength: 0.24 },
    calibration: { goal_mult: 1.08 },
  },
  wc: {
    active_strategy: 'baseline',
    factors: { strength: 0.30 },
    calibration: { goal_mult: 1.12 },
  },
};
const history = {
  pl: { model_status: { status: 'collecting', active_strategy: 'baseline', candidate_strategy: 'v4' } },
  laliga: { model_status: { status: 'winner_guard', active_strategy: 'v4', candidate_strategy: 'baseline' } },
  wc: { model_status: { status: 'promote', active_strategy: 'v4', candidate_strategy: 'baseline' } },
};
const context = {
  D: { league: 'laliga' },
  EMBEDDED_MODELS: models,
  LEARNING_HISTORY: history,
  defaultWeights: () => ({ strength: 0.15 }),
  defaultCalibration: () => ({ goal_mult: 1 }),
};
vm.createContext(context);
vm.runInContext(source, context);

function assertLeague(league, strength, goalMult, strategy, status) {
  context.D.league = league;
  assert.strictEqual(context.learningModelState(league).factors.strength, strength);
  assert.strictEqual(context.activeWeights().strength, strength);
  assert.strictEqual(context.activeCalibration().goal_mult, goalMult);
  assert.strictEqual(context.scoreModelChoice().strategy, strategy);
  assert.strictEqual(context.scoreModelChoice().status, status);
}

assertLeague('pl', 0.11, 1.01, 'baseline', 'collecting');
assertLeague('laliga', 0.24, 1.08, 'v4', 'winner_guard');
assertLeague('wc', 0.30, 1.12, 'v4', 'promote');

context.D.league = 'seriea';
assert.strictEqual(Object.keys(context.learningModelState('seriea')).length, 0);
assert.strictEqual(context.activeWeights().strength, 0.15);
assert.strictEqual(context.activeCalibration().goal_mult, 1);
assert.strictEqual(context.scoreModelChoice().strategy, 'baseline');

context.D.league = 'laliga';
const laligaWeights = context.activeWeights();
const laligaCalibration = context.activeCalibration();
laligaWeights.strength = 99;
laligaCalibration.goal_mult = 99;
assert.strictEqual(models.laliga.factors.strength, 0.24);
assert.strictEqual(models.laliga.calibration.goal_mult, 1.08);

context.EMBEDDED_MODELS.laliga = { active_strategy: 'baseline' };
assert.strictEqual(Object.keys(context.activeWeights()).length, 0);
assert.strictEqual(Object.keys(context.activeCalibration()).length, 0);
assert.strictEqual(context.scoreModelChoice().strategy, 'v4');
assert.notStrictEqual(context.activeWeights().strength, models.pl.factors.strength);

console.log('learning runtime ok');
