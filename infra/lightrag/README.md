# LightRAG 本机双实例

完整步骤、权限、接口、证据、隐私、备份与验收状态：
`/Users/kimi/Desktop/noman/docs/LightRAG双私有知识库配置与使用说明.md`。

固定官方 v1.5.7 + OCI 摘要，9622 红、9623 蓝，仅回环开放。不同密钥和存储，禁止客户端自行选择阵营或地址。

当前本机已初始化 `.local/lightrag/releases/conquest-v1-20261110/{red,blue}`，模型配置仍是占位符，**不要重复 init，不宣称已建库或用于正式比赛**。下一步在本机分别填 service.env，运行 preflight，启动 Docker，再 ingest/status/activate/evaluate。

不要打印 env／完整 Compose 配置，不把管理密钥传给业务客户端；仅检查 Compose 结构时使用 `config --quiet`。
