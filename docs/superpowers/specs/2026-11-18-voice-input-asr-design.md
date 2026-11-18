# 网页语音输入（DeepSeek ASR）设计

- 日期：2026-11-18
- 状态：设计已确认，待实现
- 关联模块：`apps/api/app/services/settings_store.py`、`apps/api/app/routers/asr.py`（新增）、`apps/api/app/services/asr.py`（新增）、`apps/api/app/schemas.py`、`apps/api/app/main.py`、`apps/web/src/components/ChatPanel.tsx`、`apps/web/src/components/AsrSettings.tsx`（新增）、`apps/web/src/components/SettingsPage.tsx`、`apps/web/src/components/Sidebar.tsx`、`apps/web/src/api.ts`、`apps/web/src/types.ts`、`apps/web/src/styles.css`
- 关联需求：`docs/intent.md` F27 网页语音输入（新增）、F2 网页 Chat

## 背景

- DeepSeek 提供语音识别工具接口 `POST https://api.deepseek.com/v1/tools/asr`：`multipart/form-data` 的 `file` 字段发送音频，一次请求返回完整转写文本，不支持流式。
  - 支持 16 位 PCM WAV 或 Ogg Opus，WAV 采样率 8000–48000 Hz。
  - 单声道或双声道（双声道只识别左声道），最大 8 MiB，上传需在 10 分钟内完成，最长 120 秒。
  - 语种自动识别（中、粤、英、日、韩等 21 种），无语言参数。
  - 响应字段：`request_id`、`text`、`duration_ms`。
  - 错误码：400 格式不支持或缺 `file`、408 上传超时、413 文件过大、429 并发超限、503 服务繁忙、504 识别超时；每账号最多 50 个并发请求。
  - 计费：每小时音频刊例价 0.78 元，限时五折 0.39 元，按音频时长精确到秒、不足 1 秒按 1 秒计。
- 平台现状：网页聊天有附件上传（图片/文件），没有任何音频处理；微信语音按 F6 约定不下载、不处理；没有 ASR 配置或接口。
- 用户实际通过 `https://ai.wulang.vip` 的 HTTPS 反向代理访问页面，满足麦克风要求的浏览器安全上下文。
- 生产机 `192.168.1.3`（Ubuntu 24.04）已有 `/usr/bin/ffmpeg` 与 `/usr/bin/ffprobe`；本地 macOS 开发机没有 ffmpeg。

## 目标

1. 在网页聊天输入框旁提供语音输入：点麦克风录音，停止后转写成文字并自动发送；发送失败时文字留在草稿。
2. 音频只在内存中流转，平台不落盘、不写会话记录；DeepSeek API Key 只存在服务端配置。
3. 单次录音最长 120 秒，到点自动停止并转写。

## 非目标

- 不做微信语音下载/转写，不改变 F6「语音不下载不处理」的约定。
- 不做独立转写页、音频文件批量转写、录音上传为附件。
- 不做流式识别、超 120 秒的分段拼接、语种选择、TTS。
- 不改消息格式、执行核、渠道投递、会话 transcript。
- 不新增 Python/npm 依赖、不新增环境变量、不改部署架构。

## 现有约束

- 平台配置集中在 `data/settings.json`，单文件、单锁、临时文件 + fsync + `os.replace`，写前留 `.bak`；密钥字段只返回 `has_api_key`（参考地图 `maps` 节）。
- 麦克风采集只在安全上下文（HTTPS 或 `localhost`）可用；生产经 HTTPS 反向代理访问。
- DeepSeek ASR 只接受 WAV/OGG，最长 120 秒、8 MiB；浏览器 `MediaRecorder` 常见输出是 WebM/Opus 或 MP4/AAC，需要服务端转码。
- 前端草稿、附件、引用按会话隔离；输入区在 Agent 非 active 或会话未加载时禁用。
- 前端没有单元测试运行器，验证以 `tsc --noEmit`、vite 构建和浏览器实测为准。

## 设计

### 1. 配置（`asr` 节）

- `PlatformSettings` 新增 `AsrSection`：

```python
class AsrSection(BaseModel):
    enabled: bool = False
    api_key: str = ""
```

- `SettingsStore` 增加 `update_asr(enabled, api_key, clear_api_key)`，行为与 `update_maps` 一致：只更新传入字段；启用但没有任何 Key 时返回 400「请先填写 DeepSeek API Key」；`clear_api_key` 清空 Key 并同时停用；保存后写盘并返回新节。
- `schemas.py` 增加 `AsrConfigUpdate`：`enabled`、`api_key`（可选）、`clear_api_key`（默认 `False`）。
- `GET /api/asr` 返回 `{enabled, has_api_key}`；`PUT /api/asr` 接受更新并返回同一结构。不返回 Key 明文。
- 首次启动不建种子、不读环境变量；Key 由用户在配置页填写。

### 2. 后端转写（`services/asr.py` + `routers/asr.py`）

**路由**

- `router = APIRouter(prefix="/api/asr", dependencies=[Depends(require_auth)])`，在 `main.py` 注册。
- `POST /api/asr/transcribe`：接收 `UploadFile`，字段名 `file`。
  1. 读取 `settings.asr`：未启用或无 Key → 409「语音识别未启用，请先在配置页填写 DeepSeek API Key」。
  2. 读取上传内容，超过 15 MiB → 413「录音文件过大」。
  3. 调转码函数得到 WAV；`FileNotFoundError`（ffmpeg 不存在）→ 503「服务器未安装 ffmpeg，无法处理语音」；转码非零退出 → 400「无法解析这段录音」；转码超时 → 504「音频转码超时」。
  4. 转码后按 WAV 数据长度算时长（16 kHz × 2 字节/样本）：超过 120.5 秒 → 400「录音超过 2 分钟」；超过 8 MiB → 413「录音文件过大」。
  5. 调 DeepSeek ASR；返回 `{text, duration_ms}`（`request_id` 不向上暴露）。
- 错误统一为 `HTTPException(status_code, detail=中文说明)`；服务端日志只记状态码与请求 id，不记音频内容与 Key。

**服务**

- `transcode_to_wav(source: bytes) -> bytes`：`asyncio.create_subprocess_exec` 调 ffmpeg，固定参数 `-hide_banner -loglevel error -i pipe:0 -ac 1 -ar 16000 -sample_fmt s16 -f wav pipe:1`，stdin 写入原始字节，stdout 读取 WAV；30 秒超时后 kill 子进程；不经过 shell。
- `wav_duration_seconds(wav: bytes) -> float`：`max(0, (len(wav) - 44) / 32000)`，只用于校验上传时长，不依赖容器头。
- `transcribe(wav: bytes, api_key: str, *, client: httpx.AsyncClient | None = None) -> dict`：`multipart/form-data` POST 到 `https://api.deepseek.com/v1/tools/asr`，`Authorization: Bearer <api_key>`；连接超时 10 秒、读超时 60 秒；返回 `{"text": str, "duration_ms": int}`。
- 状态码映射：

| DeepSeek 状态 | 平台返回 | 文案 |
| --- | --- | --- |
| 200 | 200 | 透传 `text` / `duration_ms` |
| 400 | 400 | 语音识别无法处理这段音频 |
| 401 | 400 | DeepSeek API Key 无效或已失效，请在配置页重新填写 |
| 403 | 403 | DeepSeek 账号无权使用语音识别，请检查账号权限 |
| 408 | 408 | 音频上传超时，请重试 |
| 413 | 413 | 录音文件过大 |
| 429 | 429 | 语音识别服务繁忙，请稍后再试 |
| 503 | 503 | 语音识别服务暂不可用 |
| 504 | 504 | 语音识别超时，请重试 |
| 其他/网络错误 | 502 | 语音识别服务返回异常 |

上游 401 不直接透传，避免前端或反向代理把它误判为平台登录失效。

- 转码函数与 HTTP 客户端都支持注入，便于测试；生产默认使用真实 ffmpeg 与 httpx（httpx 默认信任 `HTTP_PROXY` / `HTTPS_PROXY`，沿用现有出站代理约定）。

### 3. 前端（`ChatPanel` + 配置页）

**可见性**

- `ChatPanel` 挂载时请求 `GET /api/asr`（失败按未启用处理）；`enabled` 为真才在 `.composer-tools` 的「+」之后显示麦克风按钮。输入区不可用时（Agent 非 active 或会话未加载）麦克风按钮同样禁用。
- 配置页保存后回到聊天页重新挂载，会重新读取配置。

**录音与转写**

- 点击麦克风：`navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}})`；不可用或拒绝 → 提示「无法访问麦克风，请检查浏览器权限」。
- `MediaRecorder` 按优先级选择 `audio/webm;codecs=opus` → `audio/ogg;codecs=opus` → `audio/mp4` → 浏览器默认；`start(1000)` 每秒收块。
- 状态机 `idle → starting → recording → transcribing → idle`：
  - recording：按钮变红、显示 `0:12 / 2:00` 计时，输入框位置替换为随声音起伏的实时波形线；再点停止；到 120 秒自动停止并提示「已到 2 分钟上限，正在转写…」。
  - 停止：释放麦克风轨道；录音短于 0.5 秒 → 提示「录音太短，请重试」，不上传。
  - 上传：`POST /api/asr/transcribe`，`multipart/form-data`，字段名 `file`，文件名按容器类型取 `recording.webm` / `.ogg` / `.mp4`。
  - 成功：`text.trim()` 为空 → 提示「没有识别到语音内容」；否则追加到当前草稿（草稿为空直接写入，非空且末尾无空白时补一个空格）并立即发送；发送失败时文字保留在草稿并提示。
  - 失败：展示后端中文错误；不保留音频，不写草稿。
- 录音或转写期间发送按钮禁用；麦克风按钮在 transcribing 时禁用并显示「正在转写…」。
- 切换会话/Agent、离开聊天页、组件卸载：停止录音、释放轨道、丢弃已录数据，不上传。
- 音频 Blob 只存在内存，转写结束即释放。

**配置页**

- 新增 `AsrSettings.tsx`：照 `MapsSettings` 的开关 + Key + 清除确认（`ConfirmDialog`）模式；Key 输入框不显示已有值，只显示「已配置」；保存后提示「已保存」。
- `SettingsPage` 新增 `tab === "asr"` 卡片「语音识别」，说明「DeepSeek 语音识别；音频仅用于转写，不保存；按音频时长计费」。
- `SettingsTab` 类型新增 `asr`；侧栏「Agent全局配置」在「地图」与「运行环境」之间新增「语音识别」入口。
- `api.ts` 增加 `getAsrConfig` / `updateAsrConfig` / `transcribeAudio(file)`；`types.ts` 增加 `AsrConfig {enabled, has_api_key}` 与 `TranscribeResult {text, duration_ms}`。

### 4. 隐私与日志

- 音频路径：浏览器内存 → 平台请求内存 → DeepSeek；平台不写工作区、不写 transcript、不落临时文件。
- `api_key` 不进入前端响应、不写日志；ffmpeg 与 httpx 的错误日志不记录音频字节。
- 转写结果只进入输入框草稿，用户发送后才成为会话内容；平台不保存独立转写记录。

## 边界与已知取舍

- 单次最长 120 秒，长语音需要分段录；不做拼接。
- 语种自动识别，识别效果取决于 DeepSeek；不提供语言选择。
- 本地 macOS 没有 ffmpeg，真实转码联调需 `brew install ffmpeg`；生产已预装。
- 页面必须在 HTTPS 或 localhost 下访问才能录音；当前生产通过 `https://ai.wulang.vip` 访问，满足条件。
- 录音失败或识别失败不保留音频，不能重试同一段录音，只能重录。
- 429 并发上限为 DeepSeek 账号级（50），单用户场景基本不会触发。
- 平台不提供语音消息、TTS 或微信语音转写。

## 测试

- `apps/api/app/tests/test_asr_settings.py`（或现有 settings 测试文件内）：GET/PUT 只回 `has_api_key`；设置、更新、清除 Key；未启用开关保留 Key。
- `apps/api/app/tests/test_asr.py`：
  - 转写成功透传 `text` / `duration_ms`；
  - 空文本正常返回；
  - 未启用、无 Key 返回 409；
  - 超过 15 MiB 返回 413；
  - 转码器抛 `FileNotFoundError` → 503；非零退出 → 400；超时 → 504；
  - 转码后时长超过 120.5 秒 → 400；
  - DeepSeek 400/408/413/429/503/504 → 对应平台状态与中文文案；
  - `httpx.MockTransport` 注入客户端，转码函数注入假实现，不依赖本机 ffmpeg。
- 前端：`cd apps/web && npm run build`（`tsc --noEmit` + vite）通过。
- 浏览器实测（生产或本地装好 ffmpeg + 有效 Key）：
  1. 未启用时聊天页无麦克风按钮；
  2. 启用后录音、停止、转写，文字自动发送；发送失败时留在草稿；
  3. 录音中再点停止、120 秒自动停止；
  4. 权限拒绝提示；
  5. 转写后切会话不残留音频/状态；
  6. 手机浏览器点击录音与停止正常；
  7. 未配置 Key 时接口返回 409，不产生网络请求到 DeepSeek。
- 后端回归：`.venv/bin/python -m pytest apps/api/app/tests`。

## 文档同步

- `docs/intent.md`：新增 **F27 网页语音输入**（功能点、边界、验收），编号水位改为 F27；F2 功能点补一句「输入框支持语音转文字」。
- `CLAUDE.md`：配置分区从五个改为六个（Provider、Agent、知识库、地图、语音识别、运行环境）；补充 `asr` 配置节、`/api/asr` 接口、ffmpeg 依赖、音频不落盘与 120 秒限制。
- `README.md`：聊天功能增加语音输入；配置页分区列表增加「语音识别」；本地开发依赖说明 `brew install ffmpeg`；生产说明 ffmpeg 已预装、麦克风需要 HTTPS/localhost。
- 不新增环境变量，`deploy/.env.example` 不变。
