// Dependency-free regression checks: node backend/tests/check_workbench_ui.cjs
// Runs only local source in a VM; never connects to a game or model provider.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'backend/agent-workbench.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
class Element {
  constructor() { this.value = ''; this.disabled = false; this.hidden = false; this.textContent = ''; this.dataset = {}; this.classList = {toggle() {}}; this.details = []; }
  set innerHTML(value) {
    this.html = value;
    this.details = [...value.matchAll(/<details data-detail-key="([^"]+)"/g)].map(match => ({dataset:{detailKey:match[1]}, open:false}));
  }
  get innerHTML() { return this.html || ''; }
  querySelectorAll(selector) {
    if (selector === 'details[data-detail-key][open]') return this.details.filter(detail => detail.open);
    if (selector === 'details[data-detail-key]') return this.details;
    return [];
  }
  addEventListener() {}
}
const nodes = new Map();
const document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); }, querySelectorAll() { return []; }};
let interval;
const context = vm.createContext({document, setInterval(fn, ms) { interval = {fn, ms}; }, console});
vm.runInContext(source, context);
function run(code) { return vm.runInContext(code, context); }
function node(id) { return document.getElementById(id); }
let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }
async function main() {
  check(interval.ms === 3000, 'automatic refresh remains every three seconds');
  check(/id="refresh"[^>]*aria-label="手动刷新状态"[^>]*disabled/.test(html), 'manual refresh is an accessible icon, initially disabled');
  check(!/data-command="(?:pause|continue)"/.test(html), 'no duplicate pause/continue buttons');
  check((html.match(/id="togglePlayback"/g) || []).length === 1, 'exactly one playback toggle');
  check(/<details class="panel rules-panel"><summary>技术详情 · 协同流程与规则/.test(html), 'static flow and rules are collapsed by default');
  run("session=null;view=null;updatePlaybackControl()");
  check(node('togglePlayback').disabled && node('refresh').disabled, 'disconnected controls disabled');
  node('role').value = 'host';
  run("session={game_id:'fixture'};view={clock:{phase:'preparing'},capabilities:{can_advance:false}};updatePlaybackControl()");
  check(node('togglePlayback').disabled && node('togglePlayback').textContent === '继续播放', 'preparing cannot continue');
  run("view={clock:{phase:'paused'},capabilities:{can_advance:true}};updatePlaybackControl()");
  check(!node('togglePlayback').disabled && !node('refresh').disabled, 'paused host can continue and manually refresh');
  run("view.clock.phase='running';updatePlaybackControl()");
  check(node('togglePlayback').textContent === '暂停' && !node('togglePlayback').disabled, 'running host can pause');
  node('role').value = 'red'; run('updatePlaybackControl()');
  check(node('togglePlayback').disabled, 'non-host cannot operate time');
  node('role').value = 'host';
  run("view={clock:{phase:'finished'},capabilities:{can_advance:false}};updatePlaybackControl()");
  check(node('togglePlayback').disabled, 'finished game cannot continue');
  run("view.clock.phase='paused';updatePlaybackControl()");
  check(node('togglePlayback').disabled, 'pending confirmation cannot continue without capability');
  run("refresh=async()=>updatePlaybackControl();command=async kind=>kind;view={clock:{phase:'running'},capabilities:{can_advance:true}}");
  check(await run('togglePlayback()') === 'pause', 'toggle selects pause for running view');
  run("view.clock.phase='paused'");
  check(await run('togglePlayback()') === 'continue', 'toggle selects continue for paused view');
  run("view.capabilities.can_advance=false");
  await assert.rejects(run('togglePlayback()'), /当前阶段不可播放/); checks++;
  node('role').value = 'blue';
  await assert.rejects(run('togglePlayback()'), /主持人/); checks++;
  run("var fixture=[{role:'commander',run_id:'run-1',status:'waiting_human',output:{output:{summary:'请确认本方方案'}},knowledge:{retrieval:{knowledge_version:'fixture-v1'}}}];renderRuns(fixture)");
  check(node('runs').innerHTML.includes('批准采用') && node('runs').innerHTML.includes('驳回'), 'human review remains visible');
  check(node('runs').details.length === 2 && node('runs').details.every(detail => !detail.open), 'raw plan and evidence collapsed by default');
  node('runs').details.find(detail => detail.dataset.detailKey === 'run-1-raw').open = true;
  run('renderRuns(fixture)');
  check(node('runs').details.find(detail => detail.dataset.detailKey === 'run-1-raw').open, 'expanded technical data stays open after automatic refresh');
  check(!node('runs').details.find(detail => detail.dataset.detailKey === 'run-1-evidence').open, 'other details stay collapsed');
  const settings = fs.readFileSync(path.join(root, 'unity-client/Assets/Sandbox/Scripts/Presentation/SandboxSettingsPanel.cs'), 'utf8');
  check(settings.includes('closeSettings') && settings.includes('saveSettings'), 'settings retain top close and save');
  check(!settings.includes('validateSettings') && !settings.includes('cancelSettings'), 'duplicate footer actions removed');
  check(!/StartHealthCheck|CancelHealthCheck|连接检测|取消检测|testService/.test(settings), 'entire settings health-check UI and request removed');
  check(settings.includes('if (!ValidateSettings()) return;') && settings.includes('openAgentWorkbench'), 'save still validates and workbench entry remains');
  console.log(`PASS ${checks} UI regression checks (isolated VM, no service/model requests)`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
