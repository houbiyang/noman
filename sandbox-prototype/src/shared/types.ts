/**
 * 前端与后端 API 草案共享的边界类型。
 * 这些类型只描述页面可读取的投影，不代表内部数据库快照。
 */

export type Role = 'host' | 'red' | 'blue'
export type Side = 'red' | 'blue'
export type PageView = 'home' | 'live' | 'demo' | 'demo-saves' | 'sandbox' | 'history' | 'result' | 'replay'
export type GamePhase = 'preparing' | 'running' | 'paused' | 'awaiting_confirmation' | 'finished'

export type StateContext = {
  game_id: string
  branch_id: string
  snapshot_id: string
  context_token: string
}

export type SessionView = {
  role: Role | null
  game_id?: string
  scopes?: string[]
  expires_at: string | null
  game_ids?: string[]
}

export type SaveSummary = {
  save_id: string
  save_kind: 'auto' | 'manual' | 'final'
  display_name: string | null
  game_id: string
  source_branch_id: string
  snapshot_id: string
  simulation_step: number
  phase: GamePhase
  saved_at: string
  is_demo: boolean
  can_restore: boolean
  unavailable_reason?: string | null
}

export type HistoryItem = {
  game_id: string
  game_no: string
  title: string
  state: 'unfinished' | 'finished'
  task_type: string
  is_demo: boolean
  simulation_step: number
  saved_at: string | null
  winner?: Side | 'draw' | null
  saves?: SaveSummary[]
}

export type PageResult<T> = { items: T[]; page: number; page_size: number; total: number }

export type TaskConfigurationPreview = {
  valid: boolean
  input_revision: string
  parsed_tasks: Record<Side, { task_type: string; target_ids: string[] }> | null
  errors: Record<Side | 'shared', string | null>
  preview_token: string | null
}

export type GameResult = {
  game_id: string
  branch_id: string
  context?: StateContext
  snapshot_id: string
  simulation_step: number
  winner: 'red' | 'blue' | 'draw'
  task_type: string
  scores: Record<Side, { task: string; resource: string; safety: string; weighted_total: string }>
  weights: { task: string; resource: string; safety: string }
}

export type ReplayFrame = {
  game_id: string
  branch_id: string
  snapshot_id: string
  simulation_step: number
  read_only: true
  map: Record<string, unknown>
  units: Array<Record<string, unknown>>
  tasks: Array<Record<string, unknown>>
  events: Array<{ event_id?: string; simulation_step: number; title: string; detail?: string }>
  resources: Array<Record<string, unknown>>
  result: GameResult | null
}

export type CommandReceipt = {
  command_id: string
  request_key: string
  command_type: string
  status: 'accepted' | 'running' | 'succeeded' | 'failed' | 'rejected'
  origin_branch_id: string
  result?: { branch_id: string; snapshot_id: string; simulation_step: number; save_id?: string | null } | null
  error?: ApiError | null
  replayed: boolean
  completed_at?: string | null
}

export type ApiFieldError = {
  object_type?: string
  object_id?: string | null
  client_ref?: string | null
  path?: string
  code?: string
  message: string
}

export type ApiError = {
  code: string
  message: string
  fields?: ApiFieldError[]
  request_id?: string | null
  command_id?: string | null
  retry_action?: 'none' | 'edit_and_resubmit' | 'refresh_context' | 'query_command' | 'retry_read'
  outcome?: 'not_applied' | 'applied' | 'unknown' | 'not_applicable'
}

export type GameView = {
  api_variant: 'minimal' | 'draft'
  schema_version: string
  context: StateContext
  role: Role
  game: { game_id: string; game_no: string; title: string; is_demo: boolean; scenario_version_id?: string; rule_version_id?: string }
  clock: { simulation_step: number; deadline_step: number | null; step_minutes: number | null; phase: GamePhase; pause_reasons: string[] }
  capabilities: Record<string, boolean | string>
  map: Record<string, unknown>
  tasks: Array<Record<string, unknown>>
  camps: Array<Record<string, unknown>>
  units: Array<Record<string, unknown>>
  contacts: Array<Record<string, unknown>>
  last_seen_records: Array<Record<string, unknown>>
  decisions: Record<string, unknown>
  resources: Array<Record<string, unknown>>
  preparation_status: { ready: Partial<Record<Side, boolean>> }
  save_status: Record<string, unknown>
  service_status: Record<string, unknown>
  observed_at: string
}
