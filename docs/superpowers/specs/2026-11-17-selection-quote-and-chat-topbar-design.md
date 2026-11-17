# 划词引用与聊天页顶部栏移除设计

- 日期：2026-11-17
- 状态：设计已确认，待实现
- 关联模块：`apps/web/src/App.tsx`、`apps/web/src/components/MessageList.tsx`、`apps/web/src/components/ChatPanel.tsx`、`apps/web/src/components/SelectionQuote.tsx`（新增）、`apps/web/src/styles.css`
- 关联需求：`docs/intent.md` F2 网页 Chat、F9 消息操作

## 背景

- 现有引用只有「整条引用」：每条消息气泡下方的「引用」按钮把整条消息放进输入框上方的引用区，发送时逐行加 `> ` 前缀写成引用块（`MessageList.tsx`、`App.tsx` 的 `quote()` / 发送逻辑）。无法只引用回复里的一段话或一句话。
- 聊天页桌面端顶部有一条 `chat-topbar`（Agent 名 · 会话标题），pearl 主题下是一个圆角玻璃卡片；≤860px 时该条已隐藏。用户认为问答页不需要这条顶栏。
- 手机端全局 `mobile-bar` 承担菜单入口，聊天视图额外显示当前页面标题与会话标题。

## 目标

1. 支持**划词引用**：在单条消息内选中一段文字后，选区旁浮出「引用」按钮，点击后把选中片段放入现有引用区。
2. 去掉聊天页顶部栏：桌面端删除 `chat-topbar`；手机端聊天视图不再显示标题块，只保留菜单按钮。其他页面的手机标题栏不变。

## 非目标

- 不改后端、SSE、transcript 或消息格式；引用仍以 `> ` 引用块进入消息。
- 不引入第三方浮动工具栏/选区库，不新增 npm 依赖。
- 不支持跨多条消息划词；不支持多段引用叠加。
- 不改渠道（微信）侧引用找回，也不改引用区样式（仍最多显示 3 行）。
- 不为其他页面改动顶部栏或标题展示。

## 现有约束

- 消息列表使用 `@tanstack/react-virtual` 窗口化，行容器带 `transform` 定位；浮层必须用 `position: fixed`，避免被行内裁切或随虚拟滚动错位。
- 引用通路是单一入口 `onQuote(text)` → `quotesByKey[activeKey]` → 输入框上方引用区 → 发送时 `> ` 块；划词引用必须复用这条通路。
- 引用按会话隔离（key 为 `agent_id + session_id`，未建会话用 `__new__`），发送后清空；已有引用时再次引用直接替换。
- 引用区单段显示、3 行截断，发送内容不截断。
- 消息行按引用 memo，改动应保持列表渲染性能不受影响。

## 设计

### 1. 划词引用（新增 `SelectionQuote.tsx`）

组件输入：`scrollRef`（消息滚动容器）与 `onQuote(text)`。在 `ChatPanel` 中作为 `message-scroll` 的兄弟节点渲染，按钮固定定位。

**选区判定**

- 在消息滚动容器上监听 `selectionchange`（用 `requestAnimationFrame` 合并高频事件），并在 `mouseup` / `touchend` 时立即复核一次。
- 选区非空、未塌陷，且起点和终点所在的最近祖先都带 `data-quoteable` 标记并且是**同一个元素**时才算合法；跨消息、跨到输入框、按钮或引用区都不出按钮。
- 用户消息、Agent 回复、流式回复的气泡都加 `data-quoteable`；选中代码块、表格里的文字同样允许。
- 鼠标拖选过程中不显示按钮，`mouseup` 后显示；触控长按选择与键盘 Shift 选择通过 `selectionchange` 触发显示。

**浮动按钮**

- 小号胶囊按钮「引用」，`aria-label="引用选中内容"`，`position: fixed`，水平居中于选区、距选区上方 8px；上方空间不足时放到选区下方；左右做视口边界收敛。
- 层级高于消息内容、低于侧栏抽屉与弹窗。
- `mousedown` 阻止默认行为以保留选区；`click` 时读取选中文字、`trim()`，为空则不动作。

**隐藏时机**

- 选区塌陷或变得不合法（`selectionchange` 复核）、滚动消息区、窗口缩放、点击按钮之后、切换会话/路由（组件卸载）。
- 选区 API 与 `getRangeAt` 调用包 `try/catch`；流式内容更新导致选区失效时静默隐藏，不报错。

**提交与文本**

- 点击后调用现有 `onQuote(text)`；已有引用直接替换；清空浏览器选区并聚焦输入框（沿用 `quote()` 现有行为）。
- 保留选中内容中的原始换行，去首尾空白；发送时仍由现有逻辑逐行加 `> ` 前缀。
- 原整条「引用」按钮保留不变。

### 2. 聊天页顶部栏移除

**桌面端**

- 删除 `App.tsx` 聊天视图中的 `<header className="chat-topbar">` 元素。
- `styles.css` 删除 `.chat-topbar`、`.chat-topbar-main`、`.chat-topbar-title`、`.chat-topbar-sub`、`.chat-topbar-sub::before`、`.pearl .chat-topbar*`，以及 ≤860px 媒体查询中 `.chat-topbar { display: none; }`。

**手机端**

- `mobile-bar` 结构保留；仅当视图为聊天时不再渲染 `.mobile-bar-title`（当前页面标题与会话标题），只留左侧菜单按钮。
- 其余页面的手机标题栏（如配置、技能、回测）保持现有显示。
- 聊天气泡、任务区、输入区与安全区内边距不变；消息区已有 18px 顶部内边距，去栏后自然上移。

**信息去处**

- 聊天页不再展示 Agent 名与会话标题；Agent 名见侧栏 Agent 选择器，会话标题见侧栏当前选中行，不新增替代显示。

## 边界与已知取舍

- 引用流式回复中的文字时，内容继续刷新可能使选区失效，按钮会消失；已点下的引用不受影响。
- 超长选区在引用区仍按 3 行截断显示，发送内容完整。
- 划词引用只是另一种引用入口，不改变引用在 transcript 中的形态；历史消息里用户看不到引用来源（现状保持）。
- 手机端聊天视图顶部仍有一条仅含菜单按钮的窄栏（承担菜单入口），不是被移除的标题栏。

## 验证

- `cd apps/web && npm run build`（含 `tsc --noEmit`）。
- `.venv/bin/python -m pytest apps/api/app/tests`（后端无改动，确认无回归）。
- 浏览器实测：
  - 划词后浮出「引用」→ 点击进入引用区 → 发送后消息内为 `> ` 引用块；
  - 跨消息选择不出现按钮；纯空白选区不出现；
  - 已有引用时再划词为替换；
  - 滚动消息区、缩放窗口后按钮消失；
  - 桌面端聊天页无顶部栏；≤860px 聊天页只有菜单按钮、其他页面标题栏正常；
  - pearl 主题下无 topbar 残留样式与空白占位。

## 文档同步

- `docs/intent.md`：F2/F9 消息操作处补充「可划词引用单条消息内的片段」；F2 说明聊天页不再有顶部标题栏。
- `README.md`：消息操作一节补充划词引用；界面说明中去掉聊天页顶部栏描述（如有）。
- `CLAUDE.md`：网页聊天条目补充划词引用与顶部栏移除。
