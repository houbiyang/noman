// Isolated plugin contract tests: no access to real browser or user storage.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
let count = 0;
function check(value, message) { assert.ok(value, message); count++; }
const values = new Map();
let deny = false;
const localStorage = {
  get length() { if (deny) throw Error('SecurityError'); return values.size; },
  key(i) { return [...values.keys()][i]; },
  getItem(key) { if (deny) throw Error('SecurityError'); return values.get(key) ?? null; },
  setItem(key, value) { if (deny) throw Error('QuotaExceededError'); values.set(key, value); }
};
const context = { LibraryManager: { library: {} }, mergeInto: Object.assign, localStorage,
  window: { location: { pathname: '/test/' } }, UTF8ToString: value => value, QiongtuReturnString: value => value };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../Assets/Plugins/WebGL/SandboxHistory.jslib'), 'utf8'), context);
const plugin = context.LibraryManager.library;
context.QiongtuHistoryPrefix = plugin.$QiongtuHistoryPrefix;
check(JSON.parse(plugin.QiongtuHistoryRead()).records.length === 0, 'empty history');
check(plugin.QiongtuHistoryWrite('0123456789abcdef0123456789abcdef', '{"title":"红方 20:16"}') === '', 'Unicode write');
let read = JSON.parse(plugin.QiongtuHistoryRead());
check(read.records.length === 1 && read.records[0].id === '0123456789abcdef0123456789abcdef' && JSON.parse(read.records[0].content).title === '红方 20:16', 'reload exact record');
check(plugin.QiongtuHistoryWrite('0123456789abcdef0123456789abcdef', 'overwrite').length > 0, 'duplicate refuses overwrite');
check(JSON.parse(JSON.parse(plugin.QiongtuHistoryRead()).records[0].content).title === '红方 20:16', 'original retained');
values.set('qiongtu:save:v1:/test/:legacy', 'preserve');
check(JSON.parse(plugin.QiongtuHistoryRead()).records.length === 1, 'legacy saves are separate');
context.window.location.pathname = '/test/index.html';
check(JSON.parse(plugin.QiongtuHistoryRead()).records.length === 1, 'index file and directory share archive');
check(plugin.QiongtuHistoryWrite('../bad', 'x').length > 0, 'invalid id rejected');
context.window.location.pathname = '/other/';
check(JSON.parse(plugin.QiongtuHistoryRead()).records.length === 0, 'path namespace separation');
context.window.location.pathname = '/test/'; deny = true;
check(plugin.QiongtuHistoryWrite('fedcba9876543210fedcba9876543210', 'x').length > 0, 'quota/security refusal reported');
read = JSON.parse(plugin.QiongtuHistoryRead());
check(read.records.length === 0 && read.warning.length > 0, 'read denial surfaced');
deny = false;
check(JSON.parse(plugin.QiongtuHistoryRead()).records.length === 1 && values.get('qiongtu:save:v1:/test/:legacy') === 'preserve', 'failures do not destroy records');
console.log(`[HistoryStorageChecks] PASS ${count} checks; isolated localStorage stub`);
