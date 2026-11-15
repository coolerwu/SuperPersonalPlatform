# Agent 地图工具（高德 MCP）设计

- 日期：2026-11-15
- 状态：待评审
- 范围：`apps/api` 设置模型/API/执行核 + `apps/web` 配置页。无新依赖、无数据库变更、不做地图 UI。

## 背景

用户希望 Agent 能回答真实的位置相关问题：附近 POI、地址解析、路线规划、距离和天气。当前平台没有地图工具；插件集市只接入 `anthropics/claude-plugins-official`，其中没有地图插件。好消息是地图厂商已经提供官方 MCP：

- 高德地图官方 MCP：Streamable HTTP，端点 `https://mcp.amap.com/mcp?key=...`，需要高德开放平台的 **Web 服务** Key；提供地理/逆地理编码、POI 搜索、周边搜索、驾车/公交/步行/骑行路线、距离、天气、导航/打车链接等约 15 个工具。
- 腾讯位置服务 MCP：SSE 云端接入，约 15 个接口，语义化返回。
- 百度地图 MCP：约 10 个标准接口。

本设计选择高德：国内覆盖好、官方 HTTP MCP 与 SDK 原生兼容、工具面足够。用户已确认只要 Agent 地图工具，不要求网页地图渲染。

## 目标与非目标

### 目标

1. 在设置页全局配置高德 Web 服务 Key，并启用/停用地图工具。
2. Agent 能调用高德 MCP 的 POI、地理编码、路线、距离、天气等工具。
3. Key 只保存在后端，接口只返回 `has_api_key`；日志中的 Key 脱敏。
4. 改 Key 或开关后，下一轮对话使用新配置；停用后不再建立远程 MCP 连接。
5. 高德 MCP 不可用时普通聊天不受影响。

### 非目标

- 不做聊天内地图卡片、路线可视化或独立地图页面。
- 不做 per-Agent 开关或多提供商切换；本期只接高德。
- 不做联网 Key 校验或「测试连接」按钮。
- 不改权限模型；依赖现有 `bypassPermissions` 放行 MCP 工具。
- 不通过插件集市分发；直接使用 SDK 的 `mcp_servers` HTTP 配置。

## 架构与数据流

```text
配置页「地图」卡片
  → PUT /api/maps（enabled, api_key）
  → settings.json 的 maps 节（api_key 只写不读）
  → ClaudeTurnRunner 构造 SDK client
      → mcp_servers["amap"] = {type: "http", url: "https://mcp.amap.com/mcp?key=..."}
  → 模型在对话中调用 mcp__amap__maps_* 工具
  → 工具结果进入对话，执行过程显示工具行
```

平台不维护地图工具的参数与返回结构，工具清单由高德 MCP 提供；`/tools` 页从 init frame 读取 `mcp_servers` 和 `tools`，启用后自动显示 `amap` 及其工具。

## 后端设计

### 设置模型

在 `apps/api/app/services/settings_store.py` 增加：

```python
class MapsSection(BaseModel):
    enabled: bool = False
    provider: Literal["amap"] = "amap"
    api_key: str = ""
```

- `PlatformSettings` 增加 `maps: MapsSection = Field(default_factory=MapsSection)`。
- 解析 settings 时用 `_section(MapsSection, raw.get("maps"))`，缺省为空配置。
- `build_default_settings()` 不写 Key，只带默认 `maps`。
- `SettingsStore` 增加：

```python
async def update_maps(self, **values: Any) -> None:
    validated = _validated_update(MapsSection, values)

    def mutate(data: dict[str, Any]) -> None:
        _dict_section(data, "maps").update(validated)

    await self._json.update(mutate)
```

### API

新增 `apps/api/app/routers/maps.py`，挂载到 `/api/maps`，与其他路由一样要求登录。

`GET /api/maps` 返回：

```json
{ "enabled": false, "provider": "amap", "has_api_key": false }
```

`PUT /api/maps` 请求体：

```json
{ "enabled": true, "api_key": "your-amap-key", "clear_api_key": false }
```

规则：

- `api_key` 为空白字符串表示保持已有 Key；`clear_api_key: true` 清空 Key 并同时把 `enabled` 置为 false。
- 同时提交 `clear_api_key: true` 和 `api_key` 时以清除为准。
- `enabled: true` 但既没有已保存 Key、也没有提交新 Key 时返回 400：「请先填写高德 Web 服务 Key」。
- 只做去空格和 provider 校验，不联网校验 Key。
- 返回最新配置（同样只含 `has_api_key`）。
- 配置变更不需要立即关闭 client；Runner 的配置指纹会在下一轮对话检测到变化并重建。

### 执行核

在 `apps/api/app/services/claude_runner.py`：

- `run()` 已经通过 `settings = await self._store.get()` 拿到完整配置；把 `settings.maps` 传给 `_client(turn, agent, maps)`。
- 构造 options 时：

```python
mcp_servers = {
    platform_mcp.SERVER_NAME: platform_mcp.build_server(tool_context),
}
if maps.enabled and maps.api_key:
    mcp_servers["amap"] = {
        "type": "http",
        "url": (
            "https://mcp.amap.com/mcp?key="
            + quote(maps.api_key, safe="")
        ),
    }
kwargs["mcp_servers"] = mcp_servers
```

- 未启用或没有 Key 时不加 `amap`，不建立远程连接。
- 远程连接沿用 `build_claude_env()` 写出的 HTTP/HTTPS 代理环境变量，与现有模型调用一致。
- `allowed_tools` 保持平台工具列表；当前 `permission_mode` 是 `bypassPermissions`，MCP 工具会被自动放行。若未来改成需要审批的模式，地图工具也会进入审批范围，本期不处理。
- 配置指纹 `_config_fingerprint()` 增加 `maps_stamp`：

```python
maps_stamp = json.dumps(
    {
        "enabled": maps.enabled,
        "provider": maps.provider,
        "api_key": maps.api_key,
    },
    sort_keys=True,
)
```

缓存 client 的指纹比较和新建 client 的指纹都带上它；改 Key/开关后下一轮重建 client。

### 密钥安全

- `settings.json` 权限 0600；`GET /api/maps` 只返回 `has_api_key`，不返回 Key。
- Key 会进入 SDK 的 MCP URL。Runner 对 CLI stderr 的 debug 日志做脱敏：

```python
_AMAP_KEY_RE = re.compile(r"(mcp\.amap\.com/mcp\?key=)[^&\s]+")

def _redact_cli_line(line: str) -> str:
    return _AMAP_KEY_RE.sub(r"\1***", line)
```

- `stderr` 回调改为 `logger.debug("cli[%s] %s", turn.session_id, _redact_cli_line(line))`。
- 平台不把 Key 写入 `.env`、前端响应、仓库或普通日志。

### 错误处理

- 高德 MCP 连接失败或 Key 无效：SDK 仍能启动，普通聊天继续；init frame 的 `mcp_servers` 会显示 `amap` 的失败状态，工具页可查看。
- 远程 MCP 首次连接会增加一点 client 启动时间；现有 `claude_connect_timeout_seconds`（默认 60 秒）覆盖。
- 配置页保存只做格式校验，不因网络问题阻塞保存。

### 测试

- Settings：默认 `maps` 为空；保存、留空保持 Key、清除 Key 并停用；启用无 Key 返回 400；GET 不含 Key。
- Runner：启用且有 Key 时 options 的 `mcp_servers` 含 `amap` 且 URL 正确编码；停用或无 Key 时不含；指纹随 enabled/provider/api_key 变化。
- 日志脱敏：包含 `mcp.amap.com/mcp?key=...` 的行被替换为 `***`，其他内容不变。
- 全量后端测试。

## 前端设计

### API 与类型

`types.ts`：

```ts
export type MapsConfig = {
  enabled: boolean;
  provider: "amap";
  has_api_key: boolean;
};
```

`api.ts`：

```ts
getMapsConfig: () => request<MapsConfig>("/api/maps"),
updateMapsConfig: (payload: {
  enabled?: boolean;
  api_key?: string;
  clear_api_key?: boolean;
}) => request<MapsConfig>("/api/maps", {
  method: "PUT",
  body: JSON.stringify(payload),
}),
```

### 配置页

- `SettingsPage.tsx` 的标签增加「地图」，放在「知识库」和「运行环境」之间。
- 新增 `MapsSettings.tsx`，挂在该标签下：
  - 标题「地图」+ 说明：启用后所有 Agent 都能调用高德地图工具；Key 只保存在后端。
  - 开关「启用高德地图工具」。
  - Key 输入框（`type="password"`）：已配置时占位「已配置，留空保持不变，输入新 Key 可替换」；未配置时占位「输入高德 Web 服务 Key（不是 JS API Key）」。
  - 链接「获取高德 Web 服务 Key」，指向 `https://lbs.amap.com/api/mcp-server/create-project-and-key`。
  - 已配置时显示「密钥已配置」徽标和「清除密钥」；清除复用现有 `ConfirmDialog` 二次确认，并同时停用。
  - 「保存」按钮；保存中禁用；成功提示「已保存，下一轮对话生效」；失败显示错误。
  - 启用但没有 Key 时前端直接提示并禁用保存，后端仍然兜底 400。
- 不做「测试连接」；提示用户到「工具」页查看 `amap` MCP 状态。

### 工具页与聊天

- 工具页不改：启用后 init frame 带 `amap` server 和 `mcp__amap__*` 工具，页面自动展示。
- 聊天里地图工具显示在「执行过程」；Agent 可把路线/POI 结果整理成文字，附带高德导航/打车链接。本期不做地图渲染。

## 文档同步

- `docs/intent.md` 追加 F24「Agent 地图工具」。
- `CLAUDE.md` 的 MCP/平台工具段落补充 `amap` HTTP MCP、`/api/maps`、全局 Key 与「下一轮生效」。
- `README.md` 增加地图配置步骤与工具说明。

## 验收标准

- 配置有效 Key 并启用后，新会话问「重庆九龙坡区彩玉湖附近 15 万左右的 4S 店」或「从彩玉湖到解放碑怎么开车」，Agent 调用 `mcp__amap__*` 并给出真实 POI/路线/天气结果。
- 工具页能看到 `amap` MCP 服务及其工具；停用后新会话不再出现。
- 无效 Key 时普通聊天不受影响，工具页显示 amap 连接/工具错误。
- `GET /api/maps` 只返回 `has_api_key`；`settings.json` 保存 Key；CLI 日志中的 Key 已脱敏。
- 修改 Key 或开关后，下一轮对话生效；正在执行的轮次不受影响。
- 后端全量测试与前端构建通过。

## 涉及文件

### 后端

- `apps/api/app/services/settings_store.py`：`MapsSection`、`PlatformSettings.maps`、解析、`update_maps()`、默认配置。
- `apps/api/app/schemas.py`：`MapsConfigUpdate`。
- `apps/api/app/routers/maps.py`：新建 GET/PUT。
- `apps/api/app/main.py`：挂载 maps 路由。
- `apps/api/app/services/claude_runner.py`：MCP server 构造、指纹、stderr 脱敏。
- 测试：`test_app_settings.py` 或新建 `test_maps_api.py`、`test_claude_runner.py`。

### 前端

- `apps/web/src/types.ts`：`MapsConfig`。
- `apps/web/src/api.ts`：`getMapsConfig` / `updateMapsConfig`。
- `apps/web/src/components/MapsSettings.tsx`：新建配置卡片。
- `apps/web/src/components/SettingsPage.tsx`：增加「地图」标签并渲染卡片。
- `apps/web/src/styles.css`：仅在需要时复用现有 settings 样式，不新增大块样式。

## 兼容与迁移

- `settings.json` 新增 `maps` 节，旧文件缺省时按空配置处理，无迁移脚本。
- API 是加法式新增；无数据库变更、无新依赖。
- 不修改现有 Provider/Agent/知识库行为。

## 风险

- **网络与额度**：高德 MCP 不可达或 Key 超出配额时，地图工具失败但普通聊天继续；工具页提供状态。
- **MCP 工具变化**：工具清单由高德维护，模型可用的工具名可能随服务更新；平台不写死工具名单。
- **启动开销**：远程 MCP 首次连接会增加新会话的 client 启动时间。
- **Key 暴露面**：Key 存在 MCP URL 中，平台只对 CLI stderr 做脱敏；运行日志仍应按敏感数据管理。
- **权限模型**：当前依赖 `bypassPermissions` 自动放行 MCP 工具；若未来切到审批模式，需要把地图工具纳入审批设计。

## 参考

- 高德 MCP 接入指南：`https://developer.amap.com/api/mcp-server/gettingstarted`
- 高德 MCP Key 创建：`https://lbs.amap.com/api/mcp-server/create-project-and-key`
- Claude Code MCP 配置：`https://code.claude.com/docs/en/mcp`

端点和 Key 要求以高德官方文档为准；如果高德后续调整 MCP 路径或鉴权方式，只需改 `claude_runner.py` 中的 URL 构造。
