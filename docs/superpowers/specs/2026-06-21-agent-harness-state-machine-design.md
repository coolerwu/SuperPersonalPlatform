# Agent Harness 严格状态机设计

## 模式

Harness 仅保留 `PROMPT` 与 `AGENT` 两种模式。`PROMPT` 执行一次模型调用；
`AGENT` 替换原 `TOOLS` 模式，执行完整任务状态机。模式由调用方显式选择，AI 不参与
模式选择。

## 依赖边界

`Agent` 只保存 Agent 定义和模型定义，不保存 `llm_client`。`PromptRunner` 和
`AgentRunner` 通过构造函数获得模型网关等运行依赖。`run_agent` 根据 mode 获取对应
Runner，PROMPT 不加载工具、证据或验证依赖。

## Agent 状态机

`GOAL -> REASON -> ACT -> OBSERVE -> VERIFY -> FINALIZE -> COMPLETED`

`VERIFY` 不通过且仍有轮次时回到 `REASON`；缺少外部条件进入 `BLOCKED`；达到上限
进入 `FAILED`。另有 `CANCELLED` 终态。不提供 `DEGRADED`，AI 不能降低完成标准。

- GOAL：独立模型调用生成目标、完成条件、输出格式和证据要求。
- REASON：模型依据目标、证据和上轮验证反馈作局部决策或产生候选输出。
- ACT：顺序执行 Function Calls。
- OBSERVE：清洗工具结果并写入本次运行的证据账本，长期存储不自动写入。
- VERIFY：代码检查硬条件，新的模型上下文检查语义条件；原 Agent 不能自我放行。
- FINALIZE：仅在验证通过后，通过纯 prompt 按目标输出格式生成最终回答。

## 文件边界

`contracts.py` 保存契约；`runner.py` 保存公共分发入口；`modes/prompt.py` 保存
PromptRunner；`modes/agent.py` 保存 AgentRunner、状态与验证逻辑。包根仅重新导出稳定
公共 API。

## 失败规则

模型结构化输出无效、验证达到上限或无法满足完成条件时抛出明确运行错误，不返回伪
完成答案。工具异常作为失败证据反馈给下一轮推理；只有缺少用户输入、权限或外部资源
才进入 BLOCKED。
