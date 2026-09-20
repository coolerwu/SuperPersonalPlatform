# F9 WebDAV 目录免审批写入

- 状态：已确认，实现中
- 日期：2026-09-20
- 范围：Agent 对指定 WebDAV 映射目录可直接读写，不再逐次人工审批
- 前置依赖：WebDAV 映射目录配置、DeepAgent 原生 `FilesystemPermission` 与 HITL `interrupt_on`

## 背景与目标

当前 `agents.definitions[].webdav.directories[]` 的 `permission=write` 一律生成 `interrupt` 规则，Agent
每次写入都要人工确认，适合远端正式文档，但对 `/llmwiki` 这类由 Agent 自己维护的知识库目录会造成
大量无意义审批。目标是在保留“默认写入需审批”的前提下，允许用户对指定目录显式关闭审批，让该目录
的写入直接落到本地缓存并写回坚果云远端。

## 用户故事

- 作为用户，我把 `/llmwiki` 配成免审批后，Agent 读写该目录不再需要我逐次点批准。
- 作为用户，其它没有显式配置免审批的 WebDAV 目录仍然逐次审批，行为不回退。
- 作为用户，我在配置页能一眼看出哪些目录需要审批、哪些不需要。

## 功能点清单

1. 每项 WebDAV 映射目录除 `permission`（`read` / `write`）外，新增审批开关 `approval_required`，
   缺省为 `true`；`permission=read` 时归一化为 `true`，只读目录没有审批语义。
2. 配置页“访问权限”下拉提供三个选项：只读、读写 · 写入需审批、读写 · 免审批；选中免审批时在目录
   卡片内明确提示写入会直接写回坚果云远端。
3. `approval_required=false` 的目录，`write_file` / `edit_file` 不生成 `interrupt`，run 不进入
   `waiting_approval`；需要审批的目录仍走同一套审批事件、审批卡片、微信通知和“批准当前文件（10
   分钟）”租约。
4. 子目录优先规则不变：父目录免审批、子目录需审批（或反向）时，以更深路径的规则为准。
5. 平台对主 Agent 和通用子 Agent 注入的工作区说明按目录分别表述审批要求，避免 Agent 误以为需要等待
   审批而空转或自称“等待批准”。
6. 只读目录、未选择目录、挂载根、WebDAV 删除与二进制上传的限制不变。

## 验收标准

- 配置 `permission=write` + `approval_required=false` 的目录，Agent 写入直接生效并写回远端，run 不产生
  审批请求；同一 Agent 的需审批目录写入仍进入 `waiting_approval`，批准后生效、拒绝后文件不变。
- 不书写 `approval_required` 的既有配置语义等同 `true`，无需迁移即保持原行为。
- `permission=read` 的目录写入仍被拒绝，且不会产生审批请求。
- 只有免审批可写目录的 Agent run 不额外创建审批用的 run 级 checkpoint。
- 配置页三个选项可正常保存与读回，Agent 弹窗内显示与落盘语义一致。

## 边界不做的事

- 不做目录级、跨会话或永久免审批的批量授权，也不为技能写入、`system_prompt` 修改等其它 HITL 场景
  开通免审批。
- 不给免审批目录增加“只允许新建、不允许覆盖”之类的细分权限，也不放开删除与二进制上传。
- 不改变 WebDAV 全局连接、同步调度、缓存位置和写回顺序。

## 开放问题

- 是否需要在运行事件或 `/runs` 详情里对免审批写入增加更醒目的审计提示（例如标注“免审批直写远端”）？
  当前先不改事件模型。
