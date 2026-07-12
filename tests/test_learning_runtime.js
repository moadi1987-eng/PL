const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const source = fs.readFileSync('website/learning_runtime.js', 'utf8');
const defaultWeightValues = {
  form: 0.15, strength: 0.15, position: 0.12, home_adv: 0.08,
  streak: 0.12, h2h: 0.08, home_away_split: 0.08, goals_trend: 0.06,
  upset: 0.06, clean_sheet: 0.05, draw_tendency: 0.05,
};
const defaultCalibrationValues = {
  goal_mult: 1, home_goal_bias: 0, away_goal_bias: 0, draw_bias: 1,
  zero_zero_penalty: 0.62,
};
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
  defaultWeights: () => defaultWeightValues,
  defaultCalibration: () => defaultCalibrationValues,
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

context.D.league = 'pl';
context.EMBEDDED_MODELS.pl = {
  factors: ['not', 'weights'], calibration: ['not', 'calibration'],
};
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeWeights())), defaultWeightValues);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeCalibration())), defaultCalibrationValues);

context.EMBEDDED_MODELS.laliga = {
  factors: { form: 0.21, strength: 'bad', position: NaN, home_adv: Infinity, ignored: 99 },
  calibration: { goal_mult: 1.1, home_goal_bias: null, away_goal_bias: 'bad', draw_bias: Infinity, ignored: 99 },
};
context.D.league = 'laliga';
const sanitizedWeights = context.activeWeights();
const sanitizedCalibration = context.activeCalibration();
assert.strictEqual(sanitizedWeights.form, 0.21);
assert.strictEqual(sanitizedWeights.strength, defaultWeightValues.strength);
assert.strictEqual(sanitizedWeights.position, defaultWeightValues.position);
assert.strictEqual(sanitizedWeights.home_adv, defaultWeightValues.home_adv);
assert.strictEqual(Object.prototype.hasOwnProperty.call(sanitizedWeights, 'ignored'), false);
assert.strictEqual(sanitizedCalibration.goal_mult, 1.1);
assert.strictEqual(sanitizedCalibration.home_goal_bias, defaultCalibrationValues.home_goal_bias);
assert.strictEqual(sanitizedCalibration.away_goal_bias, defaultCalibrationValues.away_goal_bias);
assert.strictEqual(sanitizedCalibration.draw_bias, defaultCalibrationValues.draw_bias);
assert.strictEqual(Object.prototype.hasOwnProperty.call(sanitizedCalibration, 'ignored'), false);
Object.keys(defaultWeightValues).forEach((key) => assert.strictEqual(Number.isFinite(sanitizedWeights[key]), true));
Object.keys(defaultCalibrationValues).forEach((key) => assert.strictEqual(Number.isFinite(sanitizedCalibration[key]), true));
sanitizedWeights.form = 99;
sanitizedCalibration.goal_mult = 99;
assert.strictEqual(defaultWeightValues.form, 0.15);
assert.strictEqual(defaultCalibrationValues.goal_mult, 1);

context.D.league = 'laliga';
const laligaWeights = context.activeWeights();
const laligaCalibration = context.activeCalibration();
laligaWeights.strength = 99;
laligaCalibration.goal_mult = 99;
assert.strictEqual(context.EMBEDDED_MODELS.laliga.factors.strength, 'bad');
assert.strictEqual(context.EMBEDDED_MODELS.laliga.calibration.goal_mult, 1.1);

context.EMBEDDED_MODELS.laliga = { active_strategy: 'baseline' };
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeWeights())), defaultWeightValues);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeCalibration())), defaultCalibrationValues);
assert.strictEqual(context.scoreModelChoice().strategy, 'v4');
assert.notStrictEqual(context.activeWeights().strength, models.pl.factors.strength);

class EmbeddedModel {
  constructor() {
    this.factors = { strength: 0.91 };
    this.calibration = { goal_mult: 1.19 };
  }
}
context.D.league = 'pl';
context.EMBEDDED_MODELS = { pl: new EmbeddedModel() };
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeWeights())), defaultWeightValues);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeCalibration())), defaultCalibrationValues);
const customPrototype = { factors: { strength: 0.92 }, calibration: { goal_mult: 1.18 } };
context.EMBEDDED_MODELS = { pl: Object.create(customPrototype) };
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeWeights())), defaultWeightValues);
const nullModelMap = Object.create(null);
const nullModel = Object.create(null);
nullModel.factors = { strength: 0.51 };
nullModel.calibration = { goal_mult: 1.05 };
nullModelMap.pl = nullModel;
context.EMBEDDED_MODELS = nullModelMap;
assert.strictEqual(context.activeWeights().strength, 0.51);
assert.strictEqual(context.activeCalibration().goal_mult, 1.05);
context.EMBEDDED_MODELS = { pl: { factors: { strength: 0.41 }, calibration: { goal_mult: 1.04 } } };
assert.strictEqual(context.activeWeights().strength, 0.41);
assert.strictEqual(context.activeCalibration().goal_mult, 1.04);

const template = fs.readFileSync('website/pl_mobile_template.html', 'utf8');
const statusSource = template.slice(template.indexOf('function aiStatusText'), template.indexOf('function aiTrendChart'));
const renderContext = {
  D: { league: 'laliga' },
  EMBEDDED_MODELS: { laliga: { active_strategy: 'baseline', factors: defaultWeightValues, meta: { trained_matches: 0 } } },
  LEARNING_HISTORY: { laliga: { model_status: { status: 'collecting', active_strategy: 'baseline', candidate_strategy: 'v4' } } },
  defaultWeights: () => defaultWeightValues,
  defaultCalibration: () => defaultCalibrationValues,
};
vm.createContext(renderContext);
vm.runInContext(source, renderContext);
vm.runInContext(statusSource, renderContext);
assert.strictEqual(renderContext.aiStatusNumber(null), null);
assert.strictEqual(renderContext.aiStatusNumber(undefined), null);
assert.strictEqual(renderContext.aiStatusNumber(NaN), null);
assert.strictEqual(renderContext.aiStatusNumber(Infinity), null);
const freshLaLigaStatus = renderContext.aiModelStatus(
  { model_status: renderContext.LEARNING_HISTORY.laliga.model_status, data_completeness_pct: 100, model_meta: { trained_matches: 0 } },
  { total: 0, models: { baseline: {}, v4: {} } },
);
assert.match(freshLaLigaStatus, /Collecting 0\/30/);
assert.match(freshLaLigaStatus, /<b>n\/a<\/b><em>Trusted inputs unavailable<\/em>/);
assert.doesNotMatch(freshLaLigaStatus, /100\.0%|All tracked inputs available/);
const localOnlyStatus = renderContext.aiModelStatus(
  { model_status: renderContext.LEARNING_HISTORY.laliga.model_status, data_completeness_pct: 100, model_meta: { trained_matches: 0 } },
  { total: 2, models: { baseline: {}, v4: {} } },
);
assert.match(localOnlyStatus, /Collecting 0\/30/);
assert.match(localOnlyStatus, /Trusted inputs unavailable/);
const stalePlStatus = renderContext.aiModelStatus(
  { model_status: { status: 'collecting', active_strategy: 'baseline', candidate_strategy: 'v4' }, data_completeness_pct: 100, model_comparison: { total: 108 } },
  { total: 108, models: { baseline: {}, v4: {} } },
);
assert.match(stalePlStatus, /Collecting 0\/30/);
assert.match(stalePlStatus, /Trusted inputs unavailable/);
const explicitLifecycleStatus = renderContext.aiModelStatus(
  { model_status: { status: 'collecting', active_strategy: 'baseline', candidate_strategy: 'v4', verified_lifecycle_samples: 4 }, data_completeness_pct: 76.5, model_comparison: { total: 108 } },
  { total: 108, models: { baseline: {}, v4: {} } },
);
assert.match(explicitLifecycleStatus, /Collecting 4\/30/);
assert.match(explicitLifecycleStatus, /76\.5%/);
const verifiedStatus = renderContext.aiModelStatus(
  { model_status: { status: 'collecting', active_strategy: 'baseline', candidate_strategy: 'v4', verified_lifecycle_samples: 2 }, data_completeness_pct: 76.5, model_comparison: { total: 2 } },
  { total: 2, models: { baseline: {}, v4: {} } },
);
assert.match(verifiedStatus, /76\.5%/);
assert.match(verifiedStatus, /Some trusted inputs unavailable/);

console.log('learning runtime ok');
