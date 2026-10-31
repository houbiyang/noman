<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import BattleMap from './BattleMap.vue'
import { taskFormatExample, taskNodeLabels, taskTargetLabel, taskTitle, validateTaskPair, type SideTask, type TaskErrors, type TaskPair } from './taskFormat'
import HomePage from './pages/HomePage.vue'
import FormalSandboxPage from './pages/FormalSandboxPage.vue'
import HistoryPage from './pages/HistoryPage.vue'
import DemoSavePage from './pages/DemoSavePage.vue'
import ResultPage from './pages/ResultPage.vue'
import ReplayPage from './pages/ReplayPage.vue'
import { createDemoSave, isDemoSyncState, readDemoSaves, type DemoRecordItem, type DemoSyncState, type DemoUnit } from './shared/demoSaves'
import { navigateTo, parseNavigation, type NavigationState } from './shared/navigation'

type Role = 'host' | 'red' | 'blue'
type Drawer = 'suggestion' | 'event' | 'tasks' | null
type HudPanel = 'status' | 'units' | 'timeline' | 'catalog' | null
type EventType = '设备故障' | '通信中断' | '天气变化' | '任务变化'
type TroopType = '侦察无人机' | '陆兵' | '保障单位'
type Unit = DemoUnit
type RecordItem = DemoRecordItem

const queryRole = new URLSearchParams(location.search).get('role')
const role = ref<Role>(queryRole === 'red' || queryRole === 'blue' ? queryRole : 'host')
const navigation = ref<NavigationState>(parseNavigation())
const currentStep = ref(18)
const paused = ref(true)
const drawer = ref<Drawer>(null)
const activePanel = ref<HudPanel>(null)
const preparationPreview = ref(false)
const selectedTroopType = ref<TroopType | null>(null)
const previewUnits = ref<Unit[]>([])
const selectedUnitId = ref<string | null>(null)
const selectedRecordId = ref<number | null>(null)
const pending = ref<Record<'red' | 'blue', boolean>>({ red: true, blue: false })
const suggestionResult = ref<Record<'red' | 'blue', string>>({ red: '', blue: '第 7 步已确认' })
const tasks = ref<TaskPair>({
  red: { type: '区域巡查', target: 'A-B-C', deadlineStep: 60 },
  blue: { type: '区域巡查', target: 'C-B-A', deadlineStep: 60 },
})
const taskProgress = ref<Record<'red' | 'blue', number>>({ red: 1, blue: 2 })
const taskChangePending = ref<Record<'red' | 'blue', boolean>>({ red: false, blue: false })
const taskChangeContent = ref<Record<'red' | 'blue', boolean>>({ red: false, blue: false })
const taskChangeRejected = ref<Record<'red' | 'blue', boolean>>({ red: false, blue: false })
const taskDraft = ref<Record<'red' | 'blue', string>>({ ...taskFormatExample })
const taskErrors = ref<TaskErrors>({ red: '', blue: '', shared: '' })
const checkedTasks = ref<TaskPair | null>(null)
const editing = ref(false)
const assignment = ref('陆兵巡查 B 区；侦察无人机观察区域并报告已发现信息；保障单位待命')
const priority = ref('优先完成 B 区节点')
const extraResources = ref(2)
const eventType = ref<EventType>('设备故障')
const affectedSide = ref<'red' | 'blue'>('red')
const affectedUnit = ref('R-02')
const eventStep = ref(18)
const previewed = ref(false)
const notice = ref('')
const saveError = ref('')
let noticeTimer: ReturnType<typeof setTimeout> | undefined
let nextRecordId = 8
const demoStorageKey = 'qiongtu-demo-prototype-v2'

const units = ref<Unit[]>([
  { id: 'R-01', side: 'red', name: '侦察无人机', state: '在线', x: 254, y: 212, shape: '◆' },
  { id: 'R-02', side: 'red', name: '陆兵', state: '执行中', x: 376, y: 323, shape: '●' },
  { id: 'R-03', side: 'red', name: '保障单位', state: '待命', x: 198, y: 405, shape: '■' },
  { id: 'B-01', side: 'blue', name: '侦察无人机', state: '在线', x: 745, y: 210, shape: '◆' },
  { id: 'B-02', side: 'blue', name: '陆兵', state: '执行中', x: 640, y: 343, shape: '●' },
  { id: 'B-03', side: 'blue', name: '保障单位', state: '待命', x: 806, y: 410, shape: '■' },
])
const faultEndSteps = ref<Record<string, number>>({})
const records = ref<RecordItem[]>([
  { id: 1, step: 0, title: '区域巡查任务开始', detail: '双方对称初始条件', visibility: 'public', kind: 'task' },
  { id: 2, step: 5, title: '首次方案确认', detail: '红方操作员确认', visibility: 'red', kind: 'decision' },
  { id: 3, step: 7, title: '首次方案确认', detail: '蓝方操作员确认', visibility: 'blue', kind: 'decision' },
  { id: 4, step: 11, title: '公共区域更新', detail: '北侧道路开放', visibility: 'public', kind: 'event' },
  { id: 5, step: 15, title: '巡查节点 A 完成', detail: '红方节点进度更新', visibility: 'red', kind: 'task' },
  { id: 6, step: 16, title: '天气变化', detail: '区域能见度下降', visibility: 'public', kind: 'event' },
  { id: 7, step: 18, title: '任务调整建议', detail: '红方等待人工确认', visibility: 'red', kind: 'decision' },
])

const roleName = computed(() => role.value === 'host' ? '中立主持人' : role.value === 'red' ? '红方操作员' : '蓝方操作员')
const visibleUnits = computed(() => {
  const allUnits = preparationPreview.value ? [...units.value, ...previewUnits.value] : units.value
  return role.value === 'host' ? allUnits : allUnits.filter(unit => unit.side === role.value)
})
const troopCatalog: { name: TroopType; shape: string; description: string }[] = [
  { name: '侦察无人机', shape: '◆', description: '区域观察与信息报告' },
  { name: '陆兵', shape: '●', description: '区域任务' },
  { name: '保障单位', shape: '■', description: '后勤支援' },
]
const previewUnitCount = computed(() => role.value === 'host' ? previewUnits.value.length : previewUnits.value.filter(unit => unit.side === role.value).length)
const selectedUnit = computed(() => visibleUnits.value.find(unit => unit.id === selectedUnitId.value))
const visibleRecords = computed(() => records.value.filter(item => item.visibility === 'public' || role.value === 'host' || item.visibility === role.value))
const selectedRecord = computed(() => visibleRecords.value.find(item => item.id === selectedRecordId.value))
const waitingSide = computed(() => pending.value.red && pending.value.blue ? '红蓝双方' : pending.value.red ? '红方' : pending.value.blue ? '蓝方' : '')
const statusUrgent = computed(() => role.value === 'host' ? !!waitingSide.value : pending.value[role.value])
const statusText = computed(() => {
  if (preparationPreview.value) return '部署界面预览'
  if (role.value === 'host' && waitingSide.value) return `等待${waitingSide.value}确认`
  if (role.value !== 'host' && pending.value[role.value]) return '等待本方确认'
  return paused.value ? '推演已暂停' : '演示运行中'
})
const activeSide = computed<'red' | 'blue'>(() => role.value === 'blue' ? 'blue' : 'red')
const currentTask = computed(() => tasks.value[activeSide.value])
const currentTaskNodes = computed(() => taskNodeLabels(currentTask.value))
const remainingMinutes = computed(() => Math.max(0, 60 - currentStep.value))
const unitGroups = computed(() => troopCatalog.map(troop => ({
  ...troop,
  members: visibleUnits.value.filter(unit => unit.name === troop.name),
})))
const latestPublicEvent = computed(() => [...visibleRecords.value].reverse().find(item => item.visibility === 'public' && item.kind === 'event'))
const effect = computed(() => ({
  '设备故障': '指定单位暂停 5 步，恢复时记录时间线。',
  '通信中断': '仅记录通信中断演示事件；报告未知状态尚未模拟。',
  '天气变化': '天气事件对双方公开；任务进度延迟尚未模拟。',
  '任务变化': '仅记录任务变化演示事件；目标替换与待确认流程尚未模拟。',
})[eventType.value])
watch(affectedSide, side => {
  affectedUnit.value = side === 'red' ? 'R-02' : 'B-02'
  previewed.value = false
})

function notify(message: string) {
  notice.value = message
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value = '' }, 4000)
}
function restoreDemoState(state: DemoSyncState) {
  currentStep.value = state.currentStep
  paused.value = state.paused
  tasks.value = state.tasks
  taskProgress.value = state.taskProgress
  pending.value = state.pending
  taskChangePending.value = state.taskChangePending
  taskChangeContent.value = state.taskChangeContent
  taskChangeRejected.value = state.taskChangeRejected
  suggestionResult.value = state.suggestionResult
  units.value = state.units
  previewUnits.value = state.previewUnits
  faultEndSteps.value = state.faultEndSteps
  records.value = state.records
  nextRecordId = state.nextRecordId
  if (pending.value.red || pending.value.blue) paused.value = true
}
function collectDemoState(): DemoSyncState {
  return {
    currentStep: currentStep.value,
    paused: paused.value,
    tasks: tasks.value,
    taskProgress: taskProgress.value,
    pending: pending.value,
    taskChangePending: taskChangePending.value,
    taskChangeContent: taskChangeContent.value,
    taskChangeRejected: taskChangeRejected.value,
    suggestionResult: suggestionResult.value,
    units: units.value,
    previewUnits: previewUnits.value,
    faultEndSteps: faultEndSteps.value,
    records: records.value,
    nextRecordId,
  }
}
function publishDemoState() {
  window.localStorage.setItem(demoStorageKey, JSON.stringify(collectDemoState()))
}
function receiveStoredDemoState(event: StorageEvent) {
  if (event.key !== demoStorageKey) return
  if (event.newValue === null) {
    window.location.reload()
    return
  }
  try {
    const state: unknown = JSON.parse(event.newValue)
    if (!isDemoSyncState(state)) throw new Error('演示同步状态结构无效')
    restoreDemoState(state)
  } catch {
    notify('收到的演示状态无法读取，请重置演示')
  }
}
onMounted(() => {
  window.addEventListener('popstate', handleNavigationChange)
  const savedState = window.localStorage.getItem(demoStorageKey)
  if (savedState) {
    try {
      const state: unknown = JSON.parse(savedState)
      if (!isDemoSyncState(state)) throw new Error('演示同步状态结构无效')
      restoreDemoState(state)
    } catch {
      window.localStorage.removeItem(demoStorageKey)
    }
  }
  window.addEventListener('storage', receiveStoredDemoState)
  const currentUrl = new URL(window.location.href)
  if (currentUrl.searchParams.has('mode') || currentUrl.searchParams.get('view') === 'sandbox') navigateTo({ view: navigation.value.view, gameId: navigation.value.view === 'home' ? null : navigation.value.gameId }, true)
})
onUnmounted(() => {
  window.removeEventListener('popstate', handleNavigationChange)
  window.removeEventListener('storage', receiveStoredDemoState)
  if (noticeTimer) clearTimeout(noticeTimer)
})

function handleNavigationChange() {
  const next = parseNavigation()
  navigation.value = next
  if (next.role !== role.value) role.value = next.role
  drawer.value = null
  activePanel.value = null
  selectedUnitId.value = null
  selectedRecordId.value = null
}

function navigatePage(view: NavigationState['view'], gameId?: string, step?: number, snapshotId?: string, branchId?: string) {
  navigateTo({ view, gameId: gameId || null, branchId: branchId || null, snapshotId: snapshotId || null, step: view === 'replay' ? step ?? 0 : null, role: role.value })
}

function startBattle() {
  navigateTo({ view: 'live', gameId: null, step: null, role: 'host' })
}

function saveAndExitDemo() {
  if (role.value !== 'host') return
  paused.value = true
  try {
    publishDemoState()
    createDemoSave(collectDemoState())
    saveError.value = ''
    navigatePage('home')
  } catch (error) {
    notify(error instanceof Error ? error.message : '演示存档未写入，仍留在当前对局')
  }
}

function loadDemoSave(saveId: string) {
  try {
    const save = readDemoSaves().find(item => item.save_id === saveId)
    if (!save) throw new Error('所选存档不存在，请重新打开存档列表')
    restoreDemoState({ ...save.state, paused: true })
    preparationPreview.value = false
    publishDemoState()
    saveError.value = ''
    navigateTo({ view: 'demo', gameId: null, step: null, role: 'host' })
  } catch (error) {
    saveError.value = error instanceof Error ? error.message : '本机演示存档读取失败'
  }
}
function taskLine(task: SideTask): string {
  return `${task.type}|${task.target}|${task.deadlineStep}`
}
function openTaskSettings() {
  if (role.value !== 'host') return
  taskDraft.value = { red: taskLine(tasks.value.red), blue: taskLine(tasks.value.blue) }
  taskErrors.value = { red: '', blue: '', shared: '' }
  checkedTasks.value = null
  drawer.value = 'tasks'
}
function invalidateTaskCheck(side: 'red' | 'blue') {
  checkedTasks.value = null
  taskErrors.value[side] = ''
  taskErrors.value.shared = ''
}
function checkTaskFormat() {
  const result = validateTaskPair(taskDraft.value.red, taskDraft.value.blue)
  taskErrors.value = result.errors
  checkedTasks.value = result.tasks
  if (result.tasks) notify('格式校验通过，可应用红蓝任务')
}
function applyTasks() {
  if (role.value !== 'host' || !checkedTasks.value) return
  const result = validateTaskPair(taskDraft.value.red, taskDraft.value.blue)
  taskErrors.value = result.errors
  if (!result.tasks) {
    checkedTasks.value = null
    return
  }
  const validTasks = result.tasks
  const changedSides = (['red', 'blue'] as const).filter(side => taskLine(tasks.value[side]) !== taskLine(validTasks[side]) || taskChangeRejected.value[side])
  if (changedSides.length === 0) return notify('任务与当前配置相同，无需重复应用')
  tasks.value = result.tasks
  for (const side of changedSides) {
    taskProgress.value[side] = 0
    pending.value[side] = true
    taskChangePending.value[side] = true
    taskChangeContent.value[side] = true
    taskChangeRejected.value[side] = false
    suggestionResult.value[side] = ''
    addRecord('主持人设置新任务', `${result.tasks[side].type} · ${taskTargetLabel(result.tasks[side].target)} · 截止第 60 步（演示）`, side, 'task')
  }
  paused.value = true
  drawer.value = null
  publishDemoState()
  notify(`已设置${changedSides.map(side => side === 'red' ? '红方' : '蓝方').join('、')}任务；等待对应操作员确认`)
}
function changeRole(nextRole: Role) {
  role.value = nextRole
  drawer.value = null
  activePanel.value = null
  selectedUnitId.value = null
  selectedTroopType.value = null
  selectedRecordId.value = null
  navigateTo({ role: nextRole, view: navigation.value.view })
}
function togglePreparationPreview() {
  preparationPreview.value = !preparationPreview.value
  activePanel.value = null
  selectedTroopType.value = null
  selectedUnitId.value = null
  drawer.value = null
}
function togglePanel(panel: Exclude<HudPanel, null>) {
  activePanel.value = activePanel.value === panel ? null : panel
}
function selectMapUnit(unitId: string) {
  selectedUnitId.value = unitId
  activePanel.value = 'units'
}
function placePreviewUnit(position: { x: number; y: number }) {
  if (!preparationPreview.value || role.value === 'host' || !selectedTroopType.value) return
  const insideOwnArea = role.value === 'red'
    ? position.x >= 45 && position.x <= 420 && position.y >= 150 && position.y <= 495
    : position.x >= 580 && position.x <= 955 && position.y >= 150 && position.y <= 495
  if (!insideOwnArea) return notify('请选择地图上标出的本方部署区')
  if ([...units.value, ...previewUnits.value].some(unit => Math.hypot(unit.x - position.x, unit.y - position.y) < 35)) return notify('该位置已有单位，请换一个位置')
  const troop = troopCatalog.find(item => item.name === selectedTroopType.value)
  if (!troop) return
  const sideUnits = previewUnits.value.filter(unit => unit.side === role.value)
  previewUnits.value.push({ id: `${role.value === 'red' ? 'R' : 'B'}-P${String(sideUnits.length + 1).padStart(2, '0')}`, side: role.value, name: troop.name, state: '部署预览', x: Math.round(position.x), y: Math.round(position.y), shape: troop.shape })
  publishDemoState()
  notify(`${troop.name}已放入本方部署区；此操作仅供界面预览`)
}
function addRecord(title: string, detail: string, visibility: RecordItem['visibility'], kind: string) {
  records.value.push({ id: nextRecordId++, step: currentStep.value, title, detail, visibility, kind })
}
function advanceStep() {
  if (waitingSide.value) return notify(`等待${waitingSide.value}处理建议后才能推进`)
  if (currentStep.value >= 60) return notify('演示已到第 60 步')
  currentStep.value++
  for (const [unitId, endStep] of Object.entries(faultEndSteps.value)) {
    if (currentStep.value >= endStep) {
      const unit = units.value.find(item => item.id === unitId)
      if (unit) {
        unit.state = '在线'
        addRecord('设备恢复', `${unit.id} · 状态恢复（演示）`, unit.side, 'event')
      }
      delete faultEndSteps.value[unitId]
    }
  }
  paused.value = true
  publishDemoState()
  notify(`已演示推进至第 ${currentStep.value} 步`)
}
function togglePause() {
  if (waitingSide.value) return notify(`等待${waitingSide.value}处理建议后才能继续`)
  paused.value = !paused.value
  publishDemoState()
  notify(paused.value ? '演示已暂停' : '演示已继续；可使用“单步推进”查看下一步')
}
function decide(accepted: boolean) {
  if (role.value === 'host') return
  if (taskChangePending.value[role.value] && taskChangeRejected.value[role.value]) return
  if (accepted && editing.value && (!assignment.value.trim() || !priority.value.trim() || extraResources.value < 0 || extraResources.value > 20)) {
    notify('请填写分工和优先级，资源额度须为 0–20')
    return
  }
  if (taskChangePending.value[role.value] && !accepted) {
    taskChangeRejected.value[role.value] = true
    suggestionResult.value[role.value] = `第 ${currentStep.value} 步已驳回，等待主持人重新设置`
  } else {
    pending.value[role.value] = false
    taskChangePending.value[role.value] = false
    suggestionResult.value[role.value] = accepted ? `第 ${currentStep.value} 步已确认` : `第 ${currentStep.value} 步已驳回`
  }
  addRecord(accepted ? '操作员确认建议' : '操作员驳回建议', accepted ? '已记录本方处理结果（演示）' : '原建议未执行（演示）', role.value, 'decision')
  drawer.value = null
  editing.value = false
  publishDemoState()
  notify(accepted ? '已确认演示建议' : taskChangeRejected.value[role.value] ? '已驳回新任务建议，等待主持人重新设置' : '已记录驳回；原建议未执行')
}
function openEvent() {
  eventStep.value = currentStep.value
  previewed.value = false
  drawer.value = 'event'
}
function triggerEvent() {
  if (role.value !== 'host' || !previewed.value || eventStep.value !== currentStep.value) return
  const scope = eventType.value === '天气变化' ? '双方' : affectedSide.value === 'red' ? '红方' : '蓝方'
  addRecord(eventType.value, `${scope} · 主持人手动触发 · ${effect.value}`, eventType.value === '天气变化' ? 'public' : affectedSide.value, 'event')
  if (eventType.value === '设备故障') {
    const unit = units.value.find(item => item.id === affectedUnit.value)
    if (unit) {
      unit.state = '暂停 5 步'
      faultEndSteps.value[unit.id] = currentStep.value + 5
    }
  }
  drawer.value = null
  publishDemoState()
  notify(`${eventType.value}已加入演示时间线`)
}
function resetDemo() {
  window.localStorage.removeItem(demoStorageKey)
  window.location.reload()
}
</script>

<template>
  <main v-if="navigation.invalid" class="invalid-page"><h1>页面地址无效</h1><p>请检查页面参数。当前没有执行读档或其他业务操作。</p><button type="button" class="secondary-button" @click="navigatePage('home')">返回首页</button></main>
  <HomePage v-else-if="navigation.view === 'home'" @start="startBattle" @open-saves="navigatePage('history')" @open-demo-saves="navigatePage('demo-saves')" />
  <FormalSandboxPage v-else-if="navigation.view === 'live'" :game-id="navigation.gameId" :role="navigation.role" @back="navigatePage('home')" @history="navigatePage('history')" @entered="gameId => navigateTo({ view: 'live', gameId, role: navigation.role }, true)" @select-role="selectedRole => navigateTo({ view: 'live', gameId: navigation.gameId, role: selectedRole })" />
  <div v-else-if="navigation.view === 'demo'" :class="['app-shell', role]">
    <BattleMap
      :role="role"
      :preparation-preview="preparationPreview"
      :selected-troop-type="selectedTroopType"
      :selected-unit-id="selectedUnitId"
      :tasks="tasks"
      :units="visibleUnits"
      @select-unit="selectMapUnit"
      @place-preview="placePreviewUnit"
    />

    <header class="game-topbar">
      <div class="game-brand"><div class="brand-symbol">✣</div><div><strong>穹图 <small>QIONG TU</small></strong><span>红蓝协同推演 · 页面原型</span></div></div>
      <nav class="game-tabs" aria-label="主导航"><button type="button" @click="navigatePage('home')">首页</button><button :class="{ active: !preparationPreview }" type="button" @click="preparationPreview && togglePreparationPreview()">实时沙盘</button><button :class="{ active: preparationPreview }" type="button" @click="!preparationPreview && togglePreparationPreview()">开局部署预览</button><button type="button" @click="navigatePage('history')">演示存档</button></nav>
      <div class="game-role"><span class="game-demo">● 演示数据</span><div class="role-switch" aria-label="原型角色预览"><button v-for="option in (['host', 'red', 'blue'] as Role[])" :key="option" type="button" :class="{ active: role === option }" @click="changeRole(option)">{{ option === 'host' ? '主持人' : option === 'red' ? '红方' : '蓝方' }}</button></div></div>
    </header>

    <div class="game-hud">
      <button class="hud-scenario" type="button" @click="togglePanel('status')"><small>SCENARIO / 001 · 虚构地图</small><strong>北岭红蓝推演</strong><span>{{ roleName }} · {{ preparationPreview ? '部署预览' : '实时沙盘' }}</span></button>
      <div v-if="!preparationPreview" class="hud-missions" aria-label="任务进度">
        <button v-if="role === 'host'" class="hud-mission red" type="button" @click="togglePanel('status')"><small>红方任务 · {{ tasks.red.type }}</small><strong>{{ taskProgress.red }} / 3 节点</strong><i><b :style="{ width: (taskProgress.red / 3 * 100) + '%' }"></b></i></button>
        <button v-if="role === 'host'" class="hud-mission blue" type="button" @click="togglePanel('status')"><small>蓝方任务 · {{ tasks.blue.type }}</small><strong>{{ taskProgress.blue }} / 3 节点</strong><i><b :style="{ width: (taskProgress.blue / 3 * 100) + '%' }"></b></i></button>
        <button v-if="role !== 'host'" :class="['hud-mission', role]" type="button" @click="togglePanel('status')"><small>本方任务 · {{ currentTask.type }}</small><strong>{{ taskProgress[activeSide] }} / 3 节点</strong><i><b :style="{ width: (taskProgress[activeSide] / 3 * 100) + '%' }"></b></i></button>
      </div>
      <div class="hud-clock"><small>{{ preparationPreview ? 'DEPLOYMENT PREVIEW' : '仿真时间 · 每步 1 分钟' }}</small><strong>{{ preparationPreview ? '准备阶段' : String(currentStep).padStart(2, '0') + ' / 60' }}</strong><span>{{ preparationPreview ? '兵种放置仅供页面预览' : '剩余 ' + remainingMinutes + ' 分钟' }}</span></div>
    </div>

    <nav class="game-dock" aria-label="沙盘功能">
      <button type="button" :class="{ active: activePanel === 'status' }" :aria-expanded="activePanel === 'status'" @click="togglePanel('status')"><i>▤</i><span>任务态势</span></button>
      <button type="button" :class="{ active: activePanel === 'units' }" :aria-expanded="activePanel === 'units'" @click="togglePanel('units')"><i>◆</i><span>部队编组</span></button>
      <button v-if="preparationPreview" type="button" :class="{ active: activePanel === 'catalog' }" :aria-expanded="activePanel === 'catalog'" @click="togglePanel('catalog')"><i>＋</i><span>选择兵种</span></button>
      <button v-else type="button" :class="{ active: activePanel === 'timeline' }" :aria-expanded="activePanel === 'timeline'" @click="togglePanel('timeline')"><i>◷</i><span>事件记录</span></button>
      <button v-if="!preparationPreview && role !== 'host'" type="button" :class="{ urgent: pending[role] }" @click="drawer = 'suggestion'"><i>✦</i><span>{{ pending[role] ? '确认方案' : 'AI 建议' }}</span><b v-if="pending[role]">!</b></button>
      <div v-if="!preparationPreview && role === 'host'" class="dock-divider"></div>
      <button v-if="!preparationPreview && role === 'host'" type="button" @click="openTaskSettings"><i>▦</i><span>设置任务</span></button>
      <button v-if="!preparationPreview && role === 'host'" type="button" @click="openEvent"><i>✧</i><span>注入事件</span></button>
      <button v-if="!preparationPreview && role === 'host'" type="button" :disabled="!!waitingSide" :title="waitingSide ? '等待对应操作员确认' : ''" @click="togglePause"><i>{{ paused ? '▶' : 'Ⅱ' }}</i><span>{{ paused ? '继续' : '暂停' }}</span></button>
      <button v-if="!preparationPreview && role === 'host'" type="button" :disabled="!!waitingSide" :title="waitingSide ? '等待对应操作员确认' : ''" @click="advanceStep"><i>▸</i><span>推进一步</span></button>
    </nav>

    <div class="map-context">
      <div class="context-heading"><span :class="['side-emblem', role]">{{ role === 'host' ? '◇' : '◆' }}</span><div><small>{{ preparationPreview ? '开局部署' : '当前任务' }}</small><strong>{{ preparationPreview ? role === 'host' ? '查看红蓝双方部署区域' : '选择兵种并在本方区域放置' : role === 'host' ? '查看双方任务与地图目标' : taskTitle(currentTask.type) }}</strong></div></div>
      <p>{{ preparationPreview ? role === 'host' ? '操作员放置部队；主持人仅查看，本页不改变对局' : '本页仅预览放置效果，不改变对局' : role === 'host' ? '红蓝双方目标可从任务态势查看' : taskTargetLabel(currentTask.target) }}</p>
      <button v-if="preparationPreview" type="button" @click="togglePanel('catalog')">打开兵种列表 →</button>
      <button v-else type="button" @click="togglePanel('status')">查看任务详情 →</button>
    </div>

    <div class="map-status" aria-live="polite"><span :class="{ urgent: statusUrgent }">● {{ statusText }}</span><span v-if="latestPublicEvent && !preparationPreview">公开事件 · {{ latestPublicEvent.title }}</span></div>
    <div class="map-compass">N <strong>▲</strong> 北</div>
    <div v-if="preparationPreview" class="map-placement-hint">{{ role === 'host' ? '主持人可查看双方部署范围' : selectedTroopType ? '已选 ' + selectedTroopType + ' · 点击本方高亮区域放置' : '从左侧打开兵种列表，再点击本方部署区' }}</div>

    <aside v-if="activePanel === 'status' || activePanel === 'units'" class="game-panel" aria-label="沙盘信息面板">
      <div class="game-panel-head"><div><small>{{ activePanel === 'units' ? 'UNIT ROSTER' : 'MISSION STATUS' }}</small><h2>{{ activePanel === 'units' ? '部队编组' : preparationPreview ? '部署规则' : role === 'host' ? '双方任务态势' : '本方任务态势' }}</h2></div><button type="button" aria-label="关闭信息面板" @click="activePanel = null">×</button></div>
      <div class="game-panel-body">
        <template v-if="activePanel === 'status'">
          <div v-if="!preparationPreview && waitingSide && (role === 'host' || pending[role])" class="decision-alert"><span class="decision-alert-icon">!</span><div><small>关键决策 · 推演已暂停</small><strong>{{ role === 'host' ? waitingSide + '有待确认建议' : '本方方案等待确认' }}</strong><p>{{ role === 'host' ? '由对应操作员处理，主持人不能代为确认。' : '查看依据与预期影响，再确认、修改或驳回。' }}</p></div><button v-if="role !== 'host'" type="button" @click="drawer = 'suggestion'">查看方案 →</button></div>
          <div v-if="preparationPreview" class="deployment-summary"><span class="summary-kicker">开局配置 · 界面预览</span><h3>{{ role === 'host' ? '红蓝双方部署范围' : (role === 'red' ? '红' : '蓝') + '方部署区' }}</h3><p>{{ role === 'host' ? '主持人可查看双方区域，不能替操作员放置。' : '选择兵种后点击本方高亮区域。' }}</p><div class="deployment-stats"><div><small>初始单位</small><strong>{{ role === 'host' ? 6 : 3 }}</strong></div><div><small>本次预览新增</small><strong>{{ previewUnitCount }}</strong></div></div><div class="deployment-rule"><strong>部署规则</strong><span>只在开局准备阶段放置</span><span>当前不消耗资源 · 暂无数量上限</span><span>双方使用相同兵种目录</span></div></div>
          <div v-else-if="role === 'host'" class="team-overview"><div class="team red"><span>红方 · {{ tasks.red.type }}</span><strong>{{ taskProgress.red }} / 3 节点</strong><em>{{ taskTargetLabel(tasks.red.target) }}</em></div><div class="team blue"><span>蓝方 · {{ tasks.blue.type }}</span><strong>{{ taskProgress.blue }} / 3 节点</strong><em>{{ taskTargetLabel(tasks.blue.target) }}</em></div></div>
          <div v-else class="mission"><div class="section-title"><strong>当前任务</strong><small>最晚第 {{ currentTask.deadlineStep }} 仿真分钟</small></div><h3>{{ taskTitle(currentTask.type) }}</h3><div class="task-target">目标：{{ taskTargetLabel(currentTask.target) }}</div><div class="progress"><span :style="{ width: (taskProgress[activeSide] / 3 * 100) + '%' }"></span></div><div class="progress-text"><b>{{ taskProgress[activeSide] }} / 3</b> 已完成节点</div><div class="nodes"><div v-for="(label, index) in currentTaskNodes" :key="label" :class="{ done: index < taskProgress[activeSide], current: index === taskProgress[activeSide] }"><i>{{ index < taskProgress[activeSide] ? '✓' : index + 1 }}</i>{{ label }} <span>{{ index < taskProgress[activeSide] ? '已完成' : index === taskProgress[activeSide] ? '进行中' : '待开始' }}</span></div></div><p class="time-explainer">1 步 = 1 仿真分钟。当前第 {{ currentStep }} 步（{{ currentStep }} 分钟）。</p></div>
          <div v-if="!preparationPreview" class="side-section resource-section"><div class="section-title"><strong>资源指令</strong><small>每方预算 20 条</small></div><div class="resource-grid"><div><span>{{ role === 'host' ? '红方已执行' : (role === 'blue' ? '蓝方' : '红方') + '已执行' }}</span><strong>{{ role === 'blue' ? '7' : '8' }}<small> 条</small></strong></div><div><span>{{ role === 'host' ? '蓝方已执行' : '剩余额度' }}</span><strong>{{ role === 'host' ? '7' : role === 'blue' ? '13' : '12' }}<small> 条</small></strong></div></div></div>
          <div v-if="!preparationPreview" class="ai-summary"><div class="ai-title"><span>✦</span><strong>AI 总指挥摘要</strong><small>演示文案</small></div><p v-if="role === 'host'">{{ pending.red ? '红方待确认' : '红方已处理' }}；{{ pending.blue ? '蓝方待确认' : '蓝方已处理' }}。</p><p v-else-if="taskChangePending[activeSide]">主持人已设置{{ currentTask.type }}任务，等待本方确认。</p><p v-else>当前任务为{{ currentTask.type }}，已完成 {{ taskProgress[activeSide] }} / 3 个节点。</p><button v-if="role !== 'host'" type="button" @click="drawer = 'suggestion'">{{ pending[role] ? '查看待确认建议' : '查看已处理建议' }} ↗</button></div>
          <div v-if="!preparationPreview" class="uncertainty"><strong>◇ 信息不确定性</strong><p>尚未接入实时设备；状态仅用于原型展示。</p></div>
        </template>
        <template v-else>
          <div class="section-title"><strong>可见虚拟单位</strong><small>{{ visibleUnits.length }} 个</small></div>
          <div v-for="group in unitGroups" :key="group.name" class="unit-group"><div class="unit-group-head"><span class="unit-group-icon">{{ group.shape }}</span><strong>{{ group.name }}</strong><small>{{ group.members.length }} 个</small></div><button v-for="unit in group.members" :key="unit.id" type="button" :class="['unit-row', unit.side, { active: selectedUnitId === unit.id }]" @click="selectedUnitId = unit.id"><span class="unit-description"><strong>{{ unit.id }} <small>· {{ unit.name }}</small></strong><em>{{ unit.state === '暂停 5 步' ? '受事件影响' : unit.state === '部署预览' ? '新放置 · 仅预览' : '位置与状态来自演示数据' }}</em></span><span :class="['unit-status', { warning: unit.state.includes('暂停') }]">{{ unit.state }}</span></button></div>
          <div v-if="selectedUnit" class="unit-detail"><small>地图已高亮 · {{ selectedUnit.id }}</small><strong>{{ selectedUnit.name }} / {{ selectedUnit.state }}</strong><span>坐标 {{ selectedUnit.x }}，{{ selectedUnit.y }} · 虚构地图</span></div>
        </template>
      </div>
    </aside>

    <section v-if="activePanel === 'catalog' && preparationPreview" class="troop-catalog game-popover" aria-label="兵种列表"><div class="catalog-heading"><div><span class="eyebrow">TROOP CATALOG</span><h2>选择部队</h2></div><span>新增部队不消耗资源 · 仅页面预览</span><button type="button" class="popover-close" aria-label="关闭兵种列表" @click="activePanel = null">×</button></div><div class="catalog-grid"><button v-for="troop in troopCatalog" :key="troop.name" type="button" :disabled="role === 'host'" :aria-pressed="selectedTroopType === troop.name" :class="['troop-card', { active: selectedTroopType === troop.name }]" @click="selectedTroopType = troop.name"><span class="troop-portrait">{{ troop.shape }}</span><span class="troop-copy"><strong>{{ troop.name }}</strong><small>{{ troop.description }}</small><em>已放置 {{ previewUnits.filter(unit => unit.side === role && unit.name === troop.name).length }} 个</em></span><span class="troop-action">{{ selectedTroopType === troop.name ? '已选择' : '选择' }}</span></button></div></section>
    <section v-if="activePanel === 'timeline' && !preparationPreview" class="timeline game-popover" aria-label="对局时间线"><div class="timeline-head"><div><span class="eyebrow">EVENT STREAM</span><h2>对局时间线</h2></div><div><span>{{ role === 'host' ? '双方记录' : '本方及公开记录' }}</span>第 {{ currentStep }} 仿真分钟<button type="button" class="popover-close" aria-label="关闭时间线" @click="activePanel = null">×</button></div></div><div class="event-track"><div class="track-line"></div><button v-for="item in visibleRecords" :key="item.id" type="button" :class="['record', item.kind, { active: selectedRecordId === item.id }]" @click="selectedRecordId = item.id"><i></i><small>MIN {{ String(item.step).padStart(2, '0') }}</small><strong>{{ item.title }}</strong><span>{{ item.detail }}</span></button><div class="record now"><i></i><small>NOW</small><strong>当前状态</strong><span>第 {{ currentStep }} 分钟</span></div></div><div v-if="selectedRecord" class="record-detail">第 {{ selectedRecord.step }} 分钟：{{ selectedRecord.title }}。完整状态回放将在复盘页面呈现。</div></section>

    <div class="game-footer"><span>虚构地图 · 仅供界面演示 · 本机演示存档不是后端正式存档</span><button v-if="role === 'host'" type="button" @click="saveAndExitDemo">保存并返回首页</button><button type="button" @click="navigatePage('home')">返回首页</button><button type="button" @click="resetDemo">重置演示 ↺</button></div>    <div v-if="drawer" class="backdrop" @click="drawer = null"></div>
    <aside v-if="drawer === 'suggestion' && role !== 'host'" class="drawer" aria-label="任务建议">
      <div class="drawer-head"><div><span class="eyebrow">TASK / PROPOSAL</span><h2>{{ role === 'red' ? '红方' : '蓝方' }}{{ taskChangeContent[role] ? '新任务处理' : '任务调整建议' }}</h2></div><button type="button" aria-label="关闭" @click="drawer = null">×</button></div>
      <div class="drawer-body">
        <div class="drawer-status">{{ taskChangeRejected[role] ? suggestionResult[role] : pending[role] ? `待确认 · 第 ${currentStep} 步` : suggestionResult[role] }}</div>
        <template v-if="taskChangeContent[role]">
          <div class="proposal-section"><small>01 / 主持人设置</small><h3>{{ taskTitle(tasks[role].type) }}</h3><p>目标：{{ taskTargetLabel(tasks[role].target) }}；最晚第 {{ tasks[role].deadlineStep }} 仿真分钟。</p></div>
          <div class="proposal-section"><small>02 / 处理说明</small><p>新任务的节点进度已从 0 开始。此卡用于演示本方确认流程，尚未接入真实 AI 建议服务。</p></div>
          <div v-if="taskChangeRejected[role]" class="event-preview"><strong>等待主持人重新设置任务</strong><p>原建议已驳回，本方任务暂不继续执行。</p></div>
        </template>
        <template v-else>
          <div class="proposal-section"><small>01 / 建议内容</small><h3>调整 B 区巡查分工</h3><p>由陆兵继续完成 B 区巡查，侦察无人机观察区域并报告已发现信息，保障单位待命。</p></div>
          <div class="proposal-section"><small>02 / 依据与来源</small><div class="evidence"><span>任务进度</span><strong>A 区完成 · 第 15 步</strong></div><div class="evidence"><span>公共天气</span><strong>能见度下降 · 第 16 步</strong></div><div class="evidence uncertain"><span>未知信息</span><strong>B 区新报告待更新</strong></div></div>
          <div class="proposal-section"><small>03 / 预期影响</small><p>优先保障 B 区节点的连续性；实际结果由后续沙盘状态决定。</p><div class="impact"><div><small>拟新增资源指令</small><strong>2 条</strong></div><div><small>剩余预算</small><strong>12 / 20</strong></div></div></div>
          <div v-if="editing" class="edit-form"><label>本方单位分工<textarea v-model="assignment" rows="3"></textarea></label><label>任务优先级<input v-model="priority" type="text"></label><label>资源额度<input v-model.number="extraResources" type="number" min="0" max="20"></label><p>仅演示本方分工、优先级和额度的前端编辑。</p></div>
        </template>
      </div>
      <div v-if="pending[role] && !taskChangeRejected[role]" class="drawer-actions"><button v-if="!taskChangePending[role]" type="button" class="secondary-button" @click="editing = !editing">{{ editing ? '取消修改' : '修改建议' }}</button><button type="button" class="secondary-button" @click="decide(false)">驳回</button><button type="button" class="primary-button" @click="decide(true)">{{ editing && !taskChangePending[role] ? '提交修改并确认' : '确认建议' }}</button></div>
    </aside>

    <aside v-if="drawer === 'tasks' && role === 'host'" class="drawer task-drawer" aria-label="红蓝任务设置">
      <div class="drawer-head"><div><span class="eyebrow">MISSION SETUP / FORMAT CHECK</span><h2>设置红蓝双方任务</h2></div><button type="button" aria-label="关闭" @click="drawer = null">×</button></div>
      <div class="drawer-body">
        <p class="drawer-intro">每方输入一行任务，格式不正确时不能应用。对局中修改任务会清零该方新任务节点，并等待该方操作员确认。</p>
        <div class="task-format-guide"><strong>固定输入格式</strong><code>任务类型|目标|截止步数</code><p>使用半角竖线 <b>|</b> 分隔，不要输入说明文字。截止步数固定为 <b>60</b>。</p><div><span>区域巡查 / 物资护送</span><b>A-B-C 或 C-B-A</b></div><div><span>关键设施保护</span><b>设施01</b></div><div><span>受损设备撤离</span><b>撤离点</b></div></div>
        <div class="proposal-section"><small>01 / 红方任务</small><label class="task-input-label" for="red-task-input">任务指令</label><input id="red-task-input" v-model="taskDraft.red" type="text" :aria-invalid="!!taskErrors.red" :placeholder="taskFormatExample.red" @input="invalidateTaskCheck('red')"><p class="input-example">示例：{{ taskFormatExample.red }}</p><p v-if="taskErrors.red" class="input-error" role="alert">{{ taskErrors.red }}</p></div>
        <div class="proposal-section"><small>02 / 蓝方任务</small><label class="task-input-label" for="blue-task-input">任务指令</label><input id="blue-task-input" v-model="taskDraft.blue" type="text" :aria-invalid="!!taskErrors.blue" :placeholder="taskFormatExample.blue" @input="invalidateTaskCheck('blue')"><p class="input-example">示例：{{ taskFormatExample.blue }}</p><p v-if="taskErrors.blue" class="input-error" role="alert">{{ taskErrors.blue }}</p></div>
        <p v-if="taskErrors.shared" class="input-error shared-error" role="alert">{{ taskErrors.shared }}</p>
        <div v-if="checkedTasks" class="task-check-result"><strong>✓ 格式校验通过</strong><p>红方：{{ checkedTasks.red.type }} · {{ taskTargetLabel(checkedTasks.red.target) }}</p><p>蓝方：{{ checkedTasks.blue.type }} · {{ taskTargetLabel(checkedTasks.blue.target) }}</p><small>双方同模板、目标对称、截止第 60 步。</small></div>
      </div>
      <div class="drawer-actions"><button type="button" class="secondary-button" @click="drawer = null">取消</button><button type="button" class="secondary-button" @click="checkTaskFormat">校验格式</button><button type="button" class="primary-button" :disabled="!checkedTasks" @click="applyTasks">应用任务</button></div>
    </aside>

    <aside v-if="drawer === 'event' && role === 'host'" class="drawer" aria-label="事件控制"><div class="drawer-head"><div><span class="eyebrow">SCENARIO CONTROL</span><h2>事件控制</h2></div><button type="button" aria-label="关闭" @click="drawer = null">×</button></div><div class="drawer-body"><p class="drawer-intro">选择虚构条件事件，预览后加入当前演示时间线。</p><div class="proposal-section"><small>01 / 事件类型</small><div class="event-types"><button v-for="type in (['设备故障', '通信中断', '天气变化', '任务变化'] as EventType[])" :key="type" type="button" :class="{ active: eventType === type }" @click="eventType = type; previewed = false">{{ type }}</button></div></div><label class="field">影响方<select v-model="affectedSide" :disabled="eventType === '天气变化'" @change="previewed = false"><option value="red">红方</option><option value="blue">蓝方</option></select><small v-if="eventType === '天气变化'">天气变化对双方公开</small></label><label v-if="eventType === '设备故障'" class="field">影响单位<select v-model="affectedUnit" @change="previewed = false"><option v-for="unit in units.filter(item => item.side === affectedSide)" :key="unit.id" :value="unit.id">{{ unit.id }} · {{ unit.name }}</option></select></label><label class="field">触发步数<input v-model.number="eventStep" type="number" min="0" max="60" @input="previewed = false"><small>本原型仅演示当前第 {{ currentStep }} 步手动触发</small></label><div class="proposal-section"><small>02 / 固定效果</small><p>{{ effect }}</p></div><div v-if="previewed" class="event-preview"><strong>预览影响</strong><p>{{ eventType }} · {{ eventType === '天气变化' ? '双方公开' : affectedSide === 'red' ? '仅红方和主持人可见' : '仅蓝方和主持人可见' }}</p><p>{{ effect }}</p></div></div><div class="drawer-actions"><button type="button" class="secondary-button" @click="drawer = null">取消</button><button type="button" class="secondary-button" @click="previewed = true">预览影响</button><button type="button" class="primary-button" :disabled="!previewed || eventStep !== currentStep" @click="triggerEvent">确认触发</button></div></aside>
    <div v-if="notice" class="toast" role="status">{{ notice }}</div>
  </div>
  <HistoryPage v-else-if="navigation.view === 'history'" @navigate="navigatePage" @back="navigatePage('home')" />
  <DemoSavePage v-else-if="navigation.view === 'demo-saves'" :external-error="saveError" @back="navigatePage('home')" @load="loadDemoSave" />
  <ResultPage v-else-if="navigation.view === 'result' && navigation.gameId" :game-id="navigation.gameId" :role="role" @navigate="navigatePage" />
  <ReplayPage v-else-if="navigation.view === 'replay' && navigation.gameId" :game-id="navigation.gameId" :branch-id="navigation.branchId" :snapshot-id="navigation.snapshotId" :step="navigation.step" :role="role" @navigate="navigatePage" />
  <main v-else class="invalid-page"><h1>页面地址无效</h1><p>当前页面缺少有效的对局标识，未执行读档或其他业务操作。</p><button type="button" class="secondary-button" @click="navigatePage('home')">返回首页</button></main>
</template>

<style scoped>
.invalid-page{min-height:100vh;background:#edf1f2;color:#29445b;padding:8vh 8vw}.invalid-page h1{font-size:26px}.invalid-page p{font-size:13px;color:#6d8294;margin:12px 0 22px}.invalid-page button{border:1px solid #b9ccd7;background:#fff;color:#355773;border-radius:5px;padding:9px 15px}
</style>

