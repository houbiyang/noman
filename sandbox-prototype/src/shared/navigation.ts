import type { PageView, Role } from './types'

export type NavigationState = { view: PageView; gameId: string | null; branchId: string | null; snapshotId: string | null; step: number | null; role: Role; invalid: boolean }

/** 统一解析页面地址，不由子组件自行修改 URL。 */
export function parseNavigation(url = window.location.href): NavigationState {
  const parsed = new URL(url)
  const viewValue = parsed.searchParams.get('view') || 'live'
  const validView = ['home', 'live', 'demo', 'demo-saves', 'sandbox', 'history', 'result', 'replay'].includes(viewValue)
  const view: PageView = viewValue === 'sandbox' && parsed.searchParams.get('mode') === 'formal' ? 'home' : viewValue === 'sandbox' ? 'live' : validView ? viewValue as PageView : 'home'
  const roleValue = parsed.searchParams.get('role')
  const role: Role = roleValue === 'red' || roleValue === 'blue' ? roleValue : 'host'
  const gameId = parsed.searchParams.get('gameId')
  const validGameId = !gameId || /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(gameId)
  const branchId = parsed.searchParams.get('branchId')
  const validBranchId = !branchId || /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(branchId)
  const snapshotId = parsed.searchParams.get('snapshotId')
  const validSnapshotId = !snapshotId || /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(snapshotId)
  const stepRaw = parsed.searchParams.get('step')
  const stepValue = stepRaw === null ? null : Number(stepRaw)
  const validStep = stepValue === null || Number.isInteger(stepValue) && stepValue >= 0 && stepValue <= 60
  return { view, gameId: gameId?.trim() || null, branchId: branchId?.trim() || null, snapshotId: snapshotId?.trim() || null, step: validStep ? stepValue : null, role, invalid: !validView || !validStep || !validGameId || !validBranchId || !validSnapshotId || (view === 'result' || view === 'replay') && !gameId }
}

export function navigateTo(next: Partial<NavigationState>, replace = false) {
  const current = parseNavigation()
  const target = { ...current, ...next }
  const url = new URL(window.location.href)
  url.searchParams.set('view', target.view === 'sandbox' ? 'live' : target.view)
  if (target.gameId) url.searchParams.set('gameId', target.gameId)
  else url.searchParams.delete('gameId')
  if (target.view === 'replay' && target.step !== null && target.step !== undefined) url.searchParams.set('step', String(target.step))
  else url.searchParams.delete('step')
  if (target.view === 'replay' && target.branchId) url.searchParams.set('branchId', target.branchId)
  else url.searchParams.delete('branchId')
  if (target.view === 'replay' && target.snapshotId) url.searchParams.set('snapshotId', target.snapshotId)
  else url.searchParams.delete('snapshotId')
  if (target.role !== 'host') url.searchParams.set('role', target.role)
  else url.searchParams.delete('role')
  url.searchParams.delete('mode')
  window.history[replace ? 'replaceState' : 'pushState']({}, '', url)
  window.dispatchEvent(new PopStateEvent('popstate'))
}
