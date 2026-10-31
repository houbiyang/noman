import type { TaskPair } from '../taskFormat'

export type DemoUnit = { id: string; side: 'red' | 'blue'; name: string; state: string; x: number; y: number; shape: string }
export type DemoRecordItem = { id: number; step: number; title: string; detail: string; visibility: 'public' | 'red' | 'blue'; kind: string }

export type DemoSyncState = {
  currentStep: number
  paused: boolean
  tasks: TaskPair
  taskProgress: Record<'red' | 'blue', number>
  pending: Record<'red' | 'blue', boolean>
  taskChangePending: Record<'red' | 'blue', boolean>
  taskChangeContent: Record<'red' | 'blue', boolean>
  taskChangeRejected: Record<'red' | 'blue', boolean>
  suggestionResult: Record<'red' | 'blue', string>
  units: DemoUnit[]
  previewUnits: DemoUnit[]
  faultEndSteps: Record<string, number>
  records: DemoRecordItem[]
  nextRecordId: number
}

export type DemoSave = {
  schema_version: 1
  save_id: string
  saved_at: string
  state: DemoSyncState
}

const saveStorageKey = 'qiongtu-demo-saves-v1'

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isDemoUnit(value: unknown): value is DemoUnit {
  return isObject(value) && typeof value.id === 'string' && (value.side === 'red' || value.side === 'blue')
    && typeof value.name === 'string' && typeof value.state === 'string' && typeof value.shape === 'string'
    && typeof value.x === 'number' && Number.isFinite(value.x) && typeof value.y === 'number' && Number.isFinite(value.y)
}

function isDemoRecord(value: unknown): value is DemoRecordItem {
  return isObject(value) && Number.isInteger(value.id) && Number.isInteger(value.step)
    && typeof value.title === 'string' && typeof value.detail === 'string' && typeof value.kind === 'string'
    && (value.visibility === 'public' || value.visibility === 'red' || value.visibility === 'blue')
}

/** 校验本机演示快照的基本结构，避免损坏的存档覆盖当前页面状态。 */
export function isDemoSyncState(value: unknown): value is DemoSyncState {
  if (!isObject(value) || !Number.isInteger(value.currentStep) || (value.currentStep as number) < 0 || (value.currentStep as number) > 60 || typeof value.paused !== 'boolean') return false
  if (!isObject(value.tasks) || !isObject(value.tasks.red) || !isObject(value.tasks.blue)) return false
  if (!isObject(value.taskProgress) || !isObject(value.pending) || !isObject(value.taskChangePending) || !isObject(value.taskChangeContent) || !isObject(value.taskChangeRejected) || !isObject(value.suggestionResult)) return false
  if (!Array.isArray(value.units) || !Array.isArray(value.previewUnits) || !Array.isArray(value.records) || !isObject(value.faultEndSteps) || !Number.isInteger(value.nextRecordId)) return false
  if (!value.units.every(isDemoUnit) || !value.previewUnits.every(isDemoUnit) || !value.records.every(isDemoRecord)) return false
  if (!Object.values(value.faultEndSteps).every(step => Number.isInteger(step))) return false
  const tasks = value.tasks as Record<'red' | 'blue', Record<string, unknown>>
  const taskProgress = value.taskProgress as Record<'red' | 'blue', unknown>
  const pending = value.pending as Record<'red' | 'blue', unknown>
  const taskChangePending = value.taskChangePending as Record<'red' | 'blue', unknown>
  const taskChangeContent = value.taskChangeContent as Record<'red' | 'blue', unknown>
  const taskChangeRejected = value.taskChangeRejected as Record<'red' | 'blue', unknown>
  const suggestionResult = value.suggestionResult as Record<'red' | 'blue', unknown>
  return (['red', 'blue'] as const).every(side =>
    typeof tasks[side].type === 'string' && typeof tasks[side].target === 'string' && Number.isInteger(tasks[side].deadlineStep)
    && Number.isInteger(taskProgress[side]) && typeof pending[side] === 'boolean'
    && typeof taskChangePending[side] === 'boolean' && typeof taskChangeContent[side] === 'boolean'
    && typeof taskChangeRejected[side] === 'boolean' && typeof suggestionResult[side] === 'string')
}

/** 读取独立的本机演示存档，不将跨窗口同步缓存充作存档。 */
export function readDemoSaves(): DemoSave[] {
  const raw = window.localStorage.getItem(saveStorageKey)
  if (!raw) return []
  const saves: unknown = JSON.parse(raw)
  if (!Array.isArray(saves) || !saves.every(save => isObject(save) && save.schema_version === 1 && typeof save.save_id === 'string' && typeof save.saved_at === 'string' && isDemoSyncState(save.state))) throw new Error('本机演示存档数据无法读取')
  return saves as DemoSave[]
}

/** 以独立条目保存当前演示状态，不覆盖已有存档。 */
export function createDemoSave(state: DemoSyncState): DemoSave {
  const copy: unknown = JSON.parse(JSON.stringify(state))
  if (!isDemoSyncState(copy)) throw new Error('当前演示状态不完整，未创建存档')
  const save: DemoSave = { schema_version: 1, save_id: crypto.randomUUID(), saved_at: new Date().toISOString(), state: copy }
  window.localStorage.setItem(saveStorageKey, JSON.stringify([save, ...readDemoSaves()]))
  return save
}
