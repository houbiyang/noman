import type { ApiError, CommandReceipt, GameResult, GameView, HistoryItem, PageResult, ReplayFrame, SaveSummary, SessionView, StateContext, TaskConfigurationPreview } from './types'
import { normalizeGameView } from './gameView'

/** API 客户端错误，保留服务端可读业务码。 */
export class ApiClientError extends Error {
  readonly status: number
  readonly details: ApiError
  readonly pendingCommand: { gameId: string; branchId: string; requestKey: string } | null

  constructor(status: number, details: ApiError, pendingCommand: { gameId: string; branchId: string; requestKey: string } | null = null) {
    super(details.message)
    this.name = 'ApiClientError'
    this.status = status
    this.details = details
    this.pendingCommand = pendingCommand
  }
}

/** 受控的浏览器 API 访问边界。正式令牌只保存在当前页面内存，不写入 localStorage。 */
export class ApiClient {
  private readonly baseUrl: string
  private readonly timeoutMs: number
  private accessToken: string | null = null

  constructor(baseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1', timeoutMs = 0) {
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.timeoutMs = timeoutMs
  }

  setAccessToken(token: string | null) {
    this.accessToken = token?.trim() || null
  }

  get hasAccessToken() {
    return this.accessToken !== null
  }

  async getHealth(signal?: AbortSignal) { return this.request<{ status: string; service: string; database: string }>('/health', { signal }) }
  async enterLocalGame(gameId: string | null, role: 'host' | 'red' | 'blue', signal?: AbortSignal) {
    return this.request<{ game_id: string; role: 'host' | 'red' | 'blue'; token: string; expires_at: string }>('/local-entry', {
      method: 'POST', body: JSON.stringify({ game_id: gameId, role }), signal,
    })
  }
  async getSession(signal?: AbortSignal) { return this.request<SessionView>('/session', { signal }) }

  async getHistory(params: { state?: 'unfinished' | 'finished'; page?: number; pageSize?: number }, signal?: AbortSignal) {
    const query = new URLSearchParams({ page: String(params.page || 1), page_size: String(params.pageSize || 20) })
    if (params.state) query.set('state', params.state)
    return this.request<PageResult<HistoryItem>>(`/history?${query.toString()}`, { signal })
  }

  async getGameView(gameId: string, signal?: AbortSignal): Promise<GameView> { return normalizeGameView(await this.request<unknown>(`/games/${encodeURIComponent(gameId)}/view`, { signal })) }
  async getSaves(gameId: string, signal?: AbortSignal) { return this.request<PageResult<SaveSummary>>(`/games/${encodeURIComponent(gameId)}/saves`, { signal }) }
  async getResult(gameId: string, signal?: AbortSignal) { return this.request<GameResult>(`/games/${encodeURIComponent(gameId)}/result`, { signal }) }

  async getReplay(gameId: string, params: { branchId: string; step?: number; snapshotId?: string }, signal?: AbortSignal) {
    const query = new URLSearchParams({ branch_id: params.branchId })
    if (params.snapshotId) query.set('snapshot_id', params.snapshotId)
    else if (params.step !== undefined) query.set('step', String(params.step))
    return this.request<ReplayFrame>(`/games/${encodeURIComponent(gameId)}/replay?${query.toString()}`, { signal })
  }

  async getCommandResult(gameId: string, originBranchId: string, requestKey: string, signal?: AbortSignal) {
    const query = new URLSearchParams({ origin_branch_id: originBranchId, request_key: requestKey })
    return this.request<CommandReceipt>(`/games/${encodeURIComponent(gameId)}/command-results?${query.toString()}`, { signal })
  }

  async previewTasks(gameId: string, context: StateContext, inputRevision: string, rawTasks: { red: string; blue: string }, signal?: AbortSignal) {
    return this.request<TaskConfigurationPreview>(`/games/${encodeURIComponent(gameId)}/task-configuration/preview`, {
      method: 'POST', body: JSON.stringify({ context, input_revision: inputRevision, raw_tasks: rawTasks }), signal,
    })
  }

  async sendCommand(gameId: string, context: StateContext, commandType: string, payload: Record<string, unknown>, signal?: AbortSignal) {
    const requestKey = crypto.randomUUID()
    try { return await this.request<CommandReceipt>(`/games/${encodeURIComponent(gameId)}/commands`, {
      method: 'POST',
      headers: { 'Idempotency-Key': requestKey },
      body: JSON.stringify({ context, command_type: commandType, payload }),
      signal,
    }) } catch (error) {
      if (error instanceof ApiClientError && error.details.outcome === 'unknown') {
        throw new ApiClientError(error.status, { ...error.details, message: `${error.message} 请先按原请求键核对命令结果。` }, { gameId, branchId: context.branch_id, requestKey })
      }
      throw error
    }
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const controller = new AbortController()
    const timeout = this.timeoutMs > 0 ? window.setTimeout(() => controller.abort(), this.timeoutMs) : null
    const headers = new Headers(options.headers)
    headers.set('Accept', 'application/json')
    if (options.body) headers.set('Content-Type', 'application/json')
    if (this.accessToken) headers.set('Authorization', `Bearer ${this.accessToken}`)
    if (options.signal?.aborted) controller.abort()
    else options.signal?.addEventListener('abort', () => controller.abort(), { once: true })
    try {
      const response = await fetch(`${this.baseUrl}${path}`, { ...options, headers, signal: controller.signal, cache: 'no-store' })
      const body = response.headers.get('content-type')?.includes('application/json') ? await response.json().catch(() => null) : null
      if (!response.ok) {
        const details = body?.error && typeof body.error.message === 'string' ? body.error as ApiError : body?.code && typeof body.message === 'string' ? body as ApiError : undefined
        throw new ApiClientError(response.status, details || { code: `HTTP_${response.status}`, message: '服务返回了无法解析的错误。', retry_action: 'retry_read', outcome: 'unknown' })
      }
      if (body === null || typeof body !== 'object') {
        throw new ApiClientError(response.status, { code: 'INVALID_RESPONSE', message: '服务未返回有效 JSON，接口可能尚未部署。', retry_action: 'retry_read', outcome: 'not_applicable' })
      }
      return body as T
    } catch (error) {
      if (error instanceof ApiClientError) throw error
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiClientError(0, { code: 'REQUEST_TIMEOUT_OR_ABORTED', message: '请求超时或已取消，执行结果需要重新核对。', retry_action: 'query_command', outcome: 'unknown' })
      }
      throw new ApiClientError(0, { code: 'NETWORK_UNAVAILABLE', message: '暂时无法连接正式服务，当前页面没有切换为演示成功。', retry_action: 'retry_read', outcome: 'unknown' })
    } finally {
      if (timeout !== null) window.clearTimeout(timeout)
    }
  }
}

export const apiClient = new ApiClient()
