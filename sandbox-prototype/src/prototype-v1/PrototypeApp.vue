<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import SandboxMap, { type MapUnit } from './SandboxMap.vue'
import Icon from './PrototypeIcon.vue'

type Page = 'live' | 'prepare' | 'history' | 'result' | 'replay'
type Role = 'red' | 'blue' | 'host'
type Panel = 'unit' | 'ai' | 'events' | 'save' | 'timeline' | null
const page = ref<Page>('live')
const role = ref<Role>('red')
const panel = ref<Panel>(null)
const modal = ref<'create' | 'exit' | 'restore' | null>(null)
const selected = ref('r-ground')
const follow = ref(false)
const zoom = ref(1)
const showRoutes = ref(true)
const showGrid = ref(true)
const layersOpen = ref(false)
const step = ref(18)
const replayStep = ref(18)
const approved = ref(false)
const running = ref(false)
const toast = ref('')
const deployedCamps = ref<{red:{x:number;y:number}|null;blue:{x:number;y:number}|null}>({red:null,blue:null})
const deployedCamp = computed(() => deployedCamps.value[role.value==='blue'?'blue':'red'])
const deploying = ref(false)
const ready = ref({ red: false, blue: false })
const configuredUnits = ref({red:{recon:1,ground:1,support:1},blue:{recon:1,ground:1,support:1}})
const unitCounts = computed(() => configuredUnits.value[role.value==='blue'?'blue':'red'])
const restoreTarget = ref<{name:string;step:number}|null>(null)
const taskType = ref('区域巡查')
const title = ref('青岚河谷 · 协同巡查')
const draftName = ref('青岚河谷 · 第 18 步')
const saveCount = ref(0)
const savedStep = ref<number | null>(null)
const lastSaveName = ref('')
const historyFilter = ref('全部记录')
const search = ref('')
const eventType = ref('通信中断')
const eventPreview = ref(false)
const eventsApplied = ref(0)
const proposalEditing = ref(false)
const resourceLimit = ref(2)
const priority = ref('先完成桥东检查点，再前往北侧观察点')
const rejected = ref(false)
let toastTimer: ReturnType<typeof setTimeout> | null = null
let playTimer: ReturnType<typeof setInterval> | null = null

const navItems: {key: Page; name: string; icon: string}[] = [
  {key:'live',name:'作战沙盘',icon:'map'}, {key:'prepare',name:'准备部署',icon:'grid'},
  {key:'history',name:'历史档案',icon:'history'}, {key:'result',name:'结果报告',icon:'flag'}, {key:'replay',name:'推演复盘',icon:'clock'},
]
const mapPages = computed(() => ['live','prepare','replay'].includes(page.value))
const displayedStep = computed(() => page.value === 'replay' ? replayStep.value : page.value === 'prepare' ? 0 : step.value)
const roleLabel = computed(() => ({red:'红方操作员',blue:'蓝方操作员',host:'主持人'})[role.value])
const phaseLabel = computed(() => page.value === 'replay' ? '只读复盘' : page.value === 'prepare' ? '部署准备' : !approved.value ? '等待人工确认' : running.value ? '示例播放中' : '已暂停')
const initialUnits: MapUnit[] = [
  {id:'r-recon',name:'侦察无人机 01',code:'R-U01',type:'recon',side:'red',x:392,y:258,state:'侦察巡航'},
  {id:'r-ground',name:'陆兵单元 01',code:'R-G01',type:'ground',side:'red',x:427,y:375,state:'行进待命'},
  {id:'r-support',name:'保障单元 01',code:'R-S01',type:'support',side:'red',x:281,y:456,state:'待命保障'},
  {id:'b-recon',name:'侦察无人机 01',code:'B-U01',type:'recon',side:'blue',x:945,y:173,state:'侦察巡航'},
  {id:'b-ground',name:'陆兵单元 01',code:'B-G01',type:'ground',side:'blue',x:941,y:302,state:'行进待命'},
  {id:'b-support',name:'保障单元 01',code:'B-S01',type:'support',side:'blue',x:1050,y:267,state:'待命保障'},
]
const allUnits = computed(() => initialUnits.map(unit => {
  const delta = page.value === 'prepare' ? 0 : (displayedStep.value - 18) * 3
  return {...unit,x: unit.x + (unit.type === 'ground' ? delta : 0)}
}))
const visibleUnits = computed(() => allUnits.value.filter(unit => role.value === 'host' || unit.side === role.value))
const selectedUnit = computed(() => visibleUnits.value.find(unit => unit.id === selected.value) || visibleUnits.value[0]!)
const ownUnits = computed(() => visibleUnits.value.filter(unit => unit.side === (role.value === 'host' ? 'red' : role.value)))
const catalog = [ {key:'recon' as const,name:'侦察无人机',icon:'drone',detail:'态势侦察 · 信息共享'}, {key:'ground' as const,name:'陆兵单元',icon:'shield',detail:'地面行动 · 节点执行'}, {key:'support' as const,name:'保障单元',icon:'box',detail:'物资补给 · 运输协作'} ]
const historyRows = computed(() => [
  ...(savedStep.value !== null ? [{name:lastSaveName.value,code:'当前原型手动记录',type:'区域巡查',date:'本次原型会话',step:savedStep.value,status:'可恢复示例',result:'—'}] : []),
  {name:'青岚河谷 · 协同巡查',code:'QT-2026-1031-01',type:'区域巡查',date:'2026.10.31  14:32',step:18,status:'进行中',result:'—'},
  {name:'北岭通道 · 护送行动',code:'QT-2026-1030-03',type:'物资护送',date:'2026.10.30  16:45',step:60,status:'已结束',result:'红方胜'},
  {name:'桥东设施 · 联合保护',code:'QT-2026-1030-02',type:'设施保护',date:'2026.10.30  10:20',step:48,status:'已结束',result:'平局'},
  {name:'河谷南线 · 紧急撤离',code:'QT-2026-1029-01',type:'人员撤离',date:'2026.10.29  09:18',step:60,status:'已结束',result:'蓝方胜'},
].filter(row => (historyFilter.value === '全部记录' || (historyFilter.value === '进行中' ? row.status !== '已结束' : row.status === '已结束')) && row.name.includes(search.value)))
const timeline = computed(() => [
  {step:0,title:'双方部署完成',text:'三兵种单元已进入准备位置',kind:'system'},
  {step:6,title:'首次方案已确认',text:'区域巡查任务进入执行',kind:'decision'},
  {step:12,title:'桥东出现历史观察',text:'仅保留最后发现位置',kind:'observation'},
  {step:16,title:'完成西岸集结',text:'任务节点 A 已完成',kind:'task'},
  {step:18,title:approved.value ? '调整方案已采用' : '调整方案待确认',text:approved.value ? '采用记录不代表后续动作已完成' : '桥东路径变化，等待本方操作员处理',kind:'decision'},
])

function notify(message: string) { toast.value=message; if(toastTimer) clearTimeout(toastTimer); toastTimer=setTimeout(()=>toast.value='',3600) }
function stopPlayback() { if(playTimer) clearInterval(playTimer); playTimer=null; running.value=false }
function navigate(target: Page) { stopPlayback(); page.value=target; panel.value=null; modal.value=null; follow.value=false; deploying.value=false; layersOpen.value=false }
function changeRole(next: Role) { role.value=next; selected.value=next==='blue'?'b-ground':'r-ground'; panel.value=null; follow.value=false; deploying.value=false; eventPreview.value=false; stopPlayback() }
function selectUnit(id: string) { selected.value=id; if(!deploying.value) panel.value='unit' }
function togglePanel(next: Panel) { panel.value=panel.value===next?null:next; layersOpen.value=false }
function approveProposal() { if(role.value==='host') return notify('主持人可以观察等待状态，不能代替红蓝确认'); approved.value=true; rejected.value=false; panel.value=null; notify('示例方案已采用 · 后续动作尚未执行') }
function advance() { if(role.value!=='host') return notify('时间推进由主持人控制'); if(!approved.value) return notify('方案尚待本方操作员确认，无法推进'); if(step.value>=60) {stopPlayback();navigate('result');return} step.value++; if(step.value===60) {stopPlayback();navigate('result')} }
function togglePlayback() { if(running.value) {stopPlayback();return} if(role.value!=='host'||!approved.value) {advance();return} running.value=true; playTimer=setInterval(advance,1800) }
function setCamp(point: {x:number;y:number}) { if(role.value==='host') return notify('请切换本方操作员进行部署示意'); const valid=role.value==='red' ? point.x>=60&&point.x<=325&&point.y>=344&&point.y<=618 : point.x>=922&&point.x<=1157&&point.y>=161&&point.y<=382; if(!valid) return notify('请选择本方虚线部署区域内的位置'); deployedCamps.value[role.value]=point; deploying.value=false; ready.value[role.value]=false; notify('兵营位置已更新为准备草稿 · 此处仅做原型范围提示') }
function setReady() { if(role.value==='host') return; if(!deployedCamp.value) return notify('请先点选兵营位置'); ready.value[role.value]=!ready.value[role.value]; notify(ready.value[role.value]?'本方已就绪（示例）':'已取消本方就绪') }
function startGame() { if(!ready.value.red||!ready.value.blue) return notify('需要红蓝双方分别完成准备'); step.value=0;approved.value=false;navigate('live');notify('已进入首次方案等待状态 · 请切换操作员确认') }
function createGame() { if(role.value!=='host') return notify('创建对局由主持人操作'); title.value=title.value.trim()||'青岚河谷 · 协同巡查'; ready.value={red:false,blue:false};deployedCamps.value={red:null,blue:null};configuredUnits.value={red:{recon:1,ground:1,support:1},blue:{recon:1,ground:1,support:1}};step.value=0;approved.value=false;saveCount.value=0;savedStep.value=null;navigate('prepare');notify('新建本地原型对局，未连接正式服务') }
function saveGame() { if(role.value!=='host') return notify('整局存档由主持人操作'); saveCount.value++;savedStep.value=step.value;lastSaveName.value=draftName.value.trim()||`青岚河谷 · 第 ${step.value} 步`;notify('已保存本次原型会话记录 · 刷新后清除') }
function restoreGame() { step.value=restoreTarget.value?.step??savedStep.value??12;approved.value=false;stopPlayback();navigate('live');notify('已展示恢复后的暂停示例 · 需重新核对方案') }
function applyEvent() { if(role.value!=='host') return; if(!eventPreview.value) return;eventsApplied.value++;eventPreview.value=false;notify(`示例事件“${eventType.value}”已登记，未进行真实规则结算`) }
function setEvent(next:string) {eventType.value=next;eventPreview.value=false}
function toggleFollow() {follow.value=!follow.value;notify(follow.value?`正在跟随 ${selectedUnit.value.code} · 视角示意`:'已返回沙盘总览')}
function handleKey(event: KeyboardEvent) { if(event.key!=='Escape') return; if(modal.value) modal.value=null; else if(panel.value) panel.value=null; else if(layersOpen.value) layersOpen.value=false; else if(deploying.value) deploying.value=false; else follow.value=false }
watch(()=>({...configuredUnits.value.red}),()=>{ready.value.red=false})
watch(()=>({...configuredUnits.value.blue}),()=>{ready.value.blue=false})
onMounted(()=>window.addEventListener('keydown',handleKey))
onUnmounted(()=>{stopPlayback();if(toastTimer)clearTimeout(toastTimer);window.removeEventListener('keydown',handleKey)})
</script>

<template>
  <div class="prototype-app">
    <header class="app-header">
      <a class="brand" href="./prototype-v1.html" aria-label="穹图原型首页"><span class="brand-emblem"><Icon name="target" :size="26"/></span><span><b>穹图<span class="brand-dot">.</span></b><small>QIONGTU / TACTICAL SANDBOX</small></span></a>
      <nav class="main-nav" aria-label="原型页面导航"><button v-for="item in navItems" :key="item.key" :class="{active:page===item.key}" @click="navigate(item.key)"><Icon :name="item.icon" :size="16"/>{{item.name}}</button></nav>
      <div class="header-right"><span class="prototype-label">交互原型 V1</span><span class="header-divider"></span><span class="avatar">{{role==='host'?'H':role==='red'?'R':'B'}}</span><select aria-label="当前角色" :value="role" @change="changeRole(($event.target as HTMLSelectElement).value as Role)"><option value="red">红方操作员</option><option value="blue">蓝方操作员</option><option value="host">主持人</option></select></div>
    </header>
    <div class="prototype-notice"><span class="small-dot"></span>页面与交互演示<span class="notice-divider">/</span>固定虚构示例 · 未接入 Unity 与真实仿真服务<span class="notice-right">P-01 — P-10 <span>DESIGN PREVIEW</span></span></div>

    <main v-if="mapPages" class="map-workspace">
      <div class="map-surface"><SandboxMap :units="visibleUnits" :selected="selected" :follow="follow" :zoom="zoom" :deploying="deploying" :side="role" :show-routes="showRoutes" :show-grid="showGrid" :red-camp="deployedCamps.red" :blue-camp="deployedCamps.blue" @select="selectUnit" @deploy="setCamp"/></div>
      <div class="map-vignette"></div>
      <section class="mission-heading"><div class="eyebrow"><span class="live-dot"></span>{{page==='prepare'?'PREPARATION':page==='replay'?'AFTER ACTION REVIEW':'LIVE SITUATION'}}<span class="heading-code">QT-1031 / 河谷演训区</span></div><h1>{{page==='prepare'?'部署你的行动力量':page==='replay'?'回到每一个决策时刻':title}}</h1><p>{{page==='prepare'?'在本方部署区选择兵营位置，配置三兵种协作单元。':page==='replay'?'依据冻结历史状态查看过程。切换视角不改变任何已发生事实。':'固定虚构场景 · 区域巡查 · 后端权威结算架构预览'}}</p></section>
      <div class="status-cluster"><span class="status-pill" :class="{amber:!approved&&page==='live',readonly:page==='replay'}"><span class="small-dot"></span>{{phaseLabel}}</span><div class="step-tile"><small>仿真步 / MIN</small><b>{{String(displayedStep).padStart(2,'0')}}<span>/ 60</span></b></div></div>

      <aside v-if="page==='live'" class="mission-card glass"><div class="card-kicker"><Icon name="flag" :size="15"/>当前任务<span class="tag">{{role==='host'?'红方示例':role==='blue'?'蓝方任务':'红方任务'}}</span></div><h2>{{taskType==='区域巡查'?'沿河谷完成区域巡查':taskType+'协作任务'}}</h2><p>西岸集结 → 桥东检查 → 北侧观察</p><div class="node-progress"><span class="complete">A<Icon name="check" :size="11"/></span><i class="complete"></i><span class="current">B</span><i></i><span>C</span><strong>1 <small>/ 3 节点</small></strong></div><div class="mission-facts"><span>截止步 <b>60</b></span><span>任务进度 <b>1 / 3</b></span></div><button class="text-button" @click="panel='unit'">查看执行单元 <Icon name="arrow" :size="14"/></button></aside>

      <aside v-if="page==='prepare'" class="preparation-card glass"><div class="card-kicker"><Icon name="grid" :size="16"/>开局准备<span class="tag">P-02</span></div><h2>从协作开始</h2><p>每方初始三类各 1 个，准备阶段免费追加。</p><div class="preparation-steps"><span class="active">01 配置部队</span><span>02 双方就绪</span><span>03 主持人开局</span></div><label class="field-label">任务模板<select v-model="taskType" :disabled="role!=='host'"><option>区域巡查</option><option>物资护送</option><option>设施保护</option><option>人员撤离</option></select></label><div class="catalog-list"><div v-for="unit in catalog" :key="unit.key" class="catalog-item"><span class="catalog-icon"><Icon :name="unit.icon" :size="22"/></span><span><b>{{unit.name}}</b><small>{{unit.detail}}</small></span><div class="stepper"><button :disabled="role==='host'||unitCounts[unit.key]<=1" :aria-label="'减少'+unit.name" @click="unitCounts[unit.key]--">−</button><b>{{unitCounts[unit.key]}}</b><button :disabled="role==='host'" :aria-label="'增加'+unit.name" @click="unitCounts[unit.key]++">+</button></div></div></div><button class="button secondary full" :disabled="role==='host'" @click="deploying=!deploying"><Icon name="target" :size="16"/>{{deploying?'取消点选':deployedCamp?'重新选择兵营位置':'在地图中放置兵营'}}</button><p class="small-hint">{{deployedCamp?`草稿坐标 X ${deployedCamp.x} / Y ${deployedCamp.y}`:'点击本方虚线部署区；此版只验证交互。'}}</p><div class="readiness"><span><i :class="{ready:ready.red}"></i>红方 {{ready.red?'已就绪':'未就绪'}}</span><span><i :class="{ready:ready.blue}"></i>蓝方 {{ready.blue?'已就绪':'未就绪'}}</span></div><button v-if="role==='host'" class="button primary full" @click="startGame">检查并开始对局<Icon name="arrow" :size="16"/></button><button v-else class="button primary full" @click="setReady">{{ready[role]?'取消本方就绪':'确认本方准备完成'}}<Icon name="check" :size="16"/></button><button class="text-button full create-link" @click="modal='create'">新建对局 / 查看未开始状态</button></aside>

      <div class="map-center-tools"><span class="compass">N<i></i></span><span>青岚河谷<small>QINGLAN VALLEY</small></span></div>
      <div class="map-toolbox glass"><button title="放大地图" aria-label="放大地图" @click="zoom=Math.min(1.8,zoom+.2)">+</button><button title="缩小地图" aria-label="缩小地图" @click="zoom=Math.max(.8,zoom-.2)">−</button><span></span><button title="图层设置" aria-label="图层设置" :class="{active:layersOpen}" @click="layersOpen=!layersOpen"><Icon name="layers"/></button><button title="回到总览" aria-label="回到总览" @click="zoom=1;follow=false"><Icon name="target"/></button></div>
      <div v-if="layersOpen" class="layer-menu glass"><b>地图图层</b><label><input v-model="showRoutes" type="checkbox"/>路线与任务连接</label><label><input v-model="showGrid" type="checkbox"/>坐标网格</label><small>仅改变显示，不改变仿真规则。</small></div>
      <div v-if="deploying" class="deployment-tip"><Icon name="target"/>选择本方虚线区域内的兵营位置<button class="icon-button" aria-label="取消兵营点选" @click="deploying=false"><Icon name="close" :size="16"/></button></div>

      <div v-if="page==='live' && !approved" class="decision-prompt glass"><span class="prompt-icon"><Icon name="bolt" :size="20"/></span><div><b>{{rejected?'方案已驳回，等待新建议':'有一项调整需要你的决定'}}</b><small>{{role==='host'?'等待本方操作员处理，主持人不能代确认。':'桥东路线条件变化 · 总指挥已提交协作方案'}}</small></div><button class="button amber-button" @click="panel='ai'">查看建议<Icon name="arrow" :size="15"/></button></div>
      <div class="map-legend"><span><i class="red"></i>{{role==='blue'?'红方历史记录':'红方单元'}}</span><span v-if="role==='host'||role==='blue'"><i class="blue"></i>蓝方单元</span><span><i class="route"></i>已选路线示意</span><small>业务坐标示意 · 高度不参与结算</small></div>

      <section v-if="page==='live'" class="bottom-command glass"><div class="unit-summary"><span class="side-mark" :class="{blue:role==='blue'}"></span><div><b>{{roleLabel}}</b><small>{{role==='host'?visibleUnits.length:ownUnits.length}} 个可见己方/授权单元 · {{role==='host'?'全局观察':'本方视图'}}</small></div></div><div class="command-divider"></div><button class="dock-button" :class="{active:panel==='unit'}" @click="togglePanel('unit')"><Icon name="shield"/><span>单位</span><small>{{ownUnits.length}}</small></button><button class="dock-button" :class="{active:panel==='ai'}" @click="togglePanel('ai')"><Icon name="bolt"/><span>AI 建议</span><i v-if="!approved" class="notification-dot"></i></button><button v-if="role==='host'" class="dock-button" :class="{active:panel==='events'}" @click="togglePanel('events')"><Icon name="flag"/><span>事件</span></button><button class="dock-button" :class="{active:panel==='timeline'}" @click="togglePanel('timeline')"><Icon name="clock"/><span>时间线</span></button><div class="dock-spacer"></div><button class="camera-button" :class="{active:follow}" @click="toggleFollow"><Icon :name="follow?'eye':'target'" :size="17"/>{{follow?'返回总览':'单位跟随'}}</button><template v-if="role==='host'"><button class="button secondary compact" :disabled="!approved" @click="advance">单步 +1</button><button class="button primary compact" :disabled="!approved" @click="togglePlayback"><Icon :name="running?'pause':'play'" :size="15"/>{{running?'暂停':'播放示例'}}</button><button class="icon-button" title="存档与退出" aria-label="存档与退出" @click="panel='save'"><Icon name="save"/></button></template><span v-else class="authority-hint">主持人控制时间</span></section>

      <section v-if="page==='replay'" class="replay-controls glass"><div class="replay-title"><Icon name="history"/><span><b>青岚河谷 · 历史片段</b><small>冻结快照示例 / 只读模式</small></span><button class="camera-button" @click="toggleFollow">{{follow?'返回总览':'单位跟随'}}</button><button class="text-button" @click="navigate('history')">退出复盘<Icon name="external" :size="13"/></button></div><div class="timeline-range"><button class="icon-button" aria-label="前一帧" :disabled="replayStep===0" @click="replayStep=Math.max(0,replayStep-1)">‹</button><span class="mono">T+{{String(replayStep).padStart(2,'0')}}</span><input v-model.number="replayStep" type="range" min="0" max="60" aria-label="复盘仿真步"/><span class="mono">60</span><button class="icon-button" aria-label="后一帧" :disabled="replayStep===60" @click="replayStep=Math.min(60,replayStep+1)">›</button></div><div class="timeline-ticks"><span>00 部署完成</span><span>12 历史观察</span><span>18 方案调整</span><span>36 协同执行</span><span>60 结束固化</span></div></section>
    </main>

    <main v-else-if="page==='history'" class="document-workspace"><div class="page-heading"><div><div class="eyebrow">MISSION ARCHIVE / P-08</div><h1>每一次推演，都有迹可循。</h1><p>查阅历史对局，回到关键决策，继续尚未完成的任务。</p></div><button class="button primary" @click="modal='create'"><Icon name="plus" :size="17"/>新建对局</button></div><div class="archive-overview"><article><span class="mini-illustration"><Icon name="document" :size="26"/></span><div><small>示例档案</small><b>{{4+(savedStep!==null?1:0)}}<span>份</span></b></div><p>包含进行中与已结束记录</p></article><article><span class="mini-illustration"><Icon name="history" :size="26"/></span><div><small>当前原型记录</small><b>{{saveCount}}<span>次保存</span></b></div><p>仅在本次页面会话保留</p></article><article class="archive-tip"><span class="mini-illustration"><Icon name="shield" :size="26"/></span><div><b>按权限查看</b><p>进行中的私有内容不跨方公开，结束后可进入只读复盘。</p></div></article></div><section class="archive-table panel-surface"><div class="table-toolbar"><div class="segmented"><button v-for="filter in ['全部记录','进行中','已结束']" :key="filter" :class="{active:historyFilter===filter}" @click="historyFilter=filter">{{filter}}</button></div><label class="search-field"><Icon name="search" :size="16"/><input v-model="search" placeholder="搜索对局名称" aria-label="搜索对局名称"/></label></div><div class="table-scroll"><table><thead><tr><th>对局 / 编号</th><th>任务模板</th><th>保存时间</th><th>仿真步</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="row in historyRows" :key="row.code"><td><div class="archive-name"><span class="archive-map-icon"><Icon name="map" :size="21"/></span><span><b>{{row.name}}</b><small>{{row.code}}</small></span></div></td><td>{{row.type}}</td><td class="mono subdued">{{row.date}}</td><td class="mono">{{String(row.step).padStart(2,'0')}} <span class="muted">/ 60</span></td><td><span class="state-tag" :class="{finished:row.status==='已结束'}">{{row.status}}</span></td><td><button v-if="row.status==='已结束'" class="table-action" @click="navigate('replay');replayStep=row.step">查看复盘<Icon name="arrow" :size="14"/></button><button v-else-if="role==='host'" class="table-action" @click="restoreTarget={name:row.name,step:row.step};modal='restore'">恢复示例<Icon name="arrow" :size="14"/></button><span v-else class="muted">需主持人恢复</span></td></tr><tr v-if="!historyRows.length"><td colspan="6" class="empty-state">没有匹配记录，请调整名称或筛选条件。</td></tr></tbody></table></div><div class="table-foot"><span>共 {{historyRows.length}} 条示例记录</span><span>原型档案 · 非真实运行结果</span></div></section></main>

    <main v-else-if="page==='result'" class="document-workspace result-workspace"><div class="page-heading"><div><div class="eyebrow">AFTER ACTION REPORT / P-09</div><h1>行动落幕，决策留痕。</h1><p>青岚河谷 · 协同巡查 / 示例结束步 60 / 固定评分数据</p></div><button class="button secondary" @click="navigate('history')"><Icon name="back" :size="16"/>历史档案</button></div><section class="result-hero panel-surface"><div class="victory-seal"><Icon name="flag" :size="34"/></div><div class="victory-copy"><span class="eyebrow">MISSION COMPLETE</span><h2>红方胜出</h2><p>任务完成度与资源使用共同决定结果</p><span class="state-tag finished">已结束 · 示例结果</span></div><div class="result-score red-score"><small>红方总分</small><b>89<span>.0</span></b><p>任务 100 / 资源 65 / 安全 80</p></div><div class="versus">VS</div><div class="result-score blue-score"><small>蓝方总分</small><b>69<span>.0</span></b><p>任务 60 / 资源 85 / 安全 80</p></div></section><div class="result-grid"><section class="panel-surface score-breakdown"><div class="section-heading"><h2>分项表现</h2><span>60% · 20% · 20%</span></div><div class="score-legend"><span><i class="red"></i>红方</span><span><i class="blue"></i>蓝方</span></div><div v-for="item in [{name:'任务完成',weight:'权重 60%',red:100,blue:60},{name:'资源使用',weight:'权重 20%',red:65,blue:85},{name:'单位安全',weight:'权重 20%',red:80,blue:80}]" :key="item.name" class="score-row"><div><b>{{item.name}}</b><small>{{item.weight}}</small></div><div class="comparison-bars"><div><i :style="{width:item.red+'%'}" class="red-bar"></i><b>{{item.red}}</b></div><div><i :style="{width:item.blue+'%'}" class="blue-bar"></i><b>{{item.blue}}</b></div></div></div><p class="small-hint">总分 = 任务 × 0.6 + 资源 × 0.2 + 安全 × 0.2。此处仅展示独立固定样例。</p></section><section class="panel-surface decision-summary"><div class="section-heading"><h2>关键节点</h2><Icon name="clock" :size="18"/></div><div v-for="event in timeline.slice(1)" :key="event.step" class="result-event"><span class="mono">T+{{String(event.step).padStart(2,'0')}}</span><div><b>{{event.step===18?'桥东协作方案已采用':event.title}}</b><small>{{event.step===18?'依据冻结示例记录查看当时处理':event.text}}</small></div></div><button class="button primary full" @click="navigate('replay');replayStep=18">进入只读复盘<Icon name="arrow" :size="17"/></button></section></div></main>

    <footer class="app-footer"><span><i class="small-dot"></i>本地交互预览<span class="footer-separator">/</span>示例数据</span><span v-if="mapPages">{{follow?'单位跟随示意':'沙盘总览'}}<span class="footer-separator">/</span>观察视角不改变业务状态</span><span class="footer-right">穹图 · 无人智能作战沙盘<span class="mono">V1.0 / 2026.10</span></span></footer>

    <template v-if="panel && page==='live'">
      <button class="panel-scrim" aria-label="关闭功能面板" @click="panel=null"></button>
      <aside class="feature-drawer" role="dialog" :aria-label="panel==='unit'?'单位详情':panel==='ai'?'AI建议确认':panel==='events'?'事件控制':panel==='save'?'存档与退出':'事件时间线'">
        <div class="drawer-heading"><div><div class="eyebrow">{{panel==='unit'?'UNIT INSPECTOR':panel==='ai'?'COMMAND DECISION':panel==='events'?'CONDITION CONTROL':panel==='save'?'SAVE & RESTORE':'EVENT TIMELINE'}}</div><h2>{{panel==='unit'?'单位与协作':panel==='ai'?'AI 协作建议':panel==='events'?'事件控制':panel==='save'?'保存此刻，继续推演':'行动时间线'}}</h2></div><button class="icon-button" aria-label="关闭面板" @click="panel=null"><Icon name="close" :size="22"/></button></div>
        <div class="drawer-content">
          <template v-if="panel==='unit'"><div class="unit-picker"><button v-for="unit in visibleUnits" :key="unit.id" :class="{active:selectedUnit.id===unit.id}" @click="selected=unit.id;follow=false"><Icon :name="unit.type==='recon'?'drone':unit.type==='ground'?'shield':'box'" :size="18"/><span>{{unit.code}}</span></button></div><div class="unit-detail-hero"><div class="detail-glyph"><Icon :name="selectedUnit.type==='recon'?'drone':selectedUnit.type==='ground'?'shield':'box'" :size="48"/></div><span class="state-tag">{{selectedUnit.state}}</span><h3>{{selectedUnit.name}}</h3><p class="mono">{{selectedUnit.code}} / {{selectedUnit.side==='red'?'RED':'BLUE'}} FORCE</p></div><div class="detail-facts"><div><span>主任务</span><b>区域巡查 · 桥东检查</b></div><div><span>管理角色</span><b>{{selectedUnit.type==='recon'?'侦察':selectedUnit.type==='ground'?'陆兵':'保障'}}逻辑智能体</b></div><div><span>有效观察</span><b>T+{{String(step).padStart(2,'0')}} · 示例快照</b></div><div><span>业务位置</span><b class="mono">{{selectedUnit.x}} / {{selectedUnit.y}}</b></div></div><div class="info-box"><Icon name="document" :size="17"/><span>此版展示状态与选择关系，能源、库存和耐久未填充产品默认值。</span></div><button class="button primary full" @click="follow=true;panel=null">跟随此单位<Icon name="eye" :size="17"/></button></template>
          <template v-else-if="panel==='ai'"><div class="inline-alert" :class="{success:approved}"><Icon :name="approved?'check':'bolt'" :size="18"/>{{approved?'该示例方案已采用':rejected?'已驳回 · 等待后续建议':'待本方操作员确认 · 暂停推进'}}</div><template v-if="role!=='host'"><div class="proposal-heading"><span class="agent-avatar">AI</span><div><b>{{role==='blue'?'蓝方':'红方'}}总指挥</b><small>结构化示例建议 / 依据 T+18</small></div><span class="tag">示例</span></div><h3 class="proposal-title">调整桥东检查点的协同顺序</h3><p class="body-copy">先由侦察无人机核对桥东通行情况，陆兵保持当前待命位置，保障单元准备后续服务。确认有效报告后，再执行已采用计划中的后续行动。</p><div class="plan-list"><div><Icon name="drone"/><span><b>侦察先行</b><small>核对路线条件，保留未知项</small></span><span class="mono">01</span></div><div><Icon name="shield"/><span><b>陆兵协同</b><small>继续桥东节点任务，不增加并行动作</small></span><span class="mono">02</span></div><div><Icon name="box"/><span><b>保障待命</b><small>按既定服务资格提供后续保障</small></span><span class="mono">03</span></div></div><div class="evidence-card"><div><Icon name="document" :size="16"/><b>依据与未知项</b></div><p>示例规则说明 · 当前本方观察 · 已采用任务</p><small>桥东动态条件仍待核验。未调用真实模型，也没有生成真实资料引用。</small></div><div v-if="proposalEditing" class="edit-proposal"><label class="field-label">任务优先级<textarea v-model="priority" rows="3"></textarea></label><label class="field-label">资源额度上限<input v-model.number="resourceLimit" type="number" min="0" max="20"/></label></div><button v-if="!approved" class="text-button" @click="proposalEditing=!proposalEditing">{{proposalEditing?'收起人工修改':'修改分工与额度'}}<Icon name="chevron" :size="14"/></button></template><div v-else class="info-box"><Icon name="shield" :size="18"/><span>当前为主持人观察席位。这里只提供“等待本方操作员处理”的摘要；请切换红方或蓝方查看其建议确认原型。</span></div><p class="small-hint">{{role==='host'?'主持人仅查看演示等待摘要，不能代红蓝确认。':'关闭此抽屉不会确认或驳回方案。采用成功不等于后续动作已完成。'}}</p></template>
          <template v-else-if="panel==='events'"><p class="body-copy">为当前对局注入预定义条件。先查看影响，再明确登记。此版不执行真实事件结算。</p><div class="event-type-grid"><button v-for="type in ['设备故障','通信中断','天气变化','任务变化']" :key="type" :class="{active:eventType===type}" @click="setEvent(type)"><Icon :name="type==='设备故障'?'shield':type==='通信中断'?'bolt':type==='天气变化'?'layers':'flag'"/>{{type}}</button></div><label class="field-label">影响对象<select @change="eventPreview=false"><option>红方侦察无人机 R-U01</option><option>蓝方侦察无人机 B-U01</option></select></label><div class="detail-facts"><div><span>生效方式</span><b>确认后登记示例事件</b></div><div><span>持续口径</span><b>{{eventType==='任务变化'?'目标替换至本局结束':'五个仿真步（具体区间待定）'}}</b></div></div><div v-if="eventPreview" class="evidence-card"><div><Icon name="eye" :size="16"/><b>影响预览</b></div><p>{{eventType==='通信中断'?'相关共享报告在影响期间显示为未知，不提供实时精确位置。':eventType==='设备故障'?'对象在规则规定期间暂停相关行动。':eventType==='天气变化'?'受影响进度按已确认的延迟规则处理。':'目标变化需要重新核对任务和人工确认。'}}</p><small>不直接扣安全分。输入变化后需重新预览。</small></div><p class="small-hint">本次原型已登记 {{eventsApplied}} 条示例事件。</p></template>
          <template v-else-if="panel==='save'"><p class="body-copy">保留当前对局观察点，或查看整局恢复流程。</p><label class="field-label">存档名称<input v-model="draftName" maxlength="60"/></label><div class="save-preview"><Icon name="save" :size="32"/><div><b>当前示例快照</b><small>青岚河谷 / T+{{step}} / {{phaseLabel}}</small></div></div><div class="info-box"><Icon name="document" :size="18"/><span>原型保存仅保留在本次页面内存。刷新后清除，不代表完整仿真存档。</span></div><button class="button primary full" @click="saveGame">保存原型记录<Icon name="save" :size="16"/></button><button class="button secondary full spaced" :disabled="savedStep===null" @click="restoreTarget=null;modal='restore'">恢复最近示例记录</button><button class="text-button danger full spaced" @click="stopPlayback();modal='exit'">退出对局示例<Icon name="external" :size="15"/></button></template>
          <template v-else><div class="timeline-list"><div v-for="event in timeline" :key="event.step" class="timeline-event"><span class="timeline-dot"></span><small class="mono">T+{{String(event.step).padStart(2,'0')}}</small><b>{{event.title}}</b><p>{{event.text}}</p></div></div><div class="info-box"><Icon name="clock" :size="18"/><span>当前显示固定样例轨迹，不作为实际模型或仿真执行证据。</span></div></template>
        </div>
        <div v-if="panel==='ai'&&!approved" class="drawer-actions"><button class="button secondary" :disabled="role==='host'||rejected" @click="rejected=true;notify('原建议已驳回 · 等待后续建议，尚未生成新方案')">驳回</button><button class="button primary" :disabled="role==='host'||rejected||resourceLimit<0||resourceLimit>20" @click="approveProposal">{{proposalEditing?'修改后采用':'确认采用'}}<Icon name="check" :size="16"/></button></div><div v-if="panel==='events'" class="drawer-actions"><button class="button secondary" @click="eventPreview=true">预览影响</button><button class="button primary" :disabled="!eventPreview" @click="applyEvent">确认登记<Icon name="check" :size="16"/></button></div>
      </aside>
    </template>

    <div v-if="modal" class="modal-backdrop" @click.self="modal=null"><section class="modal-card" role="dialog" aria-modal="true" :aria-label="modal==='create'?'新建对局':modal==='exit'?'退出对局':'恢复确认'"><button class="modal-close icon-button" aria-label="关闭确认" @click="modal=null"><Icon name="close"/></button><div class="modal-symbol"><Icon :name="modal==='create'?'flag':modal==='exit'?'external':'history'" :size="28"/></div><div class="eyebrow">{{modal==='create'?'NEW MISSION / P-01':modal==='exit'?'LEAVE MISSION / P-07':'RESTORE PREVIEW / P-07'}}</div><h2>{{modal==='create'?'建立一场新的推演':modal==='exit'?'暂别，还是留下此刻？':'回到保存的时刻'}}</h2><p>{{modal==='create'?'从固定虚构地图开始，部署部队并安排协作任务。':modal==='exit'?'示例播放已暂停。选择保存记录后退出，或保留原记录直接离开。':'恢复后保持暂停，旧选择、视角和待处理内容需要重新核对。'}}</p><template v-if="modal==='create'"><label class="field-label">对局名称<input v-model="title" maxlength="60"/></label><div class="scenario-choice"><Icon name="map" :size="28"/><span><b>青岚河谷</b><small>固定虚构场景 · 三兵种协作</small></span><Icon name="check"/></div><button class="button primary full" :disabled="role!=='host'" @click="createGame">创建原型对局<Icon name="arrow" :size="17"/></button><p v-if="role!=='host'" class="small-hint">请切换主持人查看创建流程；操作员等待主持人建立对局。</p></template><template v-else-if="modal==='exit'"><button class="button primary full" @click="saveGame();navigate('history')">保存原型记录并退出</button><button class="button secondary full spaced" @click="navigate('history')">不新增保存，退出</button><button class="text-button full spaced" @click="modal=null">取消，返回已暂停沙盘</button></template><template v-else><div class="save-preview"><Icon name="history" :size="28"/><div><b>{{restoreTarget?.name||lastSaveName||'青岚河谷 · 检查点示例'}}</b><small>恢复步 {{restoreTarget?.step??savedStep??12}} · 原型暂停状态</small></div></div><button class="button primary full" :disabled="role!=='host'" @click="restoreGame">确认恢复示例<Icon name="arrow" :size="17"/></button><p v-if="role!=='host'" class="small-hint">需切换主持人查看恢复流程。</p></template></section></div>
    <div v-if="toast" class="toast-message" role="status"><Icon name="check" :size="17"/>{{toast}}</div>
  </div>
</template>
