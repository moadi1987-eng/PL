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
};
vm.createContext(context);
vm.runInContext(
  'var D={league:"laliga"};' +
  'var EMBEDDED_MODELS=' + JSON.stringify(models) + ';' +
  'var LEARNING_HISTORY=' + JSON.stringify(history) + ';' +
  'function defaultWeights(){return ' + JSON.stringify(defaultWeightValues) + ';}' +
  'function defaultCalibration(){return ' + JSON.stringify(defaultCalibrationValues) + ';}',
  context,
);
vm.runInContext(source, context);

const seasonRows = vm.runInContext(
  'learningRowsForSeason([{season:"2025-26",gw:1},{season:"2026-27",gw:1}],"2026-27")',
  context,
);
assert.deepStrictEqual(JSON.parse(JSON.stringify(seasonRows)), [{ season: '2026-27', gw: 1 }]);
const llRows = vm.runInContext(
  'learningRowsForSeason([{season:"2025-26",gw:29},{season:"2026-27",gw:1}],"2025-26")',
  context,
);
assert.deepStrictEqual(JSON.parse(JSON.stringify(llRows)), [{ season: '2025-26', gw: 29 }]);

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
vm.runInContext('EMBEDDED_MODELS.pl={factors:["not","weights"],calibration:["not","calibration"]};', context);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeWeights())), defaultWeightValues);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeCalibration())), defaultCalibrationValues);

vm.runInContext('EMBEDDED_MODELS.laliga={factors:{form:.21,strength:"bad",position:NaN,home_adv:Infinity,ignored:99},calibration:{goal_mult:1.1,home_goal_bias:null,away_goal_bias:"bad",draw_bias:Infinity,ignored:99}};', context);
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
const forgedObjectPrototype = { constructor: Object };
const forgedObjectModel = Object.create(forgedObjectPrototype);
forgedObjectModel.factors = { strength: 0.93 };
forgedObjectModel.calibration = { goal_mult: 1.17 };
context.EMBEDDED_MODELS = { pl: forgedObjectModel };
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeWeights())), defaultWeightValues);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeCalibration())), defaultCalibrationValues);
const nullRootForgedPrototype = Object.create(null);
nullRootForgedPrototype.constructor = Object;
const nullRootForgedModel = Object.create(nullRootForgedPrototype);
nullRootForgedModel.factors = { strength: 0.94 };
nullRootForgedModel.calibration = { goal_mult: 1.16 };
context.EMBEDDED_MODELS = { pl: nullRootForgedModel };
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeWeights())), defaultWeightValues);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeCalibration())), defaultCalibrationValues);
const fullyForgedPrototype = Object.create(null, Object.getOwnPropertyDescriptors(Object.prototype));
const fullyForgedModel = Object.create(fullyForgedPrototype);
fullyForgedModel.factors = { strength: 0.95 };
fullyForgedModel.calibration = { goal_mult: 1.15 };
context.EMBEDDED_MODELS = { pl: fullyForgedModel };
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeWeights())), defaultWeightValues);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeCalibration())), defaultCalibrationValues);
const nullModelMap = Object.create(null);
const nullModel = Object.create(null);
nullModel.factors = { strength: 0.51 };
nullModel.calibration = { goal_mult: 1.05 };
nullModelMap.pl = nullModel;
context.EMBEDDED_MODELS = nullModelMap;
assert.strictEqual(context.activeWeights().strength, 0.51);
assert.strictEqual(context.activeCalibration().goal_mult, 1.05);
const hostPlainModel = { factors: { strength: 0.41 }, calibration: { goal_mult: 1.04 } };
context.EMBEDDED_MODELS = { pl: hostPlainModel };
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeWeights())), defaultWeightValues);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.activeCalibration())), defaultCalibrationValues);
vm.runInContext('EMBEDDED_MODELS={pl:{factors:{strength:.41},calibration:{goal_mult:1.04}}};', context);
assert.strictEqual(context.activeWeights().strength, 0.41);
assert.strictEqual(context.activeCalibration().goal_mult, 1.04);

const template = fs.readFileSync('website/pl_mobile_template.html', 'utf8');
const statusSource = template.slice(template.indexOf('function aiStatusText'), template.indexOf('function aiTrendChart'));
const renderContext = {
};
vm.createContext(renderContext);
vm.runInContext(
  'var D={league:"laliga"};' +
  'var EMBEDDED_MODELS={laliga:{active_strategy:"baseline",factors:' + JSON.stringify(defaultWeightValues) + ',meta:{trained_matches:0}}};' +
  'var LEARNING_HISTORY={laliga:{model_status:{status:"collecting",active_strategy:"baseline",candidate_strategy:"v4"}}};' +
  'function defaultWeights(){return ' + JSON.stringify(defaultWeightValues) + ';}' +
  'function defaultCalibration(){return ' + JSON.stringify(defaultCalibrationValues) + ';}',
  renderContext,
);
vm.runInContext(source, renderContext);
vm.runInContext(statusSource, renderContext);
const aiRenderSource = template.slice(template.indexOf('var _aiChartAcc'), template.indexOf('\ninit();'));
const aiTarget = { innerHTML: '' };
const aiRenderContext = { aiTarget };
const comparison = {
  total: 10,
  baseline: { winner_accuracy: 60, exact_accuracy: 40, points: 18, unique_scores: 4, top_scores: [] },
  challenger: { winner_accuracy: 50, exact_accuracy: 30, points: 15, unique_scores: 3, top_scores: [] },
  delta: { winner_accuracy: -10, exact_accuracy: -10, points: -3, unique_scores: -1, draw_picks: 0 },
};
vm.createContext(aiRenderContext);
vm.runInContext(
  'var D={league:"laliga",llSeason:"2025-26",plSeason:"2026-27",arch:true,fx:[],sel:1};' +
  'var EMBEDDED_MODELS=' + JSON.stringify({
    pl: { active_strategy: 'baseline', factors: defaultWeightValues },
    laliga: { active_strategy: 'baseline', factors: defaultWeightValues },
    wc: { active_strategy: 'baseline', factors: defaultWeightValues },
  }) + ';' +
  'var LEARNING_HISTORY=' + JSON.stringify({
    pl: { current_season: '2026-27', gw_results: [{ season: '2026-27', gw: 1, total: 1, correct_winner: 1, correct_score: 1, points: 8 }], model_comparison: comparison, model_status: { verified_lifecycle_samples: 1 } },
    laliga: { current_season: '2026-27', gw_results: [], model_comparison: comparison, model_status: { verified_lifecycle_samples: 0 } },
    wc: { current_season: '2026', gw_results: [{ season: '2026', gw: 1, total: 1, correct_winner: 1, correct_score: 1, points: 3 }], model_comparison: comparison, model_status: { verified_lifecycle_samples: 1 } },
  }) + ';' +
  'function defaultWeights(){return ' + JSON.stringify(defaultWeightValues) + ';}' +
  'function defaultCalibration(){return ' + JSON.stringify(defaultCalibrationValues) + ';}' +
  'function $(id){return aiTarget;}',
  aiRenderContext,
);
vm.runInContext(source, aiRenderContext);
vm.runInContext(aiRenderSource, aiRenderContext);
function renderAi(league, season, archived) {
  aiRenderContext.D.league = league;
  aiRenderContext.D.arch = archived;
  if (league === 'laliga') aiRenderContext.D.llSeason = season;
  if (league === 'pl') aiRenderContext.D.plSeason = season;
  aiTarget.innerHTML = '';
  aiRenderContext.rAI();
  return aiTarget.innerHTML;
}
const emptyArchiveHtml = renderAi('laliga', '2025-26', true);
assert.match(emptyArchiveHtml, /No verified La Liga 2025-26 AI history/);
assert.match(emptyArchiveHtml, /Model Status/);
assert.match(emptyArchiveHtml, /Data used/);
assert.match(emptyArchiveHtml, /What Drives The Pick/);
assert.match(emptyArchiveHtml, /Recent Form/);
assert.doesNotMatch(emptyArchiveHtml, /aihero|hit rate|overall 0%|exact 0%|Accuracy Trend|MD Breakdown|Model Test|60%|50%/);
aiRenderContext.LEARNING_HISTORY.laliga.gw_results = [
  { season: '2025-26', gw: 29, total: 1, correct_winner: 1, correct_score: 1, points: 8 },
  { season: '2026-27', gw: 1, total: 1, correct_winner: 0, correct_score: 0, points: 0 },
];
const genuineArchiveHtml = renderAi('laliga', '2025-26', true);
assert.match(genuineArchiveHtml, /aihero|MD29 hit rate/);
assert.doesNotMatch(genuineArchiveHtml, /Model Test/);
aiRenderContext.LEARNING_HISTORY.laliga.model_comparison = { ...comparison, season: '2025-26' };
assert.match(renderAi('laliga', '2025-26', true), /Model Test/);
aiRenderContext.LEARNING_HISTORY.laliga.model_comparison = comparison;
aiRenderContext.LEARNING_HISTORY.laliga.gw_results = [{ season: '2026-27', gw: 1, total: 1, correct_winner: 1, correct_score: 1, points: 8 }];
const currentLaLigaHtml = renderAi('laliga', '2026-27', false);
assert.match(currentLaLigaHtml, /aihero|MD1 hit rate/);
assert.match(currentLaLigaHtml, /Model Test|60%/);
assert.match(renderAi('pl', '2026-27', false), /aihero|GW1 hit rate/);
assert.match(renderAi('wc', '2026', false), /aihero|Day1 hit rate/);
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
const completeMetricsStatus = renderContext.aiModelStatus(
  { model_status: { status: 'collecting', active_strategy: 'baseline', candidate_strategy: 'v4', verified_lifecycle_samples: 2 }, data_completeness_pct: 100 },
  { total: 2, models: {
    baseline: { points: 4, winner_accuracy: 50, exact_accuracy: 25, goal_mae: 0.75, outcome_brier: 0.42, draw_pick_rate: 50, scoreline_concentration: 50, sample_size: 2, completeness_pct: 100 },
    v4: { points: 5, winner_accuracy: 50, exact_accuracy: 50, goal_mae: 0.5, outcome_brier: 0.31, draw_pick_rate: 0, scoreline_concentration: 50, sample_size: 2, completeness_pct: 100 },
  } },
);
assert.match(completeMetricsStatus, /MAE 0\.75/);
assert.match(completeMetricsStatus, /Brier 0\.42/);
assert.match(completeMetricsStatus, /draw 50%/);
assert.match(completeMetricsStatus, /concentration 50%/);
assert.match(completeMetricsStatus, /2 samples · 100% complete/);
assert.match(completeMetricsStatus, /all verified seasons/);
const invalidMetricsStatus = renderContext.aiModelStatus(
  { model_status: { status: 'collecting', active_strategy: 'baseline', candidate_strategy: 'v4', verified_lifecycle_samples: 1 } },
  { total: 1, models: { baseline: { goal_mae: Infinity, outcome_brier: NaN }, v4: {} } },
);
assert.doesNotMatch(invalidMetricsStatus, /Infinity|NaN/);
assert.match(template, /learningRowsForSeason\(normAIRows/);
assert.match(template, /selectedSeason/);

console.log('learning runtime ok');
