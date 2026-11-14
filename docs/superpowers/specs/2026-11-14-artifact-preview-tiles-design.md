# 聊天产物文件方块预览设计

- 日期：2026-11-14
- 状态：待评审
- 范围：`apps/api` 一个只读预览接口 + `apps/web` 产物区组件。无新依赖、无数据库变更。

## 背景

聊天回复末尾的「产物」目前是行内小链接：图片是缩略图，文件是「图标 + 文件名 + 大小」的 chip。用户希望像 ChatGPT 生成文件后的展示一样，用方块卡片直接看到文件内容：

- 文本文件在方块里直接展示全部内容，内容区可滚动；
- `.md` 用 Markdown 渲染，其他文本原样显示；
- 图片继续显示缩略图；
- PDF、Office、压缩包等其余文件显示类型图标；
- 文本超过 512 KB 时只加载前 512 KB，并在方块底部标注截断。

产物来源与现有逻辑一致：assistant 的 `Write`/`Edit` 等工具产生的 `file_path`、截图类工具的 `filename`，汇总成每条消息的「产物」列表（F2）。本设计只改变展示方式，不改变产物的产生与去重规则。

## 目标与非目标

### 目标

1. 产物区变成响应式方块网格，每个方块直接展示内容或类型。
2. 文本内容在方块内可滚动查看；Markdown 渲染，代码/日志等原样显示。
3. 图片显示缩略图，PDF/Office/压缩包显示类型图标。
4. 文本预览由服务端做类型白名单与 512 KB 截断，浏览器不加载二进制或超大内容。
5. 保持珍珠冷光 + 玻璃风格与现有移动端布局。

### 非目标

- 不改变产物的生成、提取、去重与工作区外过滤规则。
- 不把正文 Markdown 里的文件链接改成方块；只改回复末尾的「产物」区。
- 不做 PDF 首页渲染、Office 内容解析或服务端缩略图。
- 不做语法高亮、行号、文件编辑或在线预览弹窗。
- 不新增前端测试框架。

## 后端设计

### 接口

```
GET /api/agents/{agent_id}/file-preview/{path:path}
```

- 鉴权与现有 `GET /api/agents/{agent_id}/files/{path:path}` 一致（Agent 路由级 `require_auth`）。
- 复用 `resolve_workspace_file`：拒绝 `..`、符号链接逃逸、工作区外路径、目录与不存在的文件。
- paused/archived Agent 的旧产物仍可预览，与文件下载接口语义一致。
- 成功返回：

```json
{
  "content": "# 标题\n...",
  "format": "markdown",
  "truncated": false,
  "size": 12345
}
```

- `format` 只有 `markdown`（`.md`、`.markdown`）与 `text`（其余白名单扩展名）。
- 非白名单扩展名返回 `415 {"detail": "file type cannot be previewed"}`；Agent 或文件不存在、读取时文件被删除（`OSError`/`FileNotFoundError`）返回 404。
- `Cache-Control: private, no-cache`。

### 文本白名单

在 `apps/api/app/services/workspace_files.py` 中定义：

```python
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}
_TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".svg", ".html", ".htm", ".css", ".scss",
    ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".bash", ".zsh",
    ".sql", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp",
    ".rb", ".php", ".lua", ".swift", ".kt", ".r", ".m", ".tex",
}
_PREVIEW_LIMIT = 512 * 1024
```

提供 `preview_format(path) -> "markdown" | "text" | None` 与同步的 `read_text_preview(path) -> (content, truncated, size)`：

- 最多读取 `_PREVIEW_LIMIT + 1` 字节，按 UTF-8 解码，错误字节替换为 `�`。
- `truncated = len(raw) > _PREVIEW_LIMIT`，内容截到限制以内；去掉 UTF-8 BOM。截断可能落在多字节字符中间，末尾出现替换字符 `�`，属可接受边界。
- 读取与 `stat` 放在路由器里的 `asyncio.to_thread`，不阻塞事件循环。

### 安全

- 只返回文本，不返回文件流；前端不会把 HTML/SVG/JS 交给浏览器渲染。
- 路径校验与下载接口完全一致；预览接口不扩大可读范围。
- 响应最多约 512 KB，避免一个消息里多个产物拖垮前端。

### 测试

在 `apps/api/app/tests/test_workspace_files.py` 增加：

- `.md` → `format: "markdown"`，`.py`/`.json` → `text`。
- 超过 512 KB 的文件 `truncated: true` 且 `len(content.encode()) <= 512 * 1024`。
- 非法 UTF-8 字节返回替换字符而不是 500。
- `.pdf`/`.zip` → 415；不存在、目录、`..`、工作区外路径 → 404。
- paused/archived Agent 的文件可读。
- `size` 与磁盘文件一致。

## 前端设计

### 组件结构

新增 `apps/web/src/components/ArtifactGrid.tsx`，替换 `MessageList.tsx` 里的 `ArtifactStrip`：

- `ArtifactGrid({ artifacts, agentId })`：渲染「产物」标签 + 网格。
- `ArtifactTile`：按类型分派：
  - 文本扩展名 → `TextArtifactTile`（Markdown 或原样文本）。
  - `kind === "image"` → `ImageArtifactTile`。
  - 其他 → `IconArtifactTile`。
- 文本扩展名判断放在前端的一个小工具里（与后端白名单保持一致）；如果判断不准，预览接口返回 415 时回落到图标。

`api.ts` 新增（路径按段编码，与 `workspaceFileUrl` 的规则一致）：

```ts
previewWorkspaceFile: (agentId: string, path: string) =>
  request<ArtifactPreview>(
    `/api/agents/${encodeURIComponent(agentId)}/file-preview/${path
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`,
  ),
```

`types.ts` 增加：

```ts
export type ArtifactPreview = {
  content: string;
  format: "markdown" | "text";
  truncated: boolean;
  size: number;
};
```

### 方块布局

- 网格：`display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px;`
- 方块：`aspect-ratio: 1 / 1; display: flex; flex-direction: column; border: 1px solid var(--border); border-radius: 12px; background: rgba(255,255,255,.72); overflow: hidden;`
- 头部：类型徽标（`MD`/`PY`/`JSON`/`PDF`/`IMG` 等，accent-soft 背景）+ 文件名（单行省略，`title` 为完整路径）。
- 主体：`flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain;`
- 底部：大小 + 「打开」链接；截断时追加「已截断前 512 KB」。
- ≤860px：`minmax(130px, 1fr)`，大约两列（视宽度可能更多）；方块高度仍为正方形。

### 三种内容

- **Markdown**：在滚动主体内用现有 `Markdown` 组件渲染（关闭语法高亮，避免小块内代码高亮开销与拥挤），外层加 `.artifact-tile-markdown` 紧凑样式：字号 12px、标题缩小、段距收紧、代码块横向滚动、图片 `max-width: 100%`。不启用 `rehype-raw`，原始 HTML 不会执行。Markdown 内的相对图片与链接按该 Markdown 文件所在目录解析，而不是一律按工作区根目录。
- **其他文本**：`<pre class="artifact-tile-text">`，等宽字体、`white-space: pre-wrap`、`word-break: break-word`。
- **图片**：`<img>` + `object-fit: contain` + `--panel-2` 背景，不裁切；点图片或「打开」查看原图。
- **图标**：居中的文件图标 + 扩展名，文件名和大小在底部；「打开」下载或新标签打开。

### 数据流

- 文本方块挂载时请求预览；模块级缓存 `Map<string, ArtifactPreview>`，键为 `${agentId}:${path}:${size}`。
- 流式写入期间文件可能暂时 404：失败后延迟 1 秒重试一次；`artifact.size` 变化会产生新键并重新拉取。
- 组件卸载时用 `AbortController` 取消请求；缓存保留，滚动回来不重复加载。
- 只有可见消息会挂载方块（消息列表已虚拟化），不会一次性请求整段历史的所有产物。

### 加载与失败

- 加载中：3 行骨架条。
- 失败/415/404：图标方块 + 「无法预览」，仍可「打开」。
- 空文件：主体显示「空文件」。
- 截断：底部标注，不额外弹窗。

### 交互与可访问性

- 文本主体只滚动，不是链接；「打开」是独立链接，`target="_blank"`、`rel="noreferrer"`。
- 方块悬停边框/阴影反馈；`:focus-visible` 轮廓；文本主体可键盘聚焦（`tabIndex={0}`）。
- 网格 `aria-label="产物文件"`；「打开」的 `aria-label` 包含文件名。
- 保持现有视觉语言，不引入新颜色变量。

### 测试

- 构建通过（`npm run build`）。
- 手工：一条回复产出 `.md`、`.py`、`.json`、`.png`、`.pdf`，检查四种方块、滚动、截断、打开链接、移动端两列。
- 不为 PDF/zip 发起预览请求（可在 Network 面板确认）。

## 边界与安全

- **HTML/SVG/JS**：按纯文本显示；预览接口返回 JSON 文本，前端用 `<pre>` 或 Markdown 组件渲染，不存在同源脚本执行路径。
- **Markdown 原始 HTML**：现有 `Markdown` 组件没有 `rehype-raw`，不会渲染内联 HTML。
- **大文件**：只读前 512 KB；不把整文件读进内存。
- **大量产物**：每块独立懒加载 + 缓存；虚拟列表只挂载可视区。
- **删除/移动文件**：预览 404 时回落到图标，聊天不报错。
- **非 UTF-8**：替换字符显示，不中断。
- **重复路径**：产物列表已按路径去重，方块用 `path` 作为 key。
- **归档 Agent**：只读预览仍放行，与现有文件下载一致。

## 涉及文件

### 后端

- `apps/api/app/services/workspace_files.py`：白名单、`preview_format`、`read_text_preview`。
- `apps/api/app/routers/agents.py`：新增预览路由（需要 `import asyncio`，用 `asyncio.to_thread` 读取）。
- `apps/api/app/tests/test_workspace_files.py`：预览接口测试。

### 前端

- `apps/web/src/api.ts`：`previewWorkspaceFile`。
- `apps/web/src/types.ts`：`ArtifactPreview`。
- `apps/web/src/components/ArtifactGrid.tsx`：新建网格与三种方块。
- `apps/web/src/components/MessageList.tsx`：用 `ArtifactGrid` 替换 `ArtifactStrip`。
- `apps/web/src/styles.css`：网格、方块、Markdown 紧凑样式、移动端。
- 可能新增 `apps/web/src/artifactTypes.ts`（文本扩展名判断与徽标）。

## 兼容与迁移

- 新接口是加法式改动；旧的 `GET /files/{path}` 不变，图片与「打开」仍使用它。
- 无数据库 schema 变更、无数据迁移、无新依赖。
- 实现时在 `docs/intent.md` 追加 F23，并同步 `CLAUDE.md` / `README.md` / 前端构建产物。

## 风险

- **小方块里的 Markdown**：渲染后的标题/表格可能拥挤；通过紧凑样式限制字号与间距，必要时在实现时用真实长文档调整。
- **512 KB 文本**：单个方块读取上限 512 KB；二进制文件伪装成文本时，解码后的替换字符与控制字符转义可能让 JSON 最坏约 3 MB，仍可接受。多个大文件同时挂载会占用内存；懒加载 + 缓存键控制重复请求，必要时后续加二进制嗅探或并发上限。
- **滚动嵌套**：方块内滚动可能影响聊天列表滚动；用 `overscroll-behavior: contain` 隔离，移动端需实测。
- **流式文件**：写入中的文件预览可能不完整；靠 1 秒重试与 size 变化刷新，最终以完成后内容为准。
- **缓存时效**：同一路径同大小的文件被重写时缓存不会更新；聊天刷新后缓存清空，属可接受边界。

## 实施顺序

1. `docs/intent.md` 追加 F23。
2. 后端白名单/读取函数 + 预览路由 + 测试。
3. 前端 `api.ts`/`types.ts`。
4. `ArtifactGrid` 与三种方块 + 样式。
5. `MessageList` 接入，替换旧产物渲染。
6. 构建、全量测试、手工验收；同步 `CLAUDE.md` / `README.md` 与 `dist`。
