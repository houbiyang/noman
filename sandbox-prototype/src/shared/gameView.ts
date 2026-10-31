import type { GamePhase, GameView, Role, StateContext } from './types'

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isRole(value: unknown): value is Role {
  return value === 'host' || value === 'red' || value === 'blue'
}

function isPhase(value: unknown): value is GamePhase {
  return value === 'preparing' || value === 'running' || value === 'paused' || value === 'awaiting_confirmation' || value === 'finished'
}

function parseContext(value: unknown): StateContext {
  if (!isObject(value) || typeof value.game_id !== 'string' || typeof value.branch_id !== 'string' || typeof value.snapshot_id !== 'string' || typeof value.context_token !== 'string') throw new Error('服务返回的状态上下文不完整')
  return { game_id: value.game_id, branch_id: value.branch_id, snapshot_id: value.snapshot_id, context_token: value.context_token }
}

function parsePreparationStatus(value: unknown): GameView['preparation_status'] {
  if (value === undefined) return { ready: {} }
  if (!isObject(value) || !isObject(value.ready)) throw new Error('服务返回的准备状态结构无效')
  const ready: GameView['preparation_status']['ready'] = {}
  for (const side of ['red', 'blue'] as const) {
    if (value.ready[side] === undefined) continue
    if (typeof value.ready[side] !== 'boolean') throw new Error('服务返回的准备状态值无效')
    ready[side] = value.ready[side]
  }
  return { ready }
}

/** 将当前最小 HTTP 视图和目标 GameView 草案统一为只读页面输入。 */
export function normalizeGameView(response: unknown): GameView {
  if (!isObject(response) || !isRole(response.role) || !isObject(response.game)) throw new Error('服务返回的角色视图结构无效')
  const context = parseContext(response.context)
  const game = response.game
  if (typeof game.game_id !== 'string' || typeof game.game_no !== 'string' || typeof game.title !== 'string' || typeof game.is_demo !== 'boolean') throw new Error('服务返回的对局摘要不完整')
  const gameSummary: GameView['game'] = { game_id: game.game_id, game_no: game.game_no, title: game.title, is_demo: game.is_demo }
  if (!Array.isArray(response.tasks) || !Array.isArray(response.units)) throw new Error('角色视图缺少任务或单位列表')
  const taskItems = response.tasks.filter(isObject)
  const unitItems = response.units.filter(isObject)
  if (taskItems.length !== response.tasks.length || unitItems.length !== response.units.length) throw new Error('角色视图列表含无效对象')

  const hasFullProjection = isObject(response.clock) && isObject(response.capabilities) && isObject(response.map) && isObject(response.save_status) && isObject(response.service_status)
  if (hasFullProjection && isObject(response.clock)) {
    const clock = response.clock
    if (!Number.isInteger(clock.simulation_step) || !isPhase(clock.phase)) throw new Error('服务返回的仿真时钟无效')
    return {
      api_variant: 'draft', schema_version: typeof response.schema_version === 'string' ? response.schema_version : 'game-view-v1',
      context, role: response.role, game: gameSummary,
      clock: { simulation_step: clock.simulation_step as number, deadline_step: typeof clock.deadline_step === 'number' ? clock.deadline_step : null, step_minutes: typeof clock.step_minutes === 'number' ? clock.step_minutes : null, phase: clock.phase, pause_reasons: Array.isArray(clock.pause_reasons) ? clock.pause_reasons.filter((reason): reason is string => typeof reason === 'string') : [] },
      capabilities: isObject(response.capabilities) ? response.capabilities as GameView['capabilities'] : {},
      map: isObject(response.map) ? response.map : {}, tasks: taskItems,
      camps: Array.isArray(response.camps) ? response.camps.filter(isObject) : [], units: unitItems,
      contacts: Array.isArray(response.contacts) ? response.contacts.filter(isObject) : [],
      last_seen_records: Array.isArray(response.last_seen_records) ? response.last_seen_records.filter(isObject) : [],
      decisions: isObject(response.decisions) ? response.decisions : {},
      resources: Array.isArray(response.resources) ? response.resources.filter(isObject) : [],
      preparation_status: parsePreparationStatus(response.preparation_status),
      save_status: isObject(response.save_status) ? response.save_status : {},
      service_status: isObject(response.service_status) ? response.service_status : {},
      observed_at: typeof response.observed_at === 'string' ? response.observed_at : '',
    }
  }

  const minimalClock = isObject(response.clock) ? response.clock : null
  const simulationStep = minimalClock?.simulation_step ?? response.simulation_step
  const phase = minimalClock?.phase ?? response.phase
  if (!Number.isInteger(simulationStep) || !isPhase(phase)) throw new Error('最小角色视图没有有效仿真步或阶段')
  return {
    api_variant: 'minimal', schema_version: typeof response.schema_version === 'string' ? response.schema_version : 'minimal-backend-v0.1', context, role: response.role, game: gameSummary,
    clock: { simulation_step: simulationStep as number, deadline_step: typeof minimalClock?.deadline_step === 'number' ? minimalClock.deadline_step : null, step_minutes: typeof minimalClock?.step_minutes === 'number' ? minimalClock.step_minutes : null, phase, pause_reasons: Array.isArray(minimalClock?.pause_reasons) ? minimalClock.pause_reasons.filter((reason): reason is string => typeof reason === 'string') : [] },
    capabilities: {}, map: {}, tasks: taskItems,
    camps: Array.isArray(response.camps) ? response.camps.filter(isObject) : [], units: unitItems,
    contacts: Array.isArray(response.contacts) ? response.contacts.filter(isObject) : [],
    last_seen_records: Array.isArray(response.last_seen_records) ? response.last_seen_records.filter(isObject) : [],
    decisions: isObject(response.decisions) ? response.decisions : {},
    resources: Array.isArray(response.resources) ? response.resources.filter(isObject) : [],
    preparation_status: parsePreparationStatus(response.preparation_status),
    save_status: {}, service_status: {}, observed_at: typeof response.observed_at === 'string' ? response.observed_at : '',
  }
}
