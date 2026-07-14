const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(require('path').join(__dirname, '..', 'website', 'season_runtime.js'), 'utf8');
const context = { console };
vm.runInNewContext(source, context, { filename: 'season_runtime.js' });
const runtime = context.SEASON_RUNTIME;

assert.ok(runtime);
const llCatalog = {
  current: '2026-27',
  items: [{ key: '2026-27' }, { key: '2025-26' }],
  data: { '2026-27': { id: 'current' }, '2025-26': { id: 'archive' } },
};
assert.strictEqual(runtime.resolveKey(llCatalog, '2025-26'), '2025-26');
assert.strictEqual(runtime.resolveKey(llCatalog, 'missing'), '2026-27');
assert.strictEqual(runtime.packFor(llCatalog, '2025-26').id, 'archive');
assert.strictEqual(runtime.catalogFor('laliga', null, llCatalog), llCatalog);
assert.strictEqual(runtime.catalogFor('pl', { data: {}, items: [] }, llCatalog), null);
assert.strictEqual(runtime.guessKey('laliga', '2025-26'), 'llg_2025-26');
assert.strictEqual(runtime.guessKey('laliga', '2026-27'), 'llg_2026-27');
assert.strictEqual(runtime.guessKey('pl', '2026-27'), 'plg_2026-27');
assert.strictEqual(runtime.guessKey('wc', ''), 'wcg');

function Storage() { this.values = Object.create(null); }
Storage.prototype.getItem = function (key) {
  return Object.prototype.hasOwnProperty.call(this.values, key) ? this.values[key] : null;
};
Storage.prototype.setItem = function (key, value) { this.values[key] = String(value); };

const storage = new Storage();
storage.setItem('llg', JSON.stringify({ 29: { 748424: { w: 'draw' } } }));
assert.strictEqual(runtime.migrateLegacyLaligaGuesses(storage, '2025-26'), true);
assert.deepStrictEqual(JSON.parse(storage.getItem('llg_2025-26')), { 29: { 748424: { w: 'draw' } } });
assert.ok(storage.getItem('llg'));

const qualified = new Storage();
qualified.setItem('llg', JSON.stringify({ old: true }));
qualified.setItem('llg_2025-26', JSON.stringify({ qualified: true }));
assert.strictEqual(runtime.migrateLegacyLaligaGuesses(qualified, '2025-26'), false);
assert.deepStrictEqual(JSON.parse(qualified.getItem('llg_2025-26')), { qualified: true });

const invalid = new Storage();
invalid.setItem('llg', '{bad json');
assert.strictEqual(runtime.migrateLegacyLaligaGuesses(invalid, '2025-26'), false);
assert.strictEqual(invalid.getItem('llg_2025-26'), null);

const current = new Storage();
current.setItem('llg', JSON.stringify({ legacy: true }));
assert.strictEqual(runtime.migrateLegacyLaligaGuesses(current, '2026-27'), false);
assert.strictEqual(current.getItem('llg_2026-27'), null);

console.log('season runtime tests passed');
