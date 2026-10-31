import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { randomInt, randomUUID } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const frontOrigin = process.env.LIVE_TEST_ORIGIN || 'http://127.0.0.1:5173'
const apiRoot = `${frontOrigin}/api/v1`
const edgePath = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const bootstrapPath = path.resolve('..', 'backend', '.local', 'bootstrap-key')
const scenarioPath = path.resolve('..', 'backend', 'scenarios', 'initial_scenario.json')

async function postJson(url, headers, body) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) })
  const result = await response.json()
  if (!response.ok) throw new Error(`测试对局创建失败：HTTP ${response.status}`)
  return result
}

async function waitUntil(check, attempts = 50) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (await check()) return true
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  return false
}

async function main() {
  assert(fs.existsSync(edgePath), '未找到本机 Edge 浏览器')
  assert(fs.existsSync(bootstrapPath), '未找到本机受控引导密钥')
  const bootstrapKey = fs.readFileSync(bootstrapPath, 'utf8').trim()
  const scenario = JSON.parse(fs.readFileSync(scenarioPath, 'utf8'))
  const requestKey = randomUUID()
  const created = await postJson(`${apiRoot}/games`, { 'X-Create-Game-Key': bootstrapKey, 'Idempotency-Key': requestKey }, {
    title: `前端席位验证-${requestKey.slice(0, 8)}`,
    scenario_id: scenario.scenario_id,
    rule_version_id: scenario.rule_version_id,
    is_demo: false,
  })
  const hostSession = await postJson(`${apiRoot}/bootstrap/games/${created.game_id}/sessions?role=host`, { 'X-Bootstrap-Key': bootstrapKey }, {})
  const hostViewResponse = await fetch(`${apiRoot}/games/${created.game_id}/view`, { headers: { Authorization: `Bearer ${hostSession.token}` } })
  assert(hostViewResponse.ok, '主持人视图读取失败')
  const hostView = await hostViewResponse.json()
  const rawTasks = { red: '区域巡查|A-B-C|60', blue: '区域巡查|C-B-A|60' }
  const preview = await postJson(`${apiRoot}/games/${created.game_id}/task-configuration/preview`, { Authorization: `Bearer ${hostSession.token}` }, {
    context: hostView.context, input_revision: '1', raw_tasks: rawTasks,
  })
  assert(preview.valid && preview.preview_token, '主持人任务预览未通过')
  const taskReceipt = await postJson(`${apiRoot}/games/${created.game_id}/commands`, {
    Authorization: `Bearer ${hostSession.token}`, 'Idempotency-Key': randomUUID(),
  }, {
    context: hostView.context, command_type: 'set_tasks',
    payload: { raw_tasks: rawTasks, input_revision: '1', preview_token: preview.preview_token },
  })
  assert.equal(taskReceipt.status, 'succeeded')
  const issued = await postJson(`${apiRoot}/bootstrap/games/${created.game_id}/sessions?role=red`, { 'X-Bootstrap-Key': bootstrapKey }, {})
  const blueSession = await postJson(`${apiRoot}/bootstrap/games/${created.game_id}/sessions?role=blue`, { 'X-Bootstrap-Key': bootstrapKey }, {})
  const profilePath = path.join(os.tmpdir(), `qiongtu-live-ui-${randomUUID()}`)
  const debugPort = randomInt(9300, 9800)
  const browser = spawn(edgePath, [
    '--headless', '--disable-gpu', '--no-first-run', '--remote-debugging-address=127.0.0.1',
    `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profilePath}`,
    `${frontOrigin}/?view=live`,
  ], { windowsHide: true, stdio: 'ignore' })
  let socket
  try {
    let tabs
    const browserReady = await waitUntil(async () => {
      try {
        tabs = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json()
        return tabs.some(tab => tab.type === 'page')
      } catch { return false }
    })
    assert(browserReady, '浏览器调试入口未就绪')
    socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl)
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true })
      socket.addEventListener('error', reject, { once: true })
    })
    let commandId = 0
    const pending = new Map()
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data)
      if (!pending.has(message.id)) return
      const { resolve, reject } = pending.get(message.id)
      pending.delete(message.id)
      message.error ? reject(new Error(message.error.message)) : resolve(message.result)
    })
    const command = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++commandId
      pending.set(id, { resolve, reject })
      socket.send(JSON.stringify({ id, method, params }))
    })
    const evaluate = async expression => (await command('Runtime.evaluate', { expression, returnByValue: true })).result.value
    const connectToken = async token => {
      assert(await waitUntil(() => evaluate('Boolean(document.querySelector(".connect-card input"))')), '席位令牌输入框未出现')
      await evaluate(`(() => { const input = document.querySelector('.connect-card input'); input.value = ${JSON.stringify(token)}; input.dispatchEvent(new Event('input', { bubbles: true })); return true })()`)
      assert(await waitUntil(() => evaluate('!document.querySelector(".connect-card > button").disabled')), '进入对局按钮未启用')
      await evaluate('document.querySelector(".connect-card > button").click()')
      assert(await waitUntil(() => evaluate('Boolean(document.querySelector(".formal-toolbar") && document.querySelector(".service-map"))')), `角色视图未加载：${await evaluate('document.body.innerText.slice(-350)')}`)
    }
    const leaveSeat = async () => {
      await evaluate('[...document.querySelectorAll(".formal-toolbar button")].find(button => button.innerText.includes("退出席位")).click()')
      assert(await waitUntil(() => evaluate('Boolean(document.querySelector(".connect-card input"))')), '退出席位后未返回连接页')
    }
    await connectToken(issued.token)
    const result = await evaluate('({ role: document.querySelector(".formal-toolbar").innerText, units: [...document.querySelectorAll(".formal-card")].find(card => card.innerText.includes("可见单位"))?.querySelectorAll(".data-row").length, readyButton: [...document.querySelectorAll(".formal-actions button")].some(button => button.innerText.includes("本方准备完成") && !button.disabled) })')
    assert.match(await evaluate('document.querySelector(".formal-toolbar").innerText'), /首版正式场景/)
    assert.equal(result.units, 3)
    assert.equal(result.readyButton, true)
    assert.match(result.role, /红方操作员/)
    await evaluate('[...document.querySelectorAll(".formal-actions button")].find(button => button.innerText.includes("本方准备完成")).click()')
    const readyShown = await waitUntil(() => evaluate('[...document.querySelectorAll(".formal-actions button")].some(button => button.innerText === "本方已准备")'))
    if (!readyShown) {
      const response = await fetch(`${apiRoot}/games/${created.game_id}/view`, { headers: { Authorization: `Bearer ${issued.token}` } })
      const latest = await response.json()
      throw new Error(`本方准备命令未在页面生效：服务端准备状态 ${JSON.stringify(latest.preparation_status ?? null)}；页面 ${await evaluate('document.body.innerText.slice(-600)')}`)
    }
    await leaveSeat()
    await connectToken(blueSession.token)
    assert.match(await evaluate('document.querySelector(".formal-toolbar").innerText'), /蓝方操作员/)
    assert.equal(await evaluate('[...document.querySelectorAll(".formal-card")].find(card => card.innerText.includes("可见单位")).querySelectorAll(".data-row").length'), 3)
    await evaluate('[...document.querySelectorAll(".formal-actions button")].find(button => button.innerText.includes("本方准备完成")).click()')
    assert(await waitUntil(() => evaluate('[...document.querySelectorAll(".formal-actions button")].some(button => button.innerText === "本方已准备")')), '蓝方准备命令未在页面生效')
    await leaveSeat()
    await connectToken(hostSession.token)
    assert.match(await evaluate('document.querySelector(".formal-toolbar").innerText'), /主持人/)
    assert.equal(await evaluate('[...document.querySelectorAll(".formal-card")].find(card => card.innerText.includes("可见单位")).querySelectorAll(".data-row").length'), 6)
    assert(await waitUntil(() => evaluate('[...document.querySelectorAll(".formal-actions button")].some(button => button.innerText === "开始对局" && !button.disabled)')), '双方准备后主持人无法开局')
    await evaluate('[...document.querySelectorAll(".formal-actions button")].find(button => button.innerText === "开始对局").click()')
    assert(await waitUntil(() => evaluate('document.body.innerText.includes("阶段：等待人工确认")')), '开局后未显示服务端等待确认阶段')
    assert(await evaluate('[...document.querySelectorAll(".formal-actions button")].some(button => button.innerText === "确认演示方案" && button.disabled)'), '正式对局在缺少真实方案时应禁止确认')
    await evaluate('[...document.querySelectorAll(".formal-topbar button")].find(button => button.innerText === "历史与快照").click()')
    assert(await waitUntil(() => evaluate('document.body.innerText.includes("查看快照")')), '正式历史目录未加载')
    await evaluate('[...document.querySelectorAll(".catalog-meta button")].find(button => button.innerText === "查看快照").click()')
    assert(await waitUntil(() => evaluate('document.querySelectorAll(".save-row").length > 0')), '正式快照目录未加载')
    await evaluate('document.querySelector(".save-row button").click()')
    assert(await waitUntil(() => evaluate('Boolean(document.querySelector(".replay-frame .service-map"))')), '快照只读复盘地图未加载')
    await evaluate('[...document.querySelectorAll(".replay-actions button")].find(button => button.innerText === "返回历史对局").click()')
    assert(await waitUntil(() => evaluate('Boolean(document.querySelector(".catalog-card"))')), '复盘返回历史目录失败')
    await evaluate('[...document.querySelectorAll(".catalog-header-actions button")].find(button => button.innerText === "退出席位").click()')
    await evaluate('[...document.querySelectorAll(".catalog-header-actions button")].find(button => button.innerText === "返回首页").click()')
    assert(await waitUntil(() => evaluate('Boolean(document.querySelector(".home-page"))')), '历史目录返回首页失败')
    await evaluate('[...document.querySelectorAll(".action-card")].find(button => button.innerText.includes("打开之前的存档")).click()')
    assert(await waitUntil(() => evaluate('Boolean(document.querySelector(".catalog-token-label input"))')), '首页存档入口未要求席位令牌')
    await evaluate(`(() => { const input = document.querySelector('.catalog-token-label input'); input.value = ${JSON.stringify(hostSession.token)}; input.dispatchEvent(new Event('input', { bubbles: true })); return true })()`)
    await evaluate('document.querySelector(".catalog-state .primary-button").click()')
    assert(await waitUntil(() => evaluate('Boolean(document.querySelector(".catalog-card"))')), '首页存档入口未读取正式历史')
    console.log(`正式三席位浏览器验证通过：双方准备、主持人开局、方案等待边界、首页历史入口和指定快照复盘。对局 ${created.game_id}`)
  } finally {
    socket?.close()
    browser.kill()
    if (browser.exitCode === null) await Promise.race([
      new Promise(resolve => browser.once('exit', resolve)),
      new Promise(resolve => setTimeout(resolve, 3000)),
    ])
    const resolvedProfile = path.resolve(profilePath)
    const temporaryDirectory = path.resolve(os.tmpdir())
    if (resolvedProfile.startsWith(`${temporaryDirectory}${path.sep}`) && path.basename(resolvedProfile).startsWith('qiongtu-live-ui-')) {
      try { fs.rmSync(resolvedProfile, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 }) } catch { /* 浏览器仍持有临时文件时交由系统清理。 */ }
    }
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
