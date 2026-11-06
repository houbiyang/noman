# 后端实施状态与本机运行

## 兵营页面后端接入（2026-11-06）

军队页面统一使用三类兵营卡片和地图选址，通过现有统一命令入口提交 `place_barracks`（`side`、`unit_type`、`position`）和 `delete_unit`（`unit_id`）。`006_barracks_commands` 扩展既有命令约束，业务表数量仍为二十张。

兵营按阵营和兵种保存独立身份，放置和重新选址限准备阶段，校验完整占地、部署区、建筑、道路接入及已有兵营。重新选址保留已有单位位置，空间测试资源只为尚未初始化的单位赋予冻结测试初值。删除限准备或暂停阶段，删除活动路线和编队引用，保留兵营及历史，并重置本方准备和建议依据；允许某兵种删除至零。所有操作沿用原身份校验、事务、幂等回执、快照及存档恢复。

旧版共用兵营和旧 `set_preparation`／预览接口继续兼容。现有对局通过最新视图读取兵营数据，页面中的单位数量、血量未知提示和资源状态来自本席位投影。真实 AI 自动出兵及正式交战规则继续按原交付范围实施。

## 当前数据库基线：仅保留青岚 v6（2026-11-04）

用户已明确授权清空旧对局、存档和旧地图版本。当前库只保留一个青岚 v6 场景和一个测试规则版本；旧对局和令牌已失效。清理前备份已经隔离恢复验证。启动和初始化不会自动清库，也不会重建旧场景。

当前地图完整保存 58,081 个高程值、144 个源道路节点、6 个派生桥面节点、243 条道路、3 个桥梁、1,769 个建筑实例、25 个材质定义、11 个点位和 2 个部署区；125 个资源文件另存相对引用和摘要，二进制资源仍位于 Unity 工程。`005_map_catalog` 已增加新地图结构约束与目录索引，业务表仍为二十张。

具体字段见 `map_catalog.py` 及 `scenario_versions.definition.map`；几何和资源来源完整保存在 `source_payload` 与 `asset_manifest`，桥头及正式作战参数仍待复核。完整证据见 [当前地图入库与数据库优化](Verification/当前地图入库与数据库优化_2026-11-04.md)。以下 S1/S2 和早期数据库章节保留原实施过程，其中清理前的演示局、旧版本数量和历史令牌不再是当前有效数据。

## S1–S2 空间测试闭环：2026-11-04

用户已确认先实现可配置测试规则。新增 `backend/s2-test-profile.json`、`spatial.py`；新建局显式 `spatial_test=true` 才启用，原演示局保持原版本。参数和地图摘要生成独立场景及规则版本，快照冻结全部测试参数；修改配置后需重新初始化登记新版本，不覆盖旧资产。

```powershell
# 启动并登记独立测试规则版本
& .\backend\start-local.ps1 -Initialize
# 在另一终端创建测试局并写入受限凭据文件
python -X utf8 -m backend.local_demo --spatial-test --title 'S2空间测试'
# 独立验证测试空间规则
python -X utf8 -m unittest backend.tests.test_spatial
```

页面本方“军队”进入三类兵营卡片，通过地图选址提交本方兵营；“任务与协作”提供统一兵营页面入口和准备状态。双方有效准备及建议采用后，单位“地图移动”提交路线，主持人单步推进。单位管理卡片显示测试能源、补给和耐久。旧预览接口可用的测试示例位置为红方 `10.3/26.2`、蓝方 `100.3/50.2`（120×80 业务坐标）。

新增 `POST /games/{game_id}/preparation/preview`，正文为 `context` 和 `preparation`（side、position、quantities）；返回绑定席位和输入的短期 preview_token。`set_preparation` 命令需携带相同输入及预览令牌；空 position 表示准备中撤销。开局后禁止改编制。存档恢复保存兵营、编队、资源、路线、航段游标和距离；恢复不重复扣能源。

测试几何采用方形占地、九点高度采样、建筑保守包络及道路接入段，尚非正式精确碰撞和通行规则。补给与耐久先保存为状态，本阶段不实现 S3–S4 的保障、战损和任务裁决。测试参数不作为正式作战参数。

## 现有功能后端：2026-11-03

已新增 FastAPI HTTP 服务，承接当前 Unity 已有的演示交互，状态真实存储到 PostgreSQL 新库。服务只监听本机 `127.0.0.1:18080`；接口说明为 `http://127.0.0.1:18080/docs`，健康检查为 `http://127.0.0.1:18080/api/v1/health`。根地址未提供控制台页面。

| 已有交互 | 当前后端行为 |
| --- | --- |
| 三席位与对局 | 独立引导权限创建演示局、签发单席位会话；令牌摘要入库，支持过期与撤销 |
| 军队配置与地图部署 | 保存每方三类单位数量与编队位置；校验角色、坐标及完整编队越界，保留稳定单位编号 |
| 单位机动 | 保存现有演示机动目标；不冒充已完成正式地形寻路、移动耗时或资源消耗 |
| 四类任务选择 | 主持人准备阶段选模板，双方重新准备；节点进度明确为尚未裁决 |
| 固定演示建议 | 本方批准、修改后批准、拒绝及重新载入；保存冻结原文、依据摘要和处理记录 |
| 时间控制 | 主持人开局、暂停、单步及继续；双方建议未采用时禁止推进；后台每两秒发起一个演示步，第六十步结束 |
| 四类事件 | 主持人先预览再登记，预览绑定输入、席位和状态，五分钟有效；当前不施加故障、通信或天气效果 |
| 存档与退出 | 自动存档、独立手动存档、保存后退出；读档校验摘要并建立新分支，保持暂停 |
| 历史和复盘 | 角色过滤的历史目录、继承分叉前缀的时间线、冻结分页游标、只读快照复盘 |
| 重试与竞争 | 幂等命令、原请求结果查询、签名上下文、对局行锁、单事务提交及失败回滚 |

本次仅修改后端与其说明，Unity 的 `DemoSandboxViewSource` 尚未切换为 HTTP 数据源，不能视为 Unity 联调完成。新建演示局从第零步开始，不沿用 Unity 展示用的第十八步。当前地图显示坐标为十二公里范围，演示业务坐标独立使用 `unity-demo-120x80-v1`，横向 0–120、纵向 0–80；二者不能混用。当前每类 1–10 个单位、0–20 建议额度和每步 0.45 的预置位移均来自演示逻辑，不作为正式作战参数。

### 启动与联调

在项目根目录运行以下 PowerShell 命令，使用本机已有 Python 和依赖，无需安装新包：

```powershell
# 首次运行或应用本轮迁移，并显式登记当前演示版本
& .\backend\start-local.ps1 -Initialize

# 后续正常启动；Ctrl+C 只结束当前前台后端进程
& .\backend\start-local.ps1

# 在另一终端显式创建演示局及三席位私有联调文件
python -X utf8 -m backend.local_demo --title '现有功能后端联调'

# 查看健康状态，不返回凭据
Invoke-RestMethod 'http://127.0.0.1:18080/api/v1/health'

# 隔离数据库集成测试与数据库约束测试
python -X utf8 backend/tests/test_service.py
python -X utf8 backend/tests/test_database.py
```

`-Port` 可指定其他本机端口；`-NoScheduler` 可关闭自动播放，仅使用单步命令。入口检测端口占用，不停止已有服务。测试必须能够访问本项目管理配置，以创建和清理随机命名测试库；HTTP 业务操作始终使用受限应用角色。

联调工具只在终端输出对局标识和文件路径，不输出席位令牌。文件存放在受 Windows ACL 保护且被 Git 忽略的 `backend/.local/` 内。对应的 `.request.json` 在创建前保存原请求键；如果响应丢失，通过创建结果接口核对原键，不要盲目再建。每次完整联调文件写入一次，不覆盖旧凭据文件。引导密钥来自现有 `.local/bootstrap-key`，签名密钥存于 `.local/backend-runtime-v1.json`；签名配置重启复用，不在启动时重置。

### HTTP 契约

下列路径均以 `/api/v1` 为前缀。业务响应设置 `Cache-Control: no-store`；不提供数据库密码下载或整局快照上传接口。

| 方法与路径 | 权限及用途 |
| --- | --- |
| `GET /health` | 公开就绪检查，分别标识数据库、真实 AI、正式作战和正式寻路能力 |
| `POST /games` | `X-Bootstrap-Key` 与 UUID `Idempotency-Key`；创建输入必须显式 `is_demo=true` |
| `GET /game-creations/{creation_key}` | 引导权限按原键核对创建结果 |
| `GET /bootstrap/games` | 引导权限查看目录摘要 |
| `POST /bootstrap/games/{game_id}/sessions` | 引导权限；正文 `{"role":"host"}`，也可为 `red` 或 `blue` |
| `GET /session`、`DELETE /session` | Bearer 会话查询、撤销 |
| `GET /games`、`GET /games/{game_id}/view` | Bearer 仅访问绑定对局和自身可见内容 |
| `GET /games/{game_id}/routes` | Bearer 查询当前固定地图道路拓扑路线；不执行仿真步 |
| `GET /maps/current` | 有效会话读取公开地图身份及节点道路摘要，不下载大型显示网格 |
| `GET /games/{game_id}/proposals/{proposal_id}` | 仅对应方可读私有固定建议，主持人也不能读取草稿 |
| `POST /games/{game_id}/conditions/preview` | 主持人预览现有四类事件登记 |
| `POST /games/{game_id}/commands` | 统一变更入口，必须提供 UUID `Idempotency-Key` |
| `GET /games/{game_id}/commands/{command_id}` | 仅原提交席位核对收据 |
| `GET /games/{game_id}/commands/by-request` | 以 `origin_branch_id`、`request_key` 核对原收据 |
| `GET /games/{game_id}/saves` | 主持人读取存档摘要，`offset`、`limit` 分页 |
| `GET /games/{game_id}/events` | 过滤角色后分页，使用返回的 `next_cursor` 继续查询 |
| `GET /games/{game_id}/replay` | 可选 `simulation_step`、`snapshot_id`、`branch_id`；进行中仅主持人，结束后各方只读自身投影 |
| `GET /games/{game_id}/result` | 第六十步后返回未裁决状态及空胜方，不虚构成绩 |

写入正文必须使用最近一次 `/view` 返回的完整 `context`，不能自行组装快照内容：

```json
{
  "context": {
    "game_id": "从视图原样取得",
    "branch_id": "从视图原样取得",
    "snapshot_id": "从视图原样取得",
    "context_token": "从视图原样取得"
  },
  "command_type": "configure_army",
  "payload": {"side": "red", "quantities": {"recon": 2, "ground": 3, "support": 1}}
}
```

上例的标识文字是占位说明，实际必须为服务返回的 UUID。支持命令及载荷的完整可执行类型见 [contracts.py](contracts.py) 与 OpenAPI；事件类型为 `equipment_fault`、`communication_outage`、`weather_change`、`task_change`，当前演示契约都要求 `target_unit_id`。事件登记、文本方案和演示部署契约与待实施的正式规则契约明确分开。

请求重试必须保留原席位、原请求键和原正文；收据中的 `replayed=true` 表示已返回原提交结果。替换输入后必须使用新请求键。状态冲突返回 409，应先重新读取视图；业务拒绝可能返回带 `status=rejected` 的持久化命令收据，入口参数或身份错误返回 `error.code/message`。数据库异常返回不含 SQL 的 503，调用方应先按原键核对结果。另一方单位位置、私有建议正文和内部控制会话不会出现在无权访问的投影中。

后台节奏按本机单进程运行设计；相同依据的重复调度可去重，但未实现多实例全局节奏协调或集群租约。当前控制席位撤销或过期后，下一次调度把对局持久化为暂停。进程重启后状态从数据库读取；有效控制会话仍在运行的演示会继续播放，暂停局不会自行推进。

验证证据见 [现有功能后端验证记录](Verification/现有功能后端验证_2026-11-03.md)。

## 当前数据库：2026-11-03

已在本项目既有 PostgreSQL 18.4 实例 `127.0.0.1:55433` 中建立新库 `qiongtu_sandbox_v1`，业务 schema 为 `sandbox`。旧库 `battle_sandbox` 保留，不导入、不覆盖其历史数据。当前设计的 **20 张业务表全部建成**，另有一张独立迁移记录表；现有演示交互 API 已实现，正式确定性规则及 Unity 联调仍待实现。

当前应用迁移为 `001_application`、`002_vectors`、`003_control_guards`、`004_current_ui_commands`。第四次迁移只补充现有交互命令枚举，不增加业务表或正式作战权限。pgvector 0.8.7 已由官方源码编译并在新库启用，`chunk_embeddings.embedding` 使用原生 `vector` 类型；未选定正式模型和维度，因此没有建立近似向量索引或导入真实向量数据。默认 Setup 包含全部表，保留 `-EnableVectors` 参数兼容旧命令。

### 管理入口

在本项目根目录执行：

```powershell
# 查询实际状态，不显示密码
& .\backend\setup-database.ps1 -Action Status

# 首次建库或执行新增迁移；重复执行核对已应用文件摘要
& .\backend\setup-database.ps1 -Action Setup

# 在随机命名的隔离库运行真实 PostgreSQL 验证；结束后只清理本次测试库
& .\backend\setup-database.ps1 -Action Verify

# 备份新库，保存到 backend\.local\backups-v1
& .\backend\setup-database.ps1 -Action Backup
```

工具使用本机已有 Python 标准库与 `D:\PostgreSQL\18\bin` 工具，不使用 ORM 或额外迁移框架。实例停止时入口启动本项目既有实例，核对控制文件系统标识，拒绝操作指向其他实例的端口；只监听回环地址，不修改防火墙或创建系统服务。需要停止实例时，应先停止连接此实例的客户端，再运行现有 `pg_ctl.exe stop -D backend\.local\pgdata`；此操作会停止本实例中的全部数据库，不能当成只停止新库。

本轮新增的运行依赖只有 pgvector。编译所需 Microsoft C++ 组件及 Windows SDK 只解压到 `backend/.local/pgvector-build/`，未安装完整 Visual Studio。由于 PostgreSQL 程序目录不可写，扩展使用独立用户目录，初始化器依据 `backend/.local/pgvector-installation.json` 校验实际文件摘要，再仅对明确目标库配置 PostgreSQL 18 的 `extension_control_path`。迁移或恢复到另一台电脑前，必须重新准备同主版本扩展及正确目录，不能只复制数据库备份。详见 [扩展安装记录](Verification/pgvector安装记录_2026-11-03.md)。

### 应用连接与权限

| 配置项 | 当前值或位置 |
| --- | --- |
| 地址与端口 | `127.0.0.1:55433` |
| 数据库 | `qiongtu_sandbox_v1` |
| 业务 schema | `sandbox` |
| 应用角色 | `qiongtu_app_v1`，非超级用户、不能建库或建角色 |
| 应用私有配置 | `backend/.local/application-database-v1.json`；密码不写入本说明 |
| 脱敏目标配置 | `backend/.local/database-v1.json` |
| 管理凭据 | 沿用本项目已有私有文件；不发给 Unity 或浏览器 |
| 验证摘要 | `backend/.local/database-verification-v1.json` |

私有配置使用 Windows ACL 限制为当前用户和 SYSTEM，并由已有 `.gitignore` 排除。应用账号只能读取、插入业务表，以及更新明确允许的列；不能修改 Schema、读取迁移管理表、删除历史，或覆盖不可变快照及手动存档。角色隔离仍由后端会话与字段投影实现，本轮没有启用 RLS，也没有账号登录页面。

### 当前实施边界

- 数据库已实现归属复合外键、提交时循环外键、幂等唯一键、状态／格式／JSON 顶层检查、不可变内容保护及控制代次不回退。
- 快照通过版本化 JSON 承载单位、地形动态覆盖和任务状态；SQL 只验证顶层及生命周期，不代替领域对象、地图几何、守恒和任务合法性校验。
- `scenario_versions.definition.map` 与 `rule_versions.definition.map_policies/unit_profiles` 可以承接地形和兵种参数；未导入地图或未经确认的正式系数，未生成虚假正式对局。
- 应用必须锁定对局控制行，校验角色、有效头、执行世代和任务租约，在同一事务提交命令、快照、事件和后续任务。数据库表存在不表示执行器或游戏服务已实现。
- 向量表、维度与来源复合外键、唯一约束、历史保护及按空间查询索引已实际创建；原生向量读写和备份恢复已验证。实际模型、正式维度、余弦零向量输入校验及完整检索权限流程仍需后端实现。
- 当前备份为数据库自定义格式；尚无正式资料及检查点，不将其描述成已验证跨资源完整恢复。测试中的备份恢复仅覆盖隔离库的测试夹具。

实际验证范围见 [数据库搭建验证记录](Verification/数据库搭建验证_2026-11-03.md)，逐表结构核对见 [数据库表清单](Verification/数据库表清单_2026-11-03.md)。以下旧版章节继续保留为历史资料，其中接口、旧运行脚本和测试结果不属于本次新库交付。

---

> 历史说明（2026-10-31）：为重新开发，旧后端源码、测试、场景、数据库迁移、启动脚本与依赖清单已移出，保存在 `D:\无人作战旧代码归档\鲲鹏鸿蒙无人智能作战地图沙盘_20261031_213131\backend`。本目录保留 `.local/` 本地数据库与凭据、`.gitignore` 和本说明。以下实现状态、测试记录与运行命令仅描述移除前的版本，不代表当前目录仍可启动旧服务。

本目录是鲲鹏鸿蒙无人智能作战地图沙盘的后端实施起点。本机启动脚本默认加载明确标记的固定演示场景（`is_demo=true`），可完成三席位准备、巡查演示、逐步结算、手动存读档和复盘。传入 `-ScenarioMode formal` 可加载首版正式场景，但真实智能体建议和完整规则尚未就绪，正式对局在首次方案阶段保持等待，不能用演示确认命令绕过。现有 HTTP 服务提供受控创建、角色投影、任务配置预览与提交、命令幂等及结果核对、历史目录、快照列表和角色隔离复盘。所有 `/api/v1/` 响应设置 `Cache-Control: no-store`。

## 当前验证范围

| 批次 | 当前状态 | 证据与限制 |
| --- | --- | --- |
| B0 | 部分完成 | 两份固定 JSON 样例、最小跨字段校验、接口入口及 PostgreSQL 迁移已落地；D-07 至 D-09 的全量契约仍待锁定。 |
| B1 | 固定演示闭环完成 | 演示场景创建、三席位、巡查任务配置、准备状态、演示方案确认、沿显式道路移动、到场后独立巡查、抽象交战、逐步推进、终结计分、快照与命令同事务提交已在隔离库验证；这不是正式规则验收。 |
| B2 | 部分完成 | 前端经 Vite `/api` 代理可读取服务端 `GameView`，执行准备、演示开局、暂停/继续/推进并读取结果；四类任务的正式规则仍未完成。 |
| B3 | 部分代码，未完成 | 已有 DeepSeek 结构化输出适配、本地中文 embedding 适配、检索权限与原文校验的纯逻辑；未配置密钥、模型文件和资料，未执行真实调用、数据库向量检索或 LangGraph 流程。 |
| B4 | 固定演示规则，正式规则未完成 | 固定巡查、沿场景道路移动、陆兵抽象交战、节点完成和终结可演示；护送、保护、撤离、保障交接、视野事件及完整事件脚本仍未实现。 |
| B5 | 演示部分完成 | 自动快照、多个不覆盖手动存档、读档新分支、重启后读取及只读角色复盘已在隔离库验证；完整运行态格式、退出流程和正式对局恢复仍未完成。 |
| B6 | 未完成 | 当前仅执行局部单元、HTTP 和隔离 PostgreSQL 测试，未进行全范围验收或目标设备实测。 |

设计依据及仍待确认事项见上层 [后端实施计划](../docs/10实施计划与交付范围.md#backplan)和[决策台账](../docs/00文档索引与决策台账.md#index)。测试配置的坐标、移动量和单位属性不作为产品正式参数。

## 本机启动

本工作区已创建隔离 PostgreSQL 18 实例，监听 `127.0.0.1:55433`，数据库名为 `battle_sandbox`；独立于已有的 5433 实例。隔离实例文件和凭据放在 `backend/.local/`。当前工作区不是 Git 仓库；若以后纳入版本管理，必须排除整个 `.local/` 目录。启动脚本只检查已执行迁移，不自动创建或覆盖数据库。

在 PowerShell 中从 `D:\无人作战\鲲鹏鸿蒙无人智能作战地图沙盘` 执行：

```powershell
& .\backend\start-local.ps1
```

服务只监听 `127.0.0.1:18080`，打开 `http://127.0.0.1:18080/` 可进入已构建的对局控制台，基础健康地址为 `http://127.0.0.1:18080/api/v1/health`。若控制台静态文件尚未生成，先在 `sandbox-prototype` 目录执行 `npm run build`。本机引导密钥保存在 `backend/.local/bootstrap-key`，须由受控本机操作读取；不要复制到前端代码、URL 或日志。停止前台服务使用 `Ctrl+C`。数据库单独运行；需要停止隔离实例时执行：

若要检查正式场景的准备态阻塞边界，可执行 `& .\backend\start-local.ps1 -ScenarioMode formal`。该模式目前不能演示完整对战。

服务启动后，打开本机控制台会直接创建固定演示对局并自动取得主持人席位；页面可在同一局切换红方、蓝方席位，刷新后按地址中的对局标识重新签发当前席位令牌。自动进入接口仅由本机启动脚本启用，并限制回环地址访问。受控脚本 `& .\backend\new-demo-game.ps1` 仍可为独立联调创建对局并把三席位令牌写入本机受限文件。红蓝双方准备后，主持人可开局、确认演示方案、逐步推进、手动存读档并读取最终结果。令牌不要复制到公开文档或版本库。

```powershell
& 'D:\PostgreSQL\18\bin\pg_ctl.exe' -D 'D:\无人作战\鲲鹏鸿蒙无人智能作战地图沙盘\backend\.local\pgdata' stop
```

## 迁移与测试

迁移文件按编号显式执行，当前隔离库已应用 `001_initial`、`002_creation_requests`、`002a_core_integrity` 和 `004_manual_saves`；同局、同分支外键及演示存读档已在隔离库验证。`003_knowledge_vectors.sql` 已编写，但本机 PostgreSQL 18 尚未安装 pgvector 服务器扩展，因此**没有执行**，也没有任何可用向量索引。新环境需要先准备 PostgreSQL 18、数据库和私有连接凭据，再逐个执行所需 SQL 文件；不要对已有业务库直接运行。

```powershell
python -m unittest discover -s backend\tests
```

如果没有 `BATTLE_DATABASE_URL`，数据库测试显示跳过。对本工作区的隔离实例执行真实数据库测试时：

```powershell
$env:BATTLE_DATABASE_URL = "host=127.0.0.1 port=55433 dbname=battle_sandbox user=battle_admin password=$(Get-Content -Raw 'backend\.local\admin-password')"
python -m unittest discover -s backend\tests
Remove-Item Env:BATTLE_DATABASE_URL
```

测试会在隔离数据库新增带随机标识的对局，不清理历史；不会访问 5433 实例。

## 已实现接口

| 方法 | 路径 | 当前行为 |
| --- | --- | --- |
| GET | `/api/v1/health` | 区分数据库就绪与内存演示模式。 |
| POST | `/api/v1/games` | 本机引导密钥与创建幂等键共同校验，按当前登记场景创建对局；本机启动脚本默认使用演示场景。 |
| GET | `/api/v1/game-creations/{request_key}` | 使用同一引导权限核对已提交的创建结果。 |
| POST | `/api/v1/bootstrap/games/{game_id}/sessions` | 本机引导密钥签发指定单席位令牌，数据库仅保存令牌摘要。 |
| POST | `/api/v1/local-entry` | 仅在本机启动脚本启用且请求来自回环地址时，自动创建或进入当前对局并签发单席位令牌。 |
| GET | `/api/v1/session` | 返回当前令牌绑定的对局和席位。 |
| GET | `/api/v1/history` | 返回当前席位可见的正式对局目录，支持未结束/已结束筛选与分页。 |
| GET | `/api/v1/games/{game_id}/view` | 返回最小准备态角色视图；主持人可见双方准备状态，操作员仅见本方。未定义观察或建议结构时拒绝投影。 |
| GET | `/api/v1/games/{game_id}/saves` | 仅本局主持人读取自动快照和手动存档；运行或暂停阶段的演示快照可读档。 |
| GET | `/api/v1/games/{game_id}/replay` | 按分支与仿真步或快照标识返回当前席位可见的只读复盘帧。 |
| POST | `/api/v1/games/{game_id}/task-configuration/preview` | 无副作用预览双方巡查任务输入；令牌绑定当前快照、席位和输入修订。 |
| POST | `/api/v1/games/{game_id}/commands` | 演示对局执行任务配置、准备、开局、演示方案确认、暂停、继续、单步推进、手动存档和读档；读档原子切换新分支。正式场景不开放演示方案确认与推进。 |
| GET | `/api/v1/games/{game_id}/result` | 对局结束后读取冻结的胜负、分项分数和权重结果。 |
| GET | `/api/v1/games/{game_id}/command-results?request_key=...` | 仅原提交席位可核对已提交收据。 |

通过 `Authorization: Bearer <会话令牌>` 访问对局接口。`Idempotency-Key` 使用 UUID；重试时必须保留原请求正文与原上下文。令牌不通过 URL 授权。当前 `/api/v1/bootstrap/...` 是受控本机引导入口；`/api/v1/local-entry` 只用于本机控制台自动进入，不应在对外服务中启用。

## 模型与检索接入边界

DeepSeek 适配位于 `deepseek_client.py`，使用官方 Chat Completions 接口，默认模型标识为 `deepseek-flash`。密钥只读取后端 `DEEPSEEK_API_KEY` 环境变量；缺失、网络失败、空 JSON 或截断响应都不会变成成功建议。当前仅使用模拟响应测试了适配层，尚未进行真实付费调用。实际模型名、版本、用量和错误需在 AI 运行记录中持久化后才能进入完整闭环。接口依据见 [DeepSeek 官方首次调用说明](https://api-docs.deepseek.com/api/responses)与[JSON 输出说明](https://api-docs.deepseek.com/guides/json_mode/)。

本地 embedding 选择 `BAAI/bge-small-zh-v1.5` 作为本机中文资料的候选模型，维度 512。`local_embeddings.py` 仅从显式本地目录加载 `model.safetensors`，不会自动下载；用权重、分词器和嵌套推理配置等本地文件的 SHA-256 指纹隔离索引空间。对应可选推理依赖写在 `requirements-embeddings.txt`，当前尚未安装，也未取得真实模型文件。模型资料见 [BAAI 模型页](https://huggingface.co/BAAI/bge-small-zh-v1.5)和 [Sentence Transformers 加载接口](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html)。

接入时，在启动后端的同一个 PowerShell 会话中设置 `DEEPSEEK_API_KEY` 和 `BATTLE_EMBEDDING_MODEL_DIR`；密钥只保留在本机环境中，不写入仓库，也不要粘贴到聊天。模型目录应为已下载的 `BAAI/bge-small-zh-v1.5` 本地文件夹，至少包含 `model.safetensors` 与 `config.json`。模型文件与资料样例就绪后，再安装 `requirements-embeddings.txt` 并执行真实推理、检索与引用验证。健康检查中的 `embedding_files_present` 只表示文件存在，不代表推理依赖或索引已经可用。

`rag.py` 目前只对传入的候选片段先执行对局、角色、审核状态和向量空间过滤，再校验原文哈希与计算相似度。它未连接资料导入、中文词法检索、pgvector 或实际引用页面；不能把其固定测试结果当成 RAG 效果验收。正式混合检索和引用还需资料目录、解析样例、索引发布及相关性标注。

`routes.py` 只依据固定场景的显式道路与禁行多边形检查候选路线，包含中途穿越检查。边界接触当前按禁行处理，属于保守的测试策略；D-04 未确认前不将这一政策或测试参数宣称为正式地图规则。


