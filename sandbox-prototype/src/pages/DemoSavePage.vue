<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { readDemoSaves, type DemoSave } from '../shared/demoSaves'

defineProps<{ externalError: string }>()
const emit = defineEmits<{ back: []; load: [saveId: string] }>()
const saves = ref<DemoSave[]>([])
const errorMessage = ref('')

function loadSaves() {
  errorMessage.value = ''
  try { saves.value = readDemoSaves() }
  catch (error) { errorMessage.value = error instanceof Error ? error.message : '本机演示存档读取失败' }
}

function savedTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '保存时间无效' : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

onMounted(loadSaves)
</script>

<template>
  <main class="save-page"><header class="save-header"><div><span class="eyebrow">LOCAL DEMO ARCHIVE</span><h1>打开之前的存档</h1><p>选择此前手动保存的本机演示进度。</p></div><button type="button" @click="emit('back')">返回首页</button></header><section class="save-content"><p v-if="externalError" class="external-error" role="alert">{{ externalError }}</p><div v-if="errorMessage" class="save-empty error" role="alert"><strong>存档读取失败</strong><p>{{ errorMessage }}</p><button type="button" @click="loadSaves">重新读取</button></div><div v-else-if="!saves.length" class="save-empty"><strong>暂无演示存档</strong><p>进入对战沙盘后，使用“保存并返回首页”创建第一份存档。</p><button type="button" @click="emit('back')">返回首页</button></div><div v-else class="save-list"><article v-for="save in saves" :key="save.save_id" class="save-card"><div class="save-icon">▣</div><div class="save-summary"><span>本机演示存档 · {{ savedTime(save.saved_at) }}</span><h2>{{ save.state.tasks.red.type }} · 第 {{ save.state.currentStep }} 步</h2><p>红方 {{ save.state.taskProgress.red }} / 3 节点 · 蓝方 {{ save.state.taskProgress.blue }} / 3 节点</p></div><button type="button" @click="emit('load', save.save_id)">读取存档 →</button></article></div><p class="save-boundary">此列表只包含本浏览器明确保存的页面演示状态，不代表后端正式存档或跨设备同步。</p></section></main>
</template>

<style scoped>
:global(body:has(.save-page)){min-width:0}
.save-page{min-height:100vh;background:linear-gradient(135deg,#eaf0ef,#dce6e8);padding:36px clamp(22px,6vw,90px);color:#233e50}.save-header{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;max-width:1100px;margin:0 auto 35px}.eyebrow{color:#b68139;font-size:10px;letter-spacing:2px;font-weight:800}.save-header h1{font-size:30px;margin:8px 0}.save-header p{color:#748b97;font-size:12px}.save-header button,.save-empty button{border:1px solid #b9ccd2;border-radius:5px;background:#fff;color:#355c71;padding:9px 14px;font-size:12px;cursor:pointer}.save-content{max-width:1100px;margin:auto}.save-list{display:grid;gap:12px}.save-card{display:flex;align-items:center;gap:18px;min-height:112px;padding:18px 20px;background:#fff;border:1px solid #c8d7da;border-radius:9px;box-shadow:0 6px 18px #203b4a12}.save-icon{display:grid;place-items:center;width:51px;height:51px;flex-shrink:0;border-radius:7px;background:#e6f0f1;color:#517b88;font-size:25px}.save-summary{flex:1}.save-summary span{font-size:10px;color:#9a7b4e}.save-summary h2{font-size:17px;margin:6px 0}.save-summary p{font-size:11px;color:#8597a2}.save-card button{border:1px solid #b87930;border-radius:5px;background:#e3a351;color:#322814;padding:10px 14px;font-size:12px;font-weight:700;cursor:pointer}.save-empty{max-width:640px;border:1px solid #c8d7da;border-radius:9px;background:#fff;padding:35px;color:#315166}.save-empty.error{border-left:3px solid #c1745c}.save-empty strong{font-size:17px}.save-empty p{font-size:12px;line-height:1.6;color:#7e939e;margin:10px 0 20px}.save-boundary{font-size:10px;color:#8299a1;margin-top:20px;line-height:1.6}@media(max-width:650px){.save-header,.save-card{flex-direction:column;align-items:flex-start}.save-summary{width:100%}.save-card button{width:100%}}
.external-error{border-left:3px solid #c1745c;background:#fff3ee;padding:10px 13px;margin-bottom:14px;color:#a55742;font-size:12px}
</style>
