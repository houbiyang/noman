<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiClient, ApiClientError } from '../shared/api'
import type { GameResult, Role, Side } from '../shared/types'

const props = defineProps<{ gameId: string; role: Role }>()
const emit = defineEmits<{ navigate: [view: 'history' | 'replay' | 'sandbox', gameId?: string] }>()
const result = ref<GameResult | null>(null)
const errorMessage = ref('')
const loading = ref(true)
let readController: AbortController | null = null
let readSequence = 0

async function loadResult() {
  readController?.abort()
  readController = new AbortController()
  const sequence = ++readSequence
  loading.value = true
  errorMessage.value = ''
  result.value = null
  try {
    const nextResult = await apiClient.getResult(props.gameId, readController.signal)
    if (sequence !== readSequence) return
    if (!nextResult.scores?.red || !nextResult.scores?.blue || !nextResult.winner) throw new Error('结果字段与当前页面契约不一致')
    result.value = nextResult
  }
  catch (error) { if (sequence === readSequence) errorMessage.value = error instanceof ApiClientError ? `${error.message}（${error.details.code}）` : error instanceof Error ? error.message : '结果读取失败。' }
  finally { if (sequence === readSequence) loading.value = false }
}

onMounted(loadResult)
watch(() => props.gameId, loadResult)
onBeforeUnmount(() => { readController?.abort(); ++readSequence })
</script>

<template>
  <main class="result-page">
    <header class="result-header"><div><span class="eyebrow">GAME RESULT / READ ONLY</span><h1>对局结果</h1><p>结果分数来自服务端冻结记录，页面不重新计算。</p></div><button type="button" class="secondary-button" @click="emit('navigate', 'history')">返回历史对局</button></header>
    <div v-if="loading" class="result-state">正在读取正式结果……</div>
    <div v-else-if="errorMessage" class="result-state error-state"><strong>结果暂不可用</strong><p>{{ errorMessage }}</p><button type="button" class="secondary-button" @click="loadResult">重新读取</button></div>
    <template v-else-if="result">
      <section class="winner-card"><span>胜负</span><strong>{{ result.winner === 'draw' ? '平局' : result.winner === 'red' ? '红方胜出' : '蓝方胜出' }}</strong><small>第 {{ result.simulation_step }} 步结束 · {{ result.task_type }}</small></section>
      <section class="score-grid"><article v-for="side in (['red', 'blue'] as Side[])" :key="side" :class="['score-card', side]"><h2>{{ side === 'red' ? '红方' : '蓝方' }}</h2><div class="score-total">{{ result.scores[side].weighted_total }}</div><div class="score-row"><span>任务</span><b>{{ result.scores[side].task }}</b></div><div class="score-row"><span>资源</span><b>{{ result.scores[side].resource }}</b></div><div class="score-row"><span>安全</span><b>{{ result.scores[side].safety }}</b></div></article></section>
      <button type="button" class="primary-button replay-button" @click="emit('navigate', 'replay', props.gameId)">进入只读复盘</button>
    </template>
  </main>
</template>

<style scoped>
.result-page{min-height:100vh;background:#edf1f2;padding:36px clamp(22px,6vw,96px);color:#243b50}.result-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:28px}.result-header h1{font-size:30px;margin:8px 0}.result-header p{color:#708396;font-size:13px}.result-state,.winner-card,.score-card{background:#fff;border:1px solid #ccd8de;border-radius:10px;padding:26px}.result-state{max-width:680px;color:#60788c;line-height:1.7}.error-state{border-left:3px solid #c98b62}.winner-card{max-width:500px;display:flex;flex-direction:column;gap:8px;margin-bottom:18px}.winner-card span{font-size:10px;color:#899bab}.winner-card strong{font-size:26px;color:#b27932}.winner-card small{font-size:11px;color:#7b8e9d}.score-grid{display:grid;grid-template-columns:repeat(2,minmax(240px,360px));gap:14px}.score-card.red{border-top:4px solid #d35c67}.score-card.blue{border-top:4px solid #4e90c8}.score-card h2{font-size:16px}.score-total{font-size:32px;font-weight:800;color:#294766;margin:12px 0 18px}.score-row{display:flex;justify-content:space-between;border-top:1px solid #edf1f4;padding:10px 0;font-size:12px;color:#748697}.score-row b{color:#34516b}.replay-button{margin-top:20px}.secondary-button,.primary-button{border-radius:5px;padding:9px 14px;font-size:12px}.secondary-button{border:1px solid #c4d1d8;background:#fff;color:#45647d}.primary-button{border:1px solid #b96e22;background:#e29937;color:#2d2518}@media(max-width:680px){.result-header,.score-grid{display:flex;flex-direction:column;gap:12px}}
</style>
