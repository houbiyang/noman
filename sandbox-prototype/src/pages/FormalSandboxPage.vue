<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ServiceGridMap from '../components/ServiceGridMap.vue'
import { apiClient, ApiClientError } from '../shared/api'
import type { GameView, Role, SaveSummary, SessionView, TaskConfigurationPreview } from '../shared/types'
import { taskFormatExample } from '../taskFormat'

const props = defineProps<{ gameId: string | null; role: Role }>()
const emit = defineEmits<{ back: []; history: []; entered: [gameId: string]; selectRole: [role: Role] }>()

const gameIdDraft = ref(props.gameId || '')
const requestedRole = ref<Role>(props.role)
const session = ref<SessionView | null>(null)
const view = ref<GameView | null>(null)
const loading = ref(false)
const commandPending = ref(false)
const errorMessage = ref('')
const errorTitle = ref('请求失败')
const feedback = ref('')
const taskDraft = ref({ red: '', blue: '' })
const taskRevision = ref(0)
const taskPreview = ref<TaskConfigurationPreview | null>(null)
const saves = ref<SaveSummary[]>([])
const saveNameDraft = ref('')
const pendingCommand = ref<{ gameId: string; branchId: string; requestKey: string } | null>(null)
const previewSnapshotId = ref('')
const previewPending = ref(false)
const detailsOpen = ref(false)
const taskEditorOpen = ref(false)
let readController: AbortController | null = null
let readSequence = 0
let sessionController: AbortController | null = null
let sessionSequence = 0

const roleLabel = computed(() => session.value?.role === 'host' ? '主持人' : session.value?.role === 'red' ? '红方操作员' : session.value?.role === 'blue' ? '蓝方操作员' : '未取得会话')
const canOperate = computed(() => !!view.value && !!session.value && !loading.value && !commandPending.value && !pendingCommand.value)
const ownedTasks = computed(() => view.value?.tasks || [])
const ownedUnits = computed(() => view.value?.units || [])
const ownedCamps = computed(() => view.value?.camps || [])
const ownedResources = computed(() => view.value?.resources || [])
const visibleTasks = computed(() => ownedTasks.value.filter(task => session.value?.role === 'host' || task.side === session.value?.role))
const ownReady = computed(() => session.value?.role === 'red' || session.value?.role === 'blue' ? view.value?.preparation_status.ready[session.value.role] : undefined)
const isValidGameId = computed(() => /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(gameIdDraft.value.trim()))
const remainingMinutes = computed(() => view.value?.clock.deadline_step != null && view.value.clock.step_minutes != null ? Math.max(0, view.value.clock.deadline_step - view.value.clock.simulation_step) * view.value.clock.step_minutes : null)

function taskNodes(task: Record<string, unknown>): Array<Record<string, unknown>> {
  return Array.isArray(task.nodes) ? task.nodes.filter(node => node && typeof node === 'object' && !Array.isArray(node)) : []
}

function completedNodeCount(task: Record<string, unknown>): number {
  return taskNodes(task).filter(node => node.status === 'completed').length
}

function taskProgress(task: Record<string, unknown>): number {
  const nodes = taskNodes(task)
  return nodes.length ? completedNodeCount(task) / nodes.length * 100 : 0
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return '未提供'
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return '详见服务端角色视图'
}

function sideLabel(value: unknown): string {
  return value === 'red' ? '红方' : value === 'blue' ? '蓝方' : displayValue(value)
}

function unitTypeLabel(value: unknown): string {
  return value === 'recon' ? '侦察无人机' : value === 'ground' ? '陆兵' : value === 'support' ? '保障单位' : displayValue(value)
}

function phaseLabel(value: unknown): string {
  const labels: Record<string, string> = { preparing: '准备中', running: '运行中', paused: '已暂停', awaiting_confirmation: '等待人工确认', finished: '已结束' }
  return typeof value === 'string' ? labels[value] || value : displayValue(value)
}

function taskTypeLabel(value: unknown): string {
  const labels: Record<string, string> = { patrol: '区域巡查', escort: '物资护送', protect: '关键设施保护', evacuate: '受损设备撤离' }
  return typeof value === 'string' ? labels[value] || value : displayValue(value)
}

function stateLabel(value: unknown): string {
  const labels: Record<string, string> = { pending: '待执行', active: '执行中', completed: '已完成', failed: '已失败', waiting_in_camp: '兵营待命', available: '可用', unavailable: '不可用' }
  return typeof value === 'string' ? labels[value] || value : displayValue(value)
}

function positionLabel(value: unknown): string {
  if (value === null) return '位置未提供'
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '位置未提供'
  const position = value as Record<string, unknown>
  if (typeof position.x !== 'string' && typeof position.x !== 'number' || typeof position.y !== 'string' && typeof position.y !== 'number') return '位置未提供'
  return `网格坐标 ${position.x}，${position.y}${typeof position.coordinate_system_id === 'string' ? ` · ${position.coordinate_system_id}` : ''}`
}

function targetLabel(value: unknown): string {
  return Array.isArray(value) && value.every(target => typeof target === 'string') ? value.join(' → ') : '目标未提供'
}

function canCommand(commandType: string): boolean {
  if (!canOperate.value || !view.value || !session.value) return false
  if (commandType === 'set_ready') return (session.value.role === 'red' || session.value.role === 'blue') && (view.value.api_variant === 'minimal' ? view.value.clock.phase === 'preparing' && view.value.preparation_status.ready[session.value.role] !== true : view.value.capabilities.set_ready === true)
  if (commandType === 'set_tasks') return session.value.role === 'host' && (view.value.api_variant === 'minimal' ? view.value.clock.phase === 'preparing' : view.value.capabilities.set_tasks === true)
  if (commandType === 'start_game') {
    const ready = view.value.preparation_status.ready
    return session.value.role === 'host' && (view.value.api_variant === 'minimal' ? view.value.clock.phase === 'preparing' && ready.red === true && ready.blue === true : view.value.capabilities.start_game === true)
  }
  if (view.value.api_variant === 'minimal') return session.value.role === 'host' && ((commandType === 'confirm_initial_plan' && view.value.clock.phase === 'awaiting_confirmation') || (commandType === 'pause' && view.value.clock.phase === 'running') || (commandType === 'continue' && view.value.clock.phase === 'paused') || (commandType === 'advance_step' && view.value.clock.phase === 'running'))
  return session.value.role === 'host' && view.value.capabilities[commandType] === true
}

function showError(error: unknown) {
  errorMessage.value = error instanceof ApiClientError ? `${error.message}（${error.details.code}）${error.pendingCommand ? ` 请求键：${error.pendingCommand.requestKey}` : ''}` : error instanceof Error ? error.message : '正式服务请求失败。'
}

function invalidateTaskPreview() {
  ++taskRevision.value
  taskPreview.value = null
  previewSnapshotId.value = ''
}

function toggleDetails() {
  detailsOpen.value = !detailsOpen.value
  if (detailsOpen.value) taskEditorOpen.value = false
}

function toggleTaskEditor() {
  taskEditorOpen.value = !taskEditorOpen.value
  if (taskEditorOpen.value) detailsOpen.value = false
}

async function previewTaskConfiguration() {
  if (!view.value || session.value?.role !== 'host' || view.value.clock.phase !== 'preparing') return
  const revision = String(taskRevision.value)
  const snapshotId = view.value.context.snapshot_id
  previewPending.value = true
  errorMessage.value = ''
  try {
    const result = await apiClient.previewTasks(view.value.context.game_id, view.value.context, revision, { ...taskDraft.value })
    if (revision !== String(taskRevision.value) || snapshotId !== view.value?.context.snapshot_id) return
    if (result.input_revision !== revision) throw new Error('服务返回了不匹配的任务输入修订')
    taskPreview.value = result
    previewSnapshotId.value = snapshotId
    feedback.value = result.valid ? '任务预览有效，可提交当前输入。' : '任务预览未通过，请按字段错误修改。'
  } catch (error) { errorTitle.value = '任务预览失败'; showError(error) }
  finally { previewPending.value = false }
}

async function applyTaskConfiguration() {
  if (!taskPreview.value?.valid || !taskPreview.value.preview_token || previewSnapshotId.value !== view.value?.context.snapshot_id) return
  await sendCommand('set_tasks', {
    raw_tasks: { ...taskDraft.value },
    input_revision: taskPreview.value.input_revision,
    preview_token: taskPreview.value.preview_token,
  })
}

async function enterLocalGame() {
  sessionController?.abort()
  sessionController = new AbortController()
  const sequence = ++sessionSequence
  requestedRole.value = props.role
  gameIdDraft.value = props.gameId || ''
  apiClient.setAccessToken(null)
  session.value = null
  view.value = null
  loading.value = true
  errorMessage.value = ''
  try {
    const entry = await apiClient.enterLocalGame(props.gameId, props.role, sessionController.signal)
    if (sequence !== sessionSequence) return
    if (entry.role !== props.role || (props.gameId && entry.game_id !== props.gameId)) throw new Error('服务返回的对局或席位与当前请求不一致')
    gameIdDraft.value = entry.game_id
    apiClient.setAccessToken(entry.token)
    if (!props.gameId) emit('entered', entry.game_id)
    await resumeSession()
  } catch (error) {
    if (sequence !== sessionSequence) return
    errorTitle.value = '自动进入对局失败'
    showError(error)
  } finally { if (sequence === sessionSequence) loading.value = false }
}

async function resumeSession() {
  sessionController?.abort()
  sessionController = new AbortController()
  const sequence = ++sessionSequence
  loading.value = true
  errorMessage.value = ''
  feedback.value = ''
  try {
    const currentSession = await apiClient.getSession(sessionController.signal)
    if (sequence !== sessionSequence) return
    if (!Array.isArray(currentSession.scopes) && currentSession.role !== 'host' && currentSession.role !== 'red' && currentSession.role !== 'blue') throw new Error('服务未返回可识别的会话范围')
    if (!gameIdDraft.value && typeof currentSession.game_id === 'string') gameIdDraft.value = currentSession.game_id
    session.value = currentSession
    if (isValidGameId.value && (currentSession.role === 'host' || currentSession.role === 'red' || currentSession.role === 'blue')) await loadView()
  } catch (error) {
    if (sequence !== sessionSequence) return
    session.value = null
    view.value = null
    apiClient.setAccessToken(null)
    errorTitle.value = '会话连接失败'
    showError(error)
  } finally { if (sequence === sessionSequence) loading.value = false }
}

async function loadView() {
  if (!isValidGameId.value || !apiClient.hasAccessToken) return
  readController?.abort()
  readController = new AbortController()
  const sequence = ++readSequence
  loading.value = true
  errorMessage.value = ''
  try {
    const nextView = await apiClient.getGameView(gameIdDraft.value.trim(), readController.signal)
    if (sequence !== readSequence) return
    if (!nextView.context || !nextView.clock || !nextView.capabilities || !nextView.save_status || !nextView.service_status || !Array.isArray(nextView.tasks) || !Array.isArray(nextView.units) || nextView.context.game_id !== gameIdDraft.value.trim() || nextView.role !== session.value?.role) throw new Error('服务返回的对局或席位与当前会话不一致')
    if (view.value?.context.snapshot_id !== nextView.context.snapshot_id) invalidateTaskPreview()
    view.value = nextView
    if (session.value?.role === 'host') await loadSaves(nextView.context.game_id)
    feedback.value = `已读取同一快照：第 ${nextView.clock.simulation_step} 步。`
  } catch (error) {
    if (sequence === readSequence) {
      errorTitle.value = feedback.value.startsWith('命令已完成') ? '命令已完成，最新视图读取失败' : '角色视图读取失败'
      showError(error)
    }
  } finally { if (sequence === readSequence) loading.value = false }
}

async function loadSaves(gameId: string) {
  try {
    const result = await apiClient.getSaves(gameId)
    if (gameId === view.value?.context.game_id) saves.value = result.items
  } catch (error) {
    saves.value = []
    errorTitle.value = '存档目录读取失败'
    showError(error)
  }
}

async function sendCommand(commandType: 'pause' | 'continue' | 'advance_step' | 'create_save' | 'restore_save' | 'set_ready' | 'start_game' | 'set_tasks' | 'confirm_initial_plan', providedPayload?: Record<string, unknown>) {
  if (!view.value || !canCommand(commandType)) return
  const basedOn = view.value.context
  const sequence = sessionSequence
  commandPending.value = true
  errorMessage.value = ''
  feedback.value = ''
  try {
    const payload = providedPayload || (commandType === 'create_save' ? { display_name: saveNameDraft.value } : commandType === 'set_ready' ? { ready: true } : {})
    const receipt = await apiClient.sendCommand(basedOn.game_id, basedOn, commandType, payload)
    if (sequence !== sessionSequence) return
    if (receipt.status === 'succeeded') {
      feedback.value = '命令已完成，正在重新读取服务端角色视图。'
      await loadView()
    } else if (receipt.status === 'accepted' || receipt.status === 'running') {
      pendingCommand.value = { gameId: basedOn.game_id, branchId: basedOn.branch_id, requestKey: receipt.request_key }
      feedback.value = `命令已受理，尚未完成。命令标识：${receipt.command_id}。请按原请求键核对结果。`
    } else {
      errorTitle.value = '命令未成功执行'
      errorMessage.value = receipt.error?.message || '服务没有返回成功收据。'
    }
  } catch (error) {
    if (sequence !== sessionSequence) return
    if (error instanceof ApiClientError && error.pendingCommand) pendingCommand.value = error.pendingCommand
    errorTitle.value = error instanceof ApiClientError && error.details.outcome === 'unknown' ? '命令结果待核对' : '命令未成功执行'
    showError(error)
  }
  finally { if (sequence === sessionSequence) commandPending.value = false }
}

async function verifyPendingCommand() {
  if (!pendingCommand.value || commandPending.value) return
  const sequence = sessionSequence
  commandPending.value = true
  try {
    const receipt = await apiClient.getCommandResult(pendingCommand.value.gameId, pendingCommand.value.branchId, pendingCommand.value.requestKey)
    if (sequence !== sessionSequence) return
    if (receipt.status === 'succeeded') {
      pendingCommand.value = null
      errorMessage.value = ''
      feedback.value = '原命令已有成功收据，正在读取最新角色视图。'
      await loadView()
    } else if (receipt.status === 'accepted' || receipt.status === 'running') {
      errorTitle.value = '命令仍在处理'
      errorMessage.value = `原请求键 ${pendingCommand.value.requestKey} 尚未完成。`
    } else {
      pendingCommand.value = null
      errorTitle.value = '命令未成功执行'
      errorMessage.value = receipt.error?.message || '服务返回未成功收据。'
    }
  } catch (error) {
    if (sequence !== sessionSequence) return
    errorTitle.value = '命令结果仍待核对'
    showError(error)
  } finally { if (sequence === sessionSequence) commandPending.value = false }
}

watch(() => [props.gameId, props.role] as const, ([nextId, nextRole]) => {
  if (nextId === (gameIdDraft.value || null) && nextRole === requestedRole.value) return
  readController?.abort()
  ++readSequence
  taskPreview.value = null
  detailsOpen.value = false
  taskEditorOpen.value = false
  enterLocalGame()
})
onBeforeUnmount(() => { readController?.abort(); ++readSequence; sessionController?.abort(); ++sessionSequence })
onMounted(enterLocalGame)
</script>

<template>
  <main class="formal-page">
    <ServiceGridMap v-if="session && view" :view="view" />
    <header class="formal-topbar"><div class="brand-mark"><span>✣</span><div><strong>穹图 <small>QIONG TU</small></strong><small>红蓝协同推演 · 服务端对局</small></div></div><nav><button type="button" class="nav-active">实时沙盘</button><button type="button" @click="emit('history')">历史与快照</button><button type="button" @click="emit('back')">返回首页</button></nav><div class="top-role"><span class="service-dot">● 服务在线</span><button v-for="seat in (['host', 'red', 'blue'] as Role[])" :key="seat" type="button" :class="{ active: session?.role === seat }" :disabled="loading || commandPending || session?.role === seat" @click="emit('selectRole', seat)">{{ seat === 'host' ? '主持人' : seat === 'red' ? '红方' : '蓝方' }}</button></div></header>
    <p v-if="!session && loading" class="entry-loading" role="status">正在进入服务端对局…</p>
    <template v-if="session && view">
      <section class="battle-hud">
        <div class="scenario-card"><small>SCENARIO / {{ view.game.game_no }}</small><strong>{{ view.game.title }}</strong><span>{{ view.game.is_demo ? '固定演示场景' : '首版正式场景' }} · {{ roleLabel }}</span></div>
        <div v-for="task in visibleTasks" :key="String(task.task_id)" :class="['mission-card', task.side]"><small>{{ sideLabel(task.side) }}任务 · {{ taskTypeLabel(task.task_type) }}</small><strong>{{ taskNodes(task).length ? `${completedNodeCount(task)} / ${taskNodes(task).length} 节点` : stateLabel(task.status) }}</strong><div v-if="taskNodes(task).length" class="mission-progress"><span :style="{ width: `${taskProgress(task)}%` }"></span></div><span>{{ targetLabel(task.target_ids) }}</span></div>
        <div class="clock-card"><small>仿真时间 · 每步 {{ view.clock.step_minutes ?? '未返回' }} 分钟</small><strong>{{ view.clock.simulation_step }} / {{ view.clock.deadline_step ?? '—' }}</strong><span>{{ view.clock.phase === 'finished' ? '已结束' : remainingMinutes !== null ? `剩余 ${remainingMinutes} 分钟 · ${phaseLabel(view.clock.phase)}` : `阶段：${phaseLabel(view.clock.phase)}` }}</span></div>
      </section>
      <aside class="battle-tools"><button type="button" :class="{ active: detailsOpen }" @click="toggleDetails">▤ 任务态势</button><button v-if="session.role === 'host' && view.clock.phase === 'preparing' && canCommand('set_tasks')" type="button" :class="{ active: taskEditorOpen }" @click="toggleTaskEditor">▦ 设置任务</button><button v-if="session.role === 'host'" type="button" @click="sendCommand('start_game')" :disabled="!canCommand('start_game')">▶ 开始对局</button><button v-if="session.role === 'host'" type="button" @click="sendCommand('pause')" :disabled="!canCommand('pause')">Ⅱ 暂停</button><button v-if="session.role === 'host'" type="button" @click="sendCommand('continue')" :disabled="!canCommand('continue')">▶ 继续</button><button v-if="session.role === 'host'" type="button" @click="sendCommand('advance_step')" :disabled="!canCommand('advance_step')">▸ 推进一步</button><button type="button" @click="loadView" :disabled="loading || !isValidGameId">↻ 刷新视图</button></aside>
      <section class="map-status-card"><span class="status-icon">◇</span><div><small>当前任务</small><strong>{{ visibleTasks.length ? visibleTasks.map(task => `${sideLabel(task.side)}${taskTypeLabel(task.task_type)}`).join(' · ') : '暂无任务' }}</strong><p>{{ view.clock.pause_reasons.length ? view.clock.pause_reasons.join('、') : `${roleLabel} · ${ownedUnits.length + ownedCamps.length} 个服务端对象` }}</p><button type="button" @click="toggleDetails">查看任务详情 →</button></div></section>
      <div class="service-compass" aria-label="地图北向">N <strong>▲</strong> 北</div>
      <aside v-if="detailsOpen" class="battle-side-panel" aria-label="服务端对局详情"><div class="side-panel-head"><div><small>SERVER STATUS / {{ view.clock.phase }}</small><h2>任务态势</h2></div><button type="button" aria-label="关闭任务态势" @click="toggleDetails">×</button></div><div class="side-panel-body"><section><small>任务进度</small><div v-for="task in visibleTasks" :key="String(task.task_id)" class="side-row"><strong>{{ sideLabel(task.side) }} · {{ taskTypeLabel(task.task_type) }}</strong><span>{{ targetLabel(task.target_ids) }} · {{ stateLabel(task.status) }}</span><div v-for="node in taskNodes(task)" :key="String(node.node_id)" class="side-node"><span>{{ displayValue(node.target_id) }}</span><span>{{ stateLabel(node.status) }}</span></div></div></section><section><small>可见单位</small><div v-for="unit in ownedUnits" :key="String(unit.unit_id)" class="side-row"><strong>{{ sideLabel(unit.side) }} · {{ unitTypeLabel(unit.unit_type) }}</strong><span>{{ positionLabel(unit.position) }}</span></div></section><section><small>资源与准备</small><div v-for="resource in ownedResources" :key="String(resource.side)" class="side-row"><strong>{{ sideLabel(resource.side) }}资源</strong><span>已执行 {{ displayValue(resource.executed_resource_commands) }} · 剩余 {{ displayValue(resource.remaining_resource_commands) }}</span></div><p v-if="session.role !== 'host'">本方准备：{{ ownReady === true ? '已完成' : ownReady === false ? '未完成' : '未返回' }}</p></section></div></aside>
      <div v-if="taskEditorOpen && session.role === 'host' && view.clock.phase === 'preparing'" class="backdrop" @click="toggleTaskEditor"></div>
      <aside v-if="taskEditorOpen && session.role === 'host' && view.clock.phase === 'preparing'" class="drawer task-drawer" aria-label="红蓝任务设置">
        <div class="drawer-head"><div><span class="eyebrow">MISSION SETUP / FORMAT CHECK</span><h2>设置红蓝双方任务</h2></div><button type="button" aria-label="关闭任务设置" @click="toggleTaskEditor">×</button></div>
        <div class="drawer-body">
          <p class="drawer-intro">每方输入一行任务，格式校验通过后才能提交。当前固定演示场景仅开放区域巡查任务。</p>
          <div class="task-format-guide"><strong>固定输入格式</strong><code>任务类型|目标|截止步数</code><p>使用半角竖线 <b>|</b> 分隔，不要输入说明文字。截止步数固定为 <b>60</b>。</p><div><span>区域巡查 / 物资护送</span><b>A-B-C 或 C-B-A</b></div><div><span>关键设施保护</span><b>设施01</b></div><div><span>受损设备撤离</span><b>撤离点</b></div></div>
          <div class="proposal-section"><small>01 / 红方任务</small><p class="current-task">当前：{{ taskTypeLabel(ownedTasks.find(task => task.side === 'red')?.task_type) }} · {{ targetLabel(ownedTasks.find(task => task.side === 'red')?.target_ids) }}</p><label class="task-input-label" for="service-red-task-input">任务指令</label><input id="service-red-task-input" v-model="taskDraft.red" type="text" :aria-invalid="!!taskPreview?.errors.red" :placeholder="taskFormatExample.red" @input="invalidateTaskPreview"><p class="input-example">示例：{{ taskFormatExample.red }}</p><p v-if="taskPreview?.errors.red" class="input-error" role="alert">{{ taskPreview.errors.red }}</p></div>
          <div class="proposal-section"><small>02 / 蓝方任务</small><p class="current-task">当前：{{ taskTypeLabel(ownedTasks.find(task => task.side === 'blue')?.task_type) }} · {{ targetLabel(ownedTasks.find(task => task.side === 'blue')?.target_ids) }}</p><label class="task-input-label" for="service-blue-task-input">任务指令</label><input id="service-blue-task-input" v-model="taskDraft.blue" type="text" :aria-invalid="!!taskPreview?.errors.blue" :placeholder="taskFormatExample.blue" @input="invalidateTaskPreview"><p class="input-example">示例：{{ taskFormatExample.blue }}</p><p v-if="taskPreview?.errors.blue" class="input-error" role="alert">{{ taskPreview.errors.blue }}</p></div>
          <p v-if="taskPreview?.errors.shared" class="input-error shared-error" role="alert">{{ taskPreview.errors.shared }}</p>
          <p v-if="errorMessage" class="input-error shared-error" role="alert">{{ errorMessage }}</p>
          <div v-if="taskPreview?.valid && taskPreview.parsed_tasks" class="task-check-result"><strong>✓ 格式校验通过</strong><p>红方：{{ taskTypeLabel(taskPreview.parsed_tasks.red.task_type) }} · {{ targetLabel(taskPreview.parsed_tasks.red.target_ids) }}</p><p>蓝方：{{ taskTypeLabel(taskPreview.parsed_tasks.blue.task_type) }} · {{ targetLabel(taskPreview.parsed_tasks.blue.target_ids) }}</p><small>校验结果绑定当前服务端快照；修改输入后需要重新校验。</small></div>
        </div>
        <div class="drawer-actions"><button type="button" class="secondary-button" @click="toggleTaskEditor">取消</button><button type="button" class="secondary-button" :disabled="previewPending || commandPending || !taskDraft.red.trim() || !taskDraft.blue.trim()" @click="previewTaskConfiguration">{{ previewPending ? '校验中…' : '校验格式' }}</button><button type="button" class="primary-button" :disabled="!taskPreview?.valid || !taskPreview.preview_token || previewSnapshotId !== view.context.snapshot_id || previewPending || commandPending" @click="applyTaskConfiguration">应用任务</button></div>
      </aside>
      <section class="bottom-status"><span>服务端角色视图 · 快照 {{ view.context.snapshot_id }}</span><span>{{ view.observed_at }}</span><template v-if="session.role === 'host'"><input v-model="saveNameDraft" class="quick-save-input" maxlength="120" placeholder="存档名称"><button type="button" :disabled="!canCommand('create_save')" @click="sendCommand('create_save')">保存快照</button></template><span v-if="feedback" class="feedback-text">{{ feedback }}</span></section>
    </template>
    <div v-if="errorMessage" class="formal-feedback error" role="alert"><strong>{{ errorTitle }}</strong><p>{{ errorMessage }}</p><button v-if="!session && !loading" type="button" @click="enterLocalGame">重试进入</button><button v-if="pendingCommand" type="button" :disabled="commandPending" @click="verifyPendingCommand">核对原命令结果</button></div>
    <p v-else-if="!session && !loading" class="entry-loading">服务端对局暂未连接，请重试进入。</p>
  </main>
</template>

<style scoped>
:global(html),:global(body),:global(#app){margin:0;min-height:100%;overflow:hidden}
.formal-page{position:relative;height:100vh;min-height:650px;overflow:hidden;background:#071923;color:#f4f6f2}
.formal-topbar{position:absolute;z-index:5;top:0;left:0;right:0;height:64px;padding:0 20px;display:flex;align-items:center;gap:35px;background:linear-gradient(180deg,#0b2130f7,#102c3bdc);border-bottom:1px solid #9ab5bd55;box-shadow:0 6px 18px #07172160}
.brand-mark{display:flex;align-items:center;gap:10px;min-width:235px}.brand-mark>span{width:34px;height:34px;display:grid;place-items:center;border:1px solid #b6d1d655;border-radius:8px;background:#ffffff14;color:#eef8f2;font-size:23px}.brand-mark strong{display:block;font-size:17px;letter-spacing:1px}.brand-mark strong small{font-size:9px;letter-spacing:2px;color:#a7c0c5;margin-left:5px}.brand-mark div>small{font-size:9px;color:#a5bfc3}
.formal-topbar nav{display:flex;gap:3px;height:100%;align-items:center}.formal-topbar nav button,.top-role button{height:38px;border:1px solid transparent;border-radius:5px;background:transparent;color:#cfdee0;padding:0 12px;font-size:12px;font-weight:700;cursor:pointer}.formal-topbar nav button:hover,.formal-topbar nav button.nav-active,.top-role button.active{color:#fff;background:#ffffff1c;border-color:#d0e0e13d}.formal-topbar nav button.nav-active{box-shadow:inset 0 -3px #e5a545}.top-role{margin-left:auto;display:flex;align-items:center;gap:4px}.service-dot{margin-right:10px;color:#d9b266;font-size:10px}
.entry-loading{position:absolute;z-index:4;left:50%;top:50%;transform:translate(-50%,-50%);color:#d9e8e8;font-size:14px}.battle-hud{position:absolute;z-index:4;top:77px;left:18px;right:18px;display:flex;align-items:stretch;gap:10px;pointer-events:none}.battle-hud>*{pointer-events:auto}.scenario-card,.mission-card,.clock-card{border:1px solid #a8c4c870;border-radius:7px;background:#0b2635e9;box-shadow:0 7px 18px #05121b6e;backdrop-filter:blur(8px);color:#f4f6f5}.scenario-card{width:220px;min-width:220px;display:flex;flex-direction:column;justify-content:center;gap:4px;padding:9px 13px;border-left:3px solid #d8a457}.scenario-card small,.clock-card small,.mission-card small{font-size:9px;color:#a9c4c8;letter-spacing:.8px}.scenario-card strong{font-size:16px}.scenario-card span,.mission-card span,.clock-card span{font-size:10px;color:#d5d3be}.mission-card{width:190px;min-height:61px;display:flex;flex-direction:column;gap:4px;padding:10px 11px;border-left:3px solid #e8787f}.mission-card.blue{border-left-color:#6ab5ed}.mission-card strong{font-size:11px;color:#ffc0bf}.mission-card.blue strong{color:#b4ddfa}.mission-progress{height:4px;border-radius:4px;background:#718b9270;overflow:hidden}.mission-progress span{display:block;height:100%;background:#e8787f}.mission-card.blue .mission-progress span{background:#6ab5ed}.clock-card{margin-left:auto;width:175px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;padding:7px 11px;text-align:center}.clock-card strong{font-size:23px;line-height:1;color:#fff;font-variant-numeric:tabular-nums}.clock-card span{color:#d6c391}
.battle-tools{position:absolute;z-index:4;top:166px;left:17px;display:flex;flex-direction:column;gap:5px}.battle-tools button{width:72px;min-height:44px;padding:0 3px;border:1px solid #9dbac25d;border-radius:6px;background:#0b2c3be8;color:#d8e8e7;font-size:9px;font-weight:700;line-height:1.2;white-space:nowrap;cursor:pointer;box-shadow:0 5px 12px #05131b55}.battle-tools button:hover:not(:disabled),.battle-tools button.active{background:#17465a;color:#fff;border-color:#e1b56a}.battle-tools button:disabled{opacity:.5;cursor:not-allowed}
.map-status-card{position:absolute;z-index:4;left:88px;bottom:46px;width:255px;display:flex;align-items:flex-start;gap:12px;padding:14px;border:1px solid #d5b06d;border-left:3px solid #e0af4d;border-radius:7px;background:#0b2a39e8;box-shadow:0 8px 19px #04141d8c}.status-icon{width:32px;height:32px;display:grid;place-items:center;border:1px solid #e5c16f;border-radius:5px;background:#e4bd6555;color:#fff0bd;font-size:19px}.map-status-card small{display:block;color:#a8c0c4;font-size:9px}.map-status-card strong{display:block;color:#f4f5e6;font-size:12px;margin:4px 0}.map-status-card p{margin:0;color:#b8cdd0;font-size:10px;line-height:1.5}.map-status-card button{margin-top:8px;padding:0;border:0;background:transparent;color:#f3ca84;font-size:10px;font-weight:700}.service-compass{position:absolute;z-index:4;right:22px;top:165px;width:48px;padding:8px 0;border:1px solid #a9c4c57a;border-radius:6px;background:#0b2937da;color:#e8f4f1;display:flex;flex-direction:column;align-items:center;gap:2px;font-size:10px}.service-compass strong{font-size:18px}
.battle-side-panel{position:absolute;z-index:4;right:18px;top:166px;bottom:46px;width:310px;display:flex;flex-direction:column;overflow:hidden;border:1px solid #d0dcd8;border-radius:9px;background:#f8faf7;box-shadow:0 15px 45px #03131dd9;color:#243c4e}.side-panel-head{min-height:66px;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 16px;background:#102f40;color:#fff}.side-panel-head small{font-size:9px;letter-spacing:1px;color:#aec9cf}.side-panel-head h2{margin:3px 0 0;font-size:18px;color:#fff}.side-panel-head button{width:29px;height:29px;border:1px solid #d4e6e680;border-radius:4px;background:#ffffff1a;color:#fff;font-size:22px;line-height:1}.side-panel-body{flex:1;overflow:auto;padding:15px}.battle-side-panel section{padding:12px 0;border-bottom:1px solid #dfe8eb}.battle-side-panel section:first-child{padding-top:0}.battle-side-panel section>small{display:block;color:#6e8799;font-size:10px;font-weight:700;letter-spacing:.7px;margin-bottom:8px}.side-row{display:flex;flex-direction:column;gap:4px;padding:8px 0;border-bottom:1px solid #e4ebed}.side-row:last-child{border-bottom:0}.side-row strong{font-size:11px;color:#29465b}.side-row>span,.battle-side-panel p{margin:0;color:#6f8493;font-size:10px;line-height:1.5}.side-node{display:flex;justify-content:space-between;padding:3px 8px;border-radius:4px;background:#edf3f4;font-size:10px;color:#4c697e}
.task-drawer .current-task{margin-top:12px;color:#50718d;font-weight:700}
.bottom-status{position:absolute;z-index:5;left:0;right:0;bottom:0;height:34px;display:flex;align-items:center;gap:17px;padding:0 30px;background:#071c28e8;border-top:1px solid #8eaab25a;color:#9ab1b8;font-size:10px}.bottom-status span:first-child{color:#b5d0cf}.quick-save-input{margin-left:auto;width:125px;border:1px solid #87a6af66;border-radius:4px;padding:5px 7px;background:#0d2b38cc;color:#e5efec;font-size:10px}.bottom-status button{border:1px solid #d4b16b;border-radius:4px;padding:5px 8px;background:#3b3a29aa;color:#f2e1b7;font-size:10px}.feedback-text{color:#d8c18d!important}
.formal-feedback{position:absolute;z-index:7;left:50%;bottom:55px;transform:translateX(-50%);max-width:620px;padding:12px 16px;border:1px solid #c88773;border-radius:6px;background:#162b37f2;color:#f6e7e2;font-size:11px}.formal-feedback p{display:inline;margin:0 0 0 8px}.formal-feedback button{margin-left:10px;border:1px solid #d7b16f;background:#3a3628;color:#f4e4bb;border-radius:4px;padding:5px 8px;font-size:10px}
@media(max-width:1100px){.battle-side-panel{right:10px;width:300px}}
@media(max-width:900px){.formal-topbar{gap:8px;padding:0 10px}.brand-mark{min-width:160px}.formal-topbar nav button{padding:0 7px}.battle-hud{left:82px;right:10px;gap:6px}.scenario-card{min-width:170px;width:170px}.mission-card{width:150px}.clock-card{width:135px}.map-status-card{left:82px}}
@media(max-width:650px){.brand-mark{min-width:120px}.brand-mark strong{font-size:13px}.brand-mark div>small,.formal-topbar nav button:not(.nav-active){display:none}.service-dot{display:none}.battle-hud{top:75px;left:77px}.scenario-card{min-width:145px;width:145px}.mission-card{display:none}.clock-card{width:120px}.battle-tools{left:9px}.map-status-card{left:76px;bottom:47px;width:220px}.battle-side-panel{right:7px;top:155px;width:205px}.bottom-status{padding:0 9px}.bottom-status span:nth-child(2),.quick-save-input{display:none}}
</style>
