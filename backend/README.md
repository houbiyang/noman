# 后端实施状态与本机运行

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


