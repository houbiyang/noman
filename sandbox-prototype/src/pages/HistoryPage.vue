<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiClient, ApiClientError } from '../shared/api'
import type { HistoryItem, SaveSummary, SessionView } from '../shared/types'

const emit = defineEmits<{ navigate: [view: 'live' | 'result' | 'replay', gameId?: string, step?: number, snapshotId?: string, branchId?: string]; back: [] }>()
const tokenDraft = ref('')
const session = ref<SessionView | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const items = ref<HistoryItem[]>([])
const selectedGameId = ref<string | null>(null)
const saves = ref<SaveSummary[]>([])
const savesLoading = ref(false)

function showError(error: unknown) {
  errorMessage.value = error instanceof ApiClientError ? `${error.message}（${error.details.code}）` : error instanceof Error ? error.message : '正式历史读取失败。'
}

async function loadHistory() {
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await apiClient.getHistory({})
    if (!Array.isArray(result.items)) throw new Error('历史目录结构不符合契约')
    items.value = result.items
  } catch (error) {
    showError(error)
  } finally {
    loading.value = false
  }
}

async function resumeSession() {
  loading.value = true
  errorMessage.value = ''
  try {
    const current = await apiClient.getSession()
    if (!current.game_id || !current.role) throw new Error('服务未返回对局席位')
    session.value = current
    await loadHistory()
  } catch (error) {
    session.value = null
    apiClient.setAccessToken(null)
    showError(error)
  } finally {
    loading.value = false
  }
}

async function connect() {
  if (!tokenDraft.value.trim()) return
  apiClient.setAccessToken(tokenDraft.value)
  tokenDraft.value = ''
  await resumeSession()
}

function disconnect() {
  apiClient.setAccessToken(null)
  session.value = null
  items.value = []
  selectedGameId.value = null
  saves.value = []
  errorMessage.value = ''
}

async function openSaves(gameId: string) {
  selectedGameId.value = gameId
  savesLoading.value = true
  errorMessage.value = ''
  try {
    const result = await apiClient.getSaves(gameId)
    if (!Array.isArray(result.items)) throw new Error('快照目录结构不符合契约')
    saves.value = result.items
  } catch (error) {
    saves.value = []
    showError(error)
  } finally {
    savesLoading.value = false
  }
}

function savedTime(value: string | null): string {
  if (!value) return '未返回保存时间'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '保存时间无效' : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

onMounted(() => { if (apiClient.hasAccessToken) resumeSession() })
</script>

<template>
  <main class="catalog-page">
    <header class="catalog-header">
      <div><span class="eyebrow">GAME CATALOG / HISTORY</span><h1>打开之前的存档</h1><p>显示当前服务端席位可见的对局与不可变快照。</p></div>
      <div class="catalog-header-actions"><button v-if="session" type="button" class="secondary-button" @click="disconnect">退出席位</button><button type="button" class="secondary-button" @click="emit('back')">返回首页</button></div>
    </header>
    <div v-if="!session" class="catalog-state"><strong>连接对局席位</strong><p>输入服务端签发的席位令牌后，可读取该席位所属对局的正式记录。令牌只保存在当前页面内存，刷新后需要重新输入。</p><label class="catalog-token-label">席位会话令牌<input v-model="tokenDraft" type="password" autocomplete="off" placeholder="粘贴服务端签发的席位令牌" @keyup.enter="connect"></label><button type="button" class="primary-button" :disabled="loading || !tokenDraft.trim()" @click="connect">{{ loading ? '正在验证…' : '查看正式存档' }}</button><p v-if="errorMessage" class="catalog-error" role="alert">{{ errorMessage }}</p></div>
    <div v-else-if="loading" class="catalog-state">正在读取正式历史目录……</div>
    <div v-else-if="errorMessage && !selectedGameId" class="catalog-state error-state"><strong>正式历史目录暂不可用</strong><p>{{ errorMessage }}</p><button type="button" class="secondary-button" @click="loadHistory">重新读取</button></div>
    <section v-else class="catalog-list" aria-label="历史对局列表">
      <div v-if="!items.length" class="catalog-state"><strong>暂无可见历史记录</strong><p>服务已返回空目录，不能据此推断存在其他不可见对局。</p></div>
      <article v-for="item in items" :key="item.game_id" class="catalog-card">
        <div><span class="catalog-tag">{{ item.state === 'unfinished' ? '未结束对局' : '已结束记录' }}</span><h2>{{ item.title }}</h2><p>{{ item.game_no }} · {{ item.task_type }} · 第 {{ item.simulation_step }} 步</p></div>
        <div class="catalog-meta"><span>{{ item.is_demo ? '演示对局' : '正式对局' }}</span><span>{{ savedTime(item.saved_at) }}</span><button v-if="item.state === 'unfinished'" type="button" class="primary-button" @click="emit('navigate', 'live', item.game_id)">继续当前对局</button><button v-else type="button" class="secondary-button" @click="emit('navigate', 'result', item.game_id)">查看结果</button><button v-if="session?.role === 'host'" type="button" class="secondary-button" @click="openSaves(item.game_id)">查看快照</button></div>
      </article>
      <div v-if="selectedGameId" class="catalog-state saves-panel"><strong>对局快照</strong><p>快照可用于只读复盘；正式对局尚未开放从旧快照恢复。</p><p v-if="savesLoading">正在读取快照……</p><p v-else-if="errorMessage" role="alert">{{ errorMessage }}</p><p v-else-if="!saves.length">当前没有可读取的快照。</p><div v-for="save in saves" :key="save.save_id" class="save-row"><span>{{ save.display_name || `第 ${save.simulation_step} 步` }} · {{ savedTime(save.saved_at) }}</span><button type="button" class="secondary-button" @click="emit('navigate', 'replay', save.game_id, save.simulation_step, save.snapshot_id, save.source_branch_id)">查看第 {{ save.simulation_step }} 步</button></div></div>
    </section>
  </main>
</template>

<style scoped>
.catalog-page{min-height:100vh;background:#edf1f2;padding:36px clamp(22px,6vw,96px);color:#243b50}.catalog-header{display:flex;justify-content:space-between;align-items:flex-start;gap:28px;margin-bottom:28px}.catalog-header h1{font-size:30px;margin:8px 0}.catalog-header p{color:#708396;font-size:13px}.catalog-list{display:grid;gap:12px;max-width:1100px}.catalog-card{display:flex;justify-content:space-between;gap:24px;align-items:center;background:#fff;border:1px solid #ccd8de;border-radius:10px;padding:20px 22px;box-shadow:0 5px 18px #26415012}.catalog-card h2{font-size:17px;margin:9px 0 6px}.catalog-card p,.catalog-meta{font-size:11px;color:#778b9c}.catalog-meta{display:flex;align-items:center;gap:15px;flex-wrap:wrap;justify-content:flex-end}.catalog-tag{font-size:10px;color:#a87734;background:#fff4df;border:1px solid #ecd39d;border-radius:4px;padding:4px 7px}.catalog-state{max-width:680px;background:#fff;border:1px solid #ccd8de;border-radius:10px;padding:30px;color:#60788c;line-height:1.7}.catalog-state strong{display:block;color:#2f506b;font-size:15px}.catalog-state p{margin:8px 0 16px}.catalog-state small{display:block;color:#9aa8b4;margin-top:16px}.error-state{border-left:3px solid #c98b62}.secondary-button,.primary-button{border-radius:5px;padding:9px 14px;font-size:12px}.secondary-button{border:1px solid #c4d1d8;background:#fff;color:#45647d}.primary-button{border:1px solid #b96e22;background:#e29937;color:#2d2518}@media(max-width:760px){.catalog-header,.catalog-card{align-items:flex-start;flex-direction:column}.catalog-meta{justify-content:flex-start}}
.catalog-header-actions{display:flex;gap:8px}.catalog-token-label{display:flex;flex-direction:column;gap:8px;max-width:500px;font-size:12px;color:#48657a;margin:18px 0}.catalog-token-label input{border:1px solid #c4d1d8;border-radius:5px;padding:11px;font-size:12px}.catalog-error{color:#a95348}.save-row{display:flex;align-items:center;justify-content:space-between;gap:12px;border-top:1px solid #e4ecef;padding:10px 0;font-size:12px}.saves-panel{max-width:1100px}
</style>
