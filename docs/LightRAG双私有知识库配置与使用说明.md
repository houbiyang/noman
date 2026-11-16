# LightRAG 双私有知识库配置与使用说明

更新时间：2026-11-10。项目：`/Users/kimi/Desktop/noman`。

> **交付状态：部署配置、原文冻结、后端检索接口及离线测试已完成；真实模型建库未验收，不能用于正式比赛。**
> Docker 引擎尚未运行，生成与嵌入模型尚未配置。本次没有启动 LightRAG、没有向云端发送项目资料、没有真实模型调用或费用记录。当前固定演示建议仍不是模型建议。

## 1. 已确认范围与重要区别

| 项目 | 本次配置 |
| --- | --- |
| 用途 | 红蓝总指挥未来可调用的只读检索工具；不实现总指挥模型、战斗规则或自动审批 |
| 规则集 | `conquest-v1`；冻结发布版本 `conquest-v1-20261110` |
| 资料 | 现有项目规则和协议的 15 个原文章节；无虚构作战资料、无共享公共库 |
| 隔离 | 两个实例、Docker 网络、管理密钥、图谱、向量、文档、状态与缓存目录分别独立 |
| 模型 | 用户后续填写已有在线 OpenAI-compatible 服务；生成、embedding 分别配置 |
| 云端边界 | 已获准处理双方资料；不因此授予读取敌方资料或自动上传实时态势的权限 |
| 运行位置 | 当前 Mac、Docker Compose；只向本机回环地址发布端口 |
| 存储 | JsonKVStorage / JsonDocStatusStorage / NetworkXStorage / NanoVectorDBStorage |
| 检索 | `mix`；关闭 rerank；不调用最终答案生成接口 |
| 建库负载 | 文件逐章串行、MAX_PARALLEL_INSERT=1、生成/嵌入异步并发 2 |
| 业务数据库 | 无迁移；不将现有 knowledge_* 表冒充 LightRAG 存储 |

### 1.1 目标规则不等于当前运行规则

本知识库的 150 米、仅合格陆兵占点、2 步占领／2 步中立化、连续稳定全控 3 步、60 分／60 步，来自目标设计与首轮测试参数，**不是已经验证的执行器规则或平衡参数**。

现有 `qinglan_control` 演示是 400 米阶段位置快照、地面／支援参评、20/40/60 分钟及 1/2/3 权重，不能与本库混用。本次不修改现有计分、固定建议、审批或地图。规则运行版本从当前对局快照读取；接口总是返回 `planning_only=true`、`execution_authorized=false`，青岚对局的 `matches_active_game_rules=false`。

即使未来对局版本恰好匹配，本接口仍不授予执行权限，必须经过既有规则校验和本方人工确认。

## 2. 文件与部署结构

```text
/Users/kimi/Desktop/noman/
├── infra/lightrag/
│   ├── compose.yaml                 # 两实例，固定镜像摘要，无 latest
│   ├── service.env.example          # 无真实模型密钥的模板
│   ├── upstream-lock.json           # 发布、OCI 摘要、平台与官方源码依据
│   ├── manage.py                    # 初始化、自检、串行入库、状态、激活、题集评测
│   └── freeze_corpus.py              # 仅创建新冻结版本，不覆盖旧资料
├── knowledge/conquest-v1-20261110/
│   ├── manifest.json                # 来源章节、原文行、版本、SHA-256
│   ├── cq-01.txt ... cq-08.txt
│   ├── agent-01.txt ... agent-07.txt
│   └── evaluation.json              # 22 个带预期来源的规则问题
├── backend/knowledge.py             # 原文白名单适配、版本与就绪检查
└── .local/lightrag/                  # 整体已被 Git 忽略，不上传
    ├── backend.json                 # 激活成功后产生，仅含 active_version
    └── releases/conquest-v1-20261110/
        ├── red/
        │   ├── service.env          # 当前用户 0600，含红库管理密钥和模型配置
        │   ├── storage/ inputs/ prompts/
        │   ├── preflight.json       # 模型自检后产生，不等于索引就绪
        │   ├── ingestion.json       # 入库追踪记录，中断后核对原 track_id
        │   ├── ready.json           # 所有审核文档处理完成后才产生
        │   └── evaluation-result.json
        └── blue/                    # 同样结构，但不共享数据或管理密钥
```

请求链路：红／蓝席位 Bearer → 现有 FastAPI → 本局席位授权 → 对应 LightRAG → 对应云端模型。

- 红库：`http://127.0.0.1:9622`；蓝库：`http://127.0.0.1:9623`。
- 容器内监听 `0.0.0.0:9621` 才能接收 Docker 转发，但宿主机仅发布 `127.0.0.1`。
- 两个 Docker 网络分别配置，不使用同一数据库或共享 volume。
- 管理端使用独立 `LIGHTRAG_API_KEY` / `X-API-Key`；业务客户端只持有自己的席位令牌。
- 本地 WebUI 供管理员维护，需要对应库的管理密钥；不要把管理密钥发给浏览器业务页面或未来 Agent。浏览器管理会话应与普通用户分开。
- 这不是对本机管理员、Docker 管理员或操作系统 root 的安全隔离；他们可读取双方本地数据。

### 2.1 上游版本核验

已核验 [官方 v1.5.7 发布](https://github.com/HKUDS/LightRAG/releases/tag/v1.5.7)（2026-09-02 发布）、tag 源码及 GHCR manifest，配置使用：

```text
ghcr.io/hkuds/lightrag:v1.5.7@sha256:5bdbd524931b011df246fe20888d110cef691e6804c12cde636a2b746d7de27e
```

官方 OCI 索引包含 `linux/amd64` 和 `linux/arm64`；Mac Apple Silicon 自动选择 arm64。核验不等于镜像已拉取、无安全漏洞或本机运行成功。不得因网络失败擅自改用 `latest`。

官方配置、接口、发布流程和 MIT 许可证链接见 `/Users/kimi/Desktop/noman/infra/lightrag/upstream-lock.json`。本次核验的是固定 tag 而非可能变化的 main 分支。

## 3. 首次配置与启动

### 3.1 Python 与本机初始化

在项目根目录操作。当前机器已有可用测试环境：

```sh
cd /Users/kimi/Desktop/noman
export PY="$PWD/.local/backend-test-venv/bin/python"
export KNOWLEDGE_RELEASE=conquest-v1-20261110
"$PY" infra/lightrag/manage.py validate
```

其他机器请建立虚拟环境并安装 `/Users/kimi/Desktop/noman/backend/requirements-dev.txt`。运行时的 httpx 已登记到 backend/requirements.txt，dev 文件也包含它，重复约束兼容。

本次已执行以下初始化，**当前机器不要重复执行**：

```sh
"$PY" infra/lightrag/manage.py init --version "$KNOWLEDGE_RELEASE"
```

初始化只产生本机目录和两套高熵服务密钥，不启动服务、不自动激活后端、不生成 ready。重复初始化会拒绝覆盖原目录和凭据。`.local/` 已在项目 `.gitignore` 中；不要使用 `git add -f` 上传它。

### 3.2 填写两份模型配置

分别在本机编辑红、蓝 `service.env`。不在聊天、说明文档或命令参数中粘贴密钥，也不要用会完整打印配置的 `docker compose config`（核对结构时用 `--quiet`）。

必须填写：

| 配置名 | 内容 |
| --- | --- |
| LLM_BINDING_HOST | 在线生成服务 HTTPS base_url，通常以 `/v1` 结尾，不含 `/chat/completions` |
| LLM_BINDING_API_KEY | 生成服务密钥 |
| LLM_MODEL | 供应商实际允许的模型名，需支持中文提取与 JSON 自检 |
| EMBEDDING_BINDING_HOST | 独立嵌入接口 HTTPS base_url，不含 `/embeddings` |
| EMBEDDING_BINDING_API_KEY | 嵌入服务密钥；不能假定聊天密钥有 embedding 权限 |
| EMBEDDING_MODEL | 实际可用的嵌入模型名 |
| EMBEDDING_DIM | 实测输出维度正整数，不直接套用示例维度 |

生成与嵌入可使用不同供应商；同一供应商账户也必须核实两类 API 是否均可用。红蓝可使用同一组**云端**模型服务信息；它们的 **LightRAG 本地管理密钥必须不同**。如需供应商侧隔离和独立账单，另开云端项目／密钥，本地两实例不会替代供应商隔离。

配置格式为简单 `KEY=value`，不要加 shell 命令、引号或行尾注释。base_url 不放密钥、用户口令或查询参数。保留模板中的认证及本地存储项，不添加免认证路径。`EMBEDDING_SEND_DIM=false` 默认不向服务强传 dimensions；只有供应商明确支持时才启用，随后重新自检。

**嵌入模型、维度、分词行为或语料变化必须重建新版本**。任何 service.env 配置变化会使就绪指纹失效；密钥轮换也会要求重新核对，不静默沿用旧 ready。若仅轮换密钥且确知模型未变化，仍应在新发布目录按流程验证，不手工伪造 ready。

### 3.3 模型连接自检（会产生真实 API 调用）

```sh
"$PY" infra/lightrag/manage.py preflight --side red
"$PY" infra/lightrag/manage.py preflight --side blue
```

此处发送固定的中文小样本，不发送全部规则。分别检查聊天连接、JSON 实体／关系结构和两个 embedding 的维度／有限数值，成功产生 `preflight.json`。模型不支持 JSON、401、429、超时、embedding 形状错误都会失败，不标记建库完成。

自检通过仅证明这两个小请求成功，不证明 LightRAG 能完成所有建图、供应商稳定性或比赛效果。不要默认自动切换模型或无限重试；外部超时不代表供应商未收费。

### 3.4 Docker 启动

要求 Docker Compose 2.30+（env_file 使用 raw 格式，防止密钥中的 `$` 被变量替换）；当前本机版本已满足。先由用户启动 Docker Desktop，确认 `docker info` 的 Server 正常。之后：

```sh
KNOWLEDGE_RELEASE="$KNOWLEDGE_RELEASE" docker compose -f infra/lightrag/compose.yaml config --quiet
KNOWLEDGE_RELEASE="$KNOWLEDGE_RELEASE" docker compose -f infra/lightrag/compose.yaml pull
KNOWLEDGE_RELEASE="$KNOWLEDGE_RELEASE" docker compose -f infra/lightrag/compose.yaml up -d
KNOWLEDGE_RELEASE="$KNOWLEDGE_RELEASE" docker compose -f infra/lightrag/compose.yaml ps
```

本机入口分别在 9622、9623；不要改为 `0.0.0.0:9622`，不要启用路由转发到局域网。`service.env` 改动后使用 `up -d --force-recreate`，单纯 `restart` 不重新加载 env_file。

排障时可在本机查看容器日志，**不要上传原始日志**。默认上游 `LOG_LEVEL=CRITICAL`、`VERBOSE=false`，减少问题与原文输出；供应商 SDK、异常栈、管理 WebUI、备份或排障临时开启日志仍可能含敏感数据。后端适配器自身只记请求 ID、阵营、版本、耗时和错误类别。

### 3.5 首次入库、状态与激活

```sh
"$PY" infra/lightrag/manage.py ingest --side red
"$PY" infra/lightrag/manage.py ingest --side blue
"$PY" infra/lightrag/manage.py status --side red
"$PY" infra/lightrag/manage.py status --side blue
"$PY" infra/lightrag/manage.py activate
```

每个实例分别通过官方 `/documents/text` 导入同一份审核原文，每章取得 track_id 后等待 `/documents/track_status/{track_id}` 的 `processed` 及非零 chunks_count，再导入下一章。**不要同时运行同一阵营的多个入库进程**，也不要在入库期间用管理界面修改或删除文档。

单章最长等待 20 分钟，中断保留 ingestion.json，不把超时当成成功；重跑将核对已记录的 track_id，不自动重复提交。若服务器已收件但客户端未保存回执，不能保证恰好一次，应由管理员先核对文档和费用，必要时重建版本，勿反复重试。

全部章节成功且 pipeline 不 busy／不要求恢复后，才原子写 ready.json。**不要手工创建 preflight、ingestion 或 ready**。

`activate` 再校验双方配置指纹、清单、服务处理状态和不同密钥后，原子写入 backend.json。这里的“激活”只表示对本机只读工具启用该版本，**不表示真实效果、隔离、恢复和正式比赛验收通过**。

每次业务检索还会核对活动版本、语料哈希、配置指纹、pipeline 以及各章处理状态，故空库、处理中、删除索引或配置变化不会仅因旧 ready 存在便返回成功。该检查增加本机 HTTP 开销，所有外部检查和检索共用 60 秒上限，未在数据库事务内等待。

## 4. 语料审核、更新与引用

### 4.1 本次导入的原文章节

- CQ-01～CQ-08：版本与状态、测试参数、三兵种资格、占点状态机、计分胜负、事件人工确认、信息存档公平性、旧模式及实施边界。
- agent-01：占点协同协议增量。
- agent-02～05：三层职责、四角色协议、通用输入包、输出结构与边界。
- agent-06：模型调用与失败协议。
- agent-07：证据、存档与分析边界。

正文不改写为模型生成摘要。manifest 保留来源文档、章节、冻结时原文起始行、原文 SHA-256 和 `target_design_test_baseline` 状态。当前来源行将来可能移动，**引用有效性以冻结文本与哈希为准**，而不是仅凭当前文件行号。规则中的 legacy 内容仅保留“禁止沿用”的原文边界，不导入旧任务规则主体。

没有导入青岚演示计分文档、数据库设计、开发计划、未确认的地形扩展数值、红蓝战术创作、对局实时世界、内部计划或复盘。也不扫描整个 docs 目录自动入库。

当前不提供任意文件上传业务接口。将来如有红蓝私有资料，先在各自受控本机审核目录放置，分别核对资料许可、来源、适用范围、提示注入和是否含实时／敌方隐藏信息，再设计各方独立清单与版本；**不要直接上传到当前已冻结规则实例**，否则未审核 chunk 会触发拒绝，且新增资料可能污染图谱。

### 4.2 新规则发布

```sh
# 使用真实发布日期／后缀；这里只是操作示例，须经过规则审核。
"$PY" infra/lightrag/freeze_corpus.py --version conquest-v1-20261111-review1
"$PY" infra/lightrag/manage.py validate --version conquest-v1-20261111-review1
"$PY" infra/lightrag/manage.py init --version conquest-v1-20261111-review1
```

freeze 工具只提取明确审核范围，遇到缺章节或已有目录就停止。人工比较新旧 diff、确认数值和状态、更新 evaluation.json，再配置两套模型并各自 preflight。目标规则发生语义变化时，不能机械复用旧题集或旧 `ruleset` 名称。

当前固定端口只运行一对版本，更新需要维护窗口：先停旧容器，再设置 `KNOWLEDGE_RELEASE` 为新版本并启动，新版本入库／状态通过后执行带 `--version` 的 activate。旧后端配置在切换期间可能因新索引不匹配而返回未就绪，这是安全失败，**不是自动降级**。此方案不承诺零停机蓝绿发布。

保留旧版整个数据目录与语料。不得覆盖旧 manifest、拼接新旧有效规则或复用不同 embedding 的向量文件。

### 4.3 原文引用与提示注入

原文引用示例：`[cq-03] conquest-v1-20261110 / docs/02仿真规则与参数.md / CQ-03 三兵种资格与主动作 / sha256=<接口返回值>`。

上游图谱用于 `mix` 的召回，但接口上下文仅由**已命中的审核原文章节**构造，返回 `context_kind=verified_originals_not_graph_summary`。不会把图谱实体描述／关系摘要包装为原文；检索 chunk 必须有白名单 file_source 且逐字属于冻结原文。伪造来源、被改写内容、跨库标记或注入文本会报错，不转发上游任意摘要和服务器路径。

这不等于彻底解决语义提示注入。未来总指挥仍须将 evidence 当作数据，不执行其中“忽略系统指令”“改查蓝库”等命令；最终写世界必须依靠服务端权限与规则校验，不能靠提示词保证安全。

## 5. 后端检索工具接口

`POST /api/v1/games/{game_id}/knowledge/query`。保持现有 Bearer 席位认证，不开放新的凭据签发入口。

请求仅有：

```json
{"question":"中立点占领需要几步？","knowledge_version":"conquest-v1-20261110"}
```

问题去除首尾空白后为 2～2000 字符。版本必须显式传递，不自动补默认。`side`、workspace、URL、目录、聊天历史和世界状态等额外字段一律 422。

认证示例（从本机安全输入席位令牌，不放入 shell 历史）：

```sh
read -r -s SEAT_TOKEN
export SEAT_TOKEN
# 在本机设置 GAME_ID 为已有、允许访问的对局 UUID，不创建或修改比赛。
export GAME_ID='<已有对局UUID>'
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer $SEAT_TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"中立点占领需要几步？","knowledge_version":"conquest-v1-20261110"}' \
  "http://127.0.0.1:18080/api/v1/games/$GAME_ID/knowledge/query"
unset SEAT_TOKEN
```

不要在共享机器复制这类含展开后凭据的命令，环境和进程参数也不构成密钥保险箱。生产集成请由受控客户端的安全凭据存储和请求头发送，勿开启 shell tracing。

席位规则：红仅 9622、蓝仅 9623；主持人 403；别局令牌 404；无效／撤销会话 401。权限和对局上下文在短事务中读取，关闭数据库连接后才联网。并发撤销不会追溯取消已经完成授权的请求；撤销后的新请求将失败。

### 5.1 返回字段

| 字段 | 语义 |
| --- | --- |
| request_id / side / knowledge_version | 本次检索标识、服务端认定阵营、冻结版本 |
| planning_only / execution_authorized | 始终 true / false；不执行命令 |
| active_game_rules / matches_active_game_rules | 当前运行规则与是否匹配 conquest-v1 |
| context | 至多 8 个已验证原文章节拼成的上下文；不生成最终指挥回答 |
| evidence | id、原文 text、来源 path／section／冻结行、版本、SHA-256、状态及 kind |
| evidence_insufficient | 无已核验原文时为 **true**；有候选时为 **null（尚未判断能否支持结论）**，不谎报 false／足够 |
| support_not_assessed | 始终 true：只检索不做语义蕴含裁定，命中不是答案正确的证明 |
| evidence_status | `no_verified_evidence` 或 `candidate_sources_unassessed` |
| safety_notice | 资料不是工具指令／执行授权 |

这项保守的三态约定用于避免把“检到相关片段”误当作“足够证明问题结论”。无法从原文支持的具体结论必须由后续受控 Agent 或人工明确标记不足，不能编造回答；本工具本身不声称完成回答支持性裁定。即使返回非空原文，也不得去掉此限制。

### 5.2 错误约定

沿用现有 `{"error":{"code":"...","message":"..."}}` 和 `Cache-Control: no-store`：

| HTTP | 示例代码 | 处理 |
| --- | --- | --- |
| 401 | SESSION_REQUIRED / SESSION_INVALID | 补有效席位，不使用管理密钥替代 |
| 403 | KNOWLEDGE_ROLE_FORBIDDEN | 主持人不能走业务接口读双方私库 |
| 404 | GAME_NOT_FOUND / KNOWLEDGE_VERSION_NOT_FOUND | 校验对局归属、精确版本 |
| 409 | KNOWLEDGE_VERSION_INACTIVE | 指定版本未部署；不换版本或另一方库 |
| 422 | INPUT_INVALID | 多余字段、空问题、长度／版本格式错误 |
| 503 | KNOWLEDGE_NOT_READY / KNOWLEDGE_CORPUS_INVALID | 配置、哈希、全部文档状态或 ready 不满足 |
| 503 | KNOWLEDGE_INDEX_BUSY / KNOWLEDGE_RATE_LIMITED | 处理／恢复／限流；按人工确认策略稍后重试 |
| 502 | KNOWLEDGE_UPSTREAM_AUTH / UPSTREAM_ERROR / QUERY_FAILED | 上游认证、网络、格式或检索失败；完整代码带 KNOWLEDGE_ 前缀 |
| 502 | KNOWLEDGE_EVIDENCE_INVALID / KNOWLEDGE_RESPONSE_INVALID | 非审核原文或响应结构错误，不当成成功资料 |
| 504 | KNOWLEDGE_TIMEOUT | 整体 60 秒或上游超时；供应商可能仍计费 |

不跨库兜底、不静默换模型、不把异常正文或供应商返回当作引用。后端不保留检索响应缓存，不自动转发聊天历史或快照，也不将查询入库；用户主动在问题中粘贴敏感信息仍会发给所选服务，需自行遵守资料范围。

## 6. 测试与分层验收

### 6.1 可复现命令

```sh
# 完全离线检索／管理测试，不依赖 Docker 或云模型
"$PY" -m unittest backend.tests.test_knowledge -v
# 当前本机测试入口：隔离临时 PostgreSQL 库 + 青岚／审批等现有回归 + 离线检索
"$PY" -m backend.portable test
```

portable 会使用项目专用本机 PostgreSQL，不接管其他数据库；测试随机数据库最终清理。不对现存比赛清库，不声称验证可选 pgvector。

### 6.2 真实建库后的题集评测

双方各完成 status 和 activate 后运行（检索关键词提取／embedding 可能计费）：

```sh
"$PY" infra/lightrag/manage.py evaluate --side red
"$PY" infra/lightrag/manage.py evaluate --side blue
```

evaluation.json 有 22 问。最低来源命中门槛为 18，所有 critical 项必须命中指定来源；结果分别存到两方本机 evaluation-result.json。这里只自动核对**支持章节是否召回**，必须逐题人工核对原文是否确实支持结论；不能用 `support_not_assessed` 的候选直接宣称答案正确。20+ 问题之外另测资料外问题（例如未定义兵种参数），须拒绝补造。

### 6.3 正式使用前仍需真实测试

请在**可销毁的两实例验收环境**中操作，勿向本次冻结发布库添加 canary：

1. 各写不同红／蓝标记、实体和关系，以各自管理密钥验证图谱、检索结果和缓存文件不能串库；再重复查询、重启并复查。当前离线测试只证明端口、凭据选择和未知来源拒绝，不证明上游真实图谱／缓存隔离。
2. 用真实对局席位请求：红／蓝正常、主持人拒绝、别局令牌拒绝、伪造 side 拒绝、撤销后拒绝。真实 DB 的角色和跨局测试已自动覆盖，真实 LightRAG 网络链路仍待跑。
3. 加带恶意指令的临时文档，确认不扩大权限；当前只允许审核规则，非白名单／非原文片段将被后端拒绝。不要将验收恶意文档留在比赛库。
4. 测空库、处理中、401、429、维度不匹配、生成失败、服务不可达和超时，不得获得伪成功或另一方资料。
5. 实际停止／启动容器后重复查询；使用真实索引备份进行恢复，再复查题集、文档计数、来源、哈希及双方 canary。离线文件复制恢复不是 Docker／真实索引恢复验收。
6. 核查监听地址、无管理密钥进业务客户端、供应商账单及资料保留政策。

### 6.4 本次实际记录

| 检查项 | 结果 |
| --- | --- |
| 固定 v1.5.7 发布、源码、官方镜像 OCI 索引及 arm64 | 已在线核验，摘要已记录 |
| 15 章冻结哈希与来源逐字一致、排除 400 米青岚依据 | 已通过静态测试 |
| 两套本机配置与不同服务密钥、文件权限 0600 | 已生成与检查；模型仍为占位符 |
| Docker Compose `config --quiet` | 已通过，仅配置解析，无需 daemon |
| 检索、原文校验、权限契约、故障、管理自检／串行入库模拟 | 41 项离线测试通过；全部网络用 MockTransport |
| 现有青岚／地图／报告／计分／审批回归及新增真实席位授权 | 与上述离线测试合计 110 项通过（最终命令记录见本次交付） |
| 模型未填写时 preflight | 已验证失败退出，不请求模型、不产生 ready |
| Docker daemon | 未运行，socket 不存在；未启动容器 |
| 真实模型连接与付费调用 | 未执行，待配置 |
| 实际建图、22 问来源命中率、真实图谱／缓存隔离 | 未执行，不得写为通过 |
| Docker 重启与真实索引备份恢复 | 未执行；仅离线文件恢复模拟通过 |
| 正式比赛可用 | **否**：还缺真实模型、索引和完整验收；conquest 执行器也不是本次范围 |

## 7. 备份、恢复、回滚

### 7.1 一致备份

本地文件／图／向量必须共同备份，不能只备份 graphml 或向量 JSON。暂停业务检索，在维护窗口停止两实例，确保没有入库进程：

```sh
KNOWLEDGE_RELEASE="$KNOWLEDGE_RELEASE" docker compose -f infra/lightrag/compose.yaml stop
umask 077
mkdir -p .local/lightrag-backups
BACKUP=".local/lightrag-backups/lightrag-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$BACKUP" .local/lightrag knowledge infra/lightrag
shasum -a 256 "$BACKUP" > "$BACKUP.sha256"
KNOWLEDGE_RELEASE="$KNOWLEDGE_RELEASE" docker compose -f infra/lightrag/compose.yaml up -d
```

归档包括双方资料及 service.env 凭据，按最高敏感级别保管，限制权限并在外部备份介质加密；不得上传 Git、公共网盘或聊天。哈希旁文件不能替代防篡改／加密机制。

### 7.2 恢复

先停止服务、校验备份 SHA-256。**先在独立临时目录解压核对内容**，不要直接 `tar` 覆盖仓库、模型密钥或当前资料。将现有 `.local/lightrag` 改名保留，恢复完整备份目录，权限设为目录 0700、凭据／标记 0600；确保冻结 manifest 和原文为同一备份版本。恢复配置版本对应的 Compose，运行双方 status、activate、evaluate 和权限隔离测试。核验全部通过前不恢复业务使用。

若只需要切回已留存旧版本：停止新版本 → 设置旧 KNOWLEDGE_RELEASE → 启动旧实例 → `status --version <旧版> --side red/blue` → `activate --version <旧版>` → 复核查询。配置变更会导致旧指纹失效，不能通过手改 ready 绕过。失败时返回未就绪，不借用新版本／对方索引。

`docker compose down` 不会删除本次绑定目录，但仍先备份；不要执行 `rm -rf .local`，它还包含现有业务数据库和其他运行资料。

## 8. 常见故障与成本／隐私

| 现象 | 排查 |
| --- | --- |
| Docker socket 不存在 | Docker Desktop 未启动；启动后再 docker info，不以配置解析通过代替运行成功 |
| compose 缺 KNOWLEDGE_RELEASE | 在同一终端 export，或命令前显式赋值；勿删必填校验 |
| 模型未补齐 | 分别编辑红蓝 service.env 所有占位符；自检不会自动猜测供应商或维度 |
| 生成可用但 embedding 失败 | 检查独立 endpoint、模型权限、维度、float 编码及 dimensions 支持，不复用聊天接口路径 |
| 文件持续 pending／failed | 在本机管理端核对 track、供应商限额、模型上下文和格式，必要时销毁失败版本重建 |
| ready 存在但 503 | 配置指纹／manifest 变化、缺章、空库、处理或恢复状态；ready 不是免检凭证 |
| 版本 409 | 客户端明确版本与 backend.json 不一致；走审核切换流程，不改成隐式 latest |
| 证据 502 | 可能是未审核来源、chunk 改写／解析变化、错误实例或污染；不要关闭校验，应核对冻结文本与固定上游 API |
| 题集未达标 | 检查原文解析、生成模型实体提取、embedding 中文效果，再在新版本调参；不要修改题集掩盖失败 |
| 超时 | 可能是状态检查、embedding、关键词或供应商等待；降低单次负载／确认限流，禁止无界重试 |

云端可能接收：建库全部审核原文、实体关系／摘要请求，以及检索问题、关键词和 embedding 输入。资料没有因为本地部署就变成离线处理；供应商留存、跨境和训练使用政策需用户另行确认。首批两方规则相同，不代表今后私有资料可以互传。

主要费用来自：红蓝**各自一次**嵌入和建图／摘要、检索 embedding／关键词提取、手工重试和版本重建。检索不生成最终指挥回答，但也不是零生成模型调用。未取得真实模型用量和单价前不估造总价；请在供应商设置预算／额度提醒，初次逐章观察用量后再继续。

升级固定版本须重新核验发布、许可证、镜像摘要／架构、配置和 `/query/data`、文档追踪结构，并重复测试，不原地升级正在比赛的索引。云端模型名背后的权重也可能变化，同名不保证相同 embedding 空间。
