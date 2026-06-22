# Agent Command Center 重构设计

## 目标

将 Agent 页面重构为 Hermes / OpenClaw 风格的深色命令中心，并完整移除“自开发”和“终端”的前端页面、路由、依赖、后端接口及专属运行时。

## 视觉目标

- 采用 2026-06-22 生成的第 1 张“任务轨道”视觉稿：
  `/Users/wulang/.codex/generated_images/019eecbd-28c8-7ac1-94f8-c82e9e417c84/exec-e40699ac-2a76-4344-bf77-3918782a09d6.png`。
- 页面使用全高三栏工作区：紧凑功能轨道、主工作区、按需上下文检查器。
- 保留“对话、Agent 管理、Skill 管理、模型配置”四个完整功能，通过左侧纵向轨道切换。
- 不增加重复全局标题；通过分隔线、字重和密度表达层级，不使用渐变、发光或卡片堆砌。
- 桌面端对话态包含会话列表、消息流和 Agent 运行检查器；管理态保留资源列表、编辑区和必要的配置工具条。
- 移动端将功能轨道折叠为横向模式切换，检查器下沉，保证主操作可用。

## 行为边界

- 保留现有 Agent Chat WebSocket、会话 CRUD、图片输入、Markdown 回复、运行 checkpoint、Agent/Skill/Model 配置保存能力。
- `/models` 兼容路由仍打开 Agent 页的模型配置模式。
- 删除 `/self-dev`、`/terminal` 页面与导航；这些路径回退首页，不保留兼容页。
- 删除 `/api/self-dev/*` 与 `/api/system/terminal/connect`，同时删除自开发任务 worker、任务存储服务、终端 PTY 实现、xterm 依赖和仅供自开发使用的仓库工具。
- 删除示例配置中的 `common:self-dev`，但不主动清理用户工作区的历史运行数据。

## 验收

- 前端测试证明菜单和路由不再暴露自开发、终端，Agent 四种模式仍可切换。
- 后端应用路由表不包含自开发 HTTP 接口或终端 WebSocket。
- 前后端测试与构建通过。
- 在 1440x1024 与移动端视口完成浏览器视觉检查，并将对照结论写入根目录 `design-qa.md`。

