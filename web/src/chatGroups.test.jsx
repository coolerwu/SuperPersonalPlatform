import React from "react";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ChatGroupsPage } from "./chatGroups.jsx";
import { ApprovalPanel, ChatComposer } from "./chatComponents.jsx";

afterEach(cleanup);
const config = "agents:\n  definitions:\n    - id: assistant\n      name: 基础助手\n";
const base = () => ({ id: "group_one", name: "设计讨论", archived: false, members: [
  { id: "host", name: "主持", agent_id: "assistant", prompt: "组织讨论" },
  { id: "review", name: "评审", agent_id: "assistant", prompt: "检查问题" },
], host_member_id: "host", messages: [], executions: [] });

function mockApi(group = base()) {
  return vi.fn(async (url, options) => {
    if (url === "/api/workspace/read") return { content: config };
    if (url === "/api/chat-groups" && !options) return { groups: [{ id: group.id, name: group.name }] };
    if (options?.method === "PUT") return { ...group, ...JSON.parse(options.body) };
    return group;
  });
}

test("shared composer preserves IME and shift-enter, prevents sends while busy", () => {
  const send = vi.fn();
  const { rerender } = render(<ChatComposer value="你好" onChange={() => {}} onSend={send} />);
  const input = screen.getByRole("textbox");
  fireEvent.compositionStart(input);
  fireEvent.keyDown(input, { key: "Enter" });
  expect(send).not.toHaveBeenCalled();
  fireEvent.compositionEnd(input);
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  expect(send).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(send).toHaveBeenCalledTimes(1);
  rerender(<ChatComposer value="你好" onChange={() => {}} onSend={send} busy />);
  fireEvent.keyDown(input, { key: "Enter" });
  expect(send).toHaveBeenCalledTimes(1);
});

test("shared approval panel prevents duplicate submits and restores controls after failure", async () => {
  let rejectDecision;
  const onDecision = vi.fn(() => new Promise((_, reject) => { rejectDecision = reject; }));
  render(<ApprovalPanel approval={{ interrupts: [{ actions: [{ name: "write_file", args: { file_path: "/webdav/a.md" } }] }] }} onDecision={onDecision} />);

  fireEvent.click(screen.getByRole("button", { name: "仅批准本次" }));
  fireEvent.click(screen.getByRole("button", { name: "批准中…" }));
  expect(onDecision).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "批准中…" })).toBeDisabled();

  rejectDecision(new Error("审批接口不可用"));
  expect(await screen.findByRole("alert")).toHaveTextContent("审批接口不可用");
  expect(screen.getByRole("button", { name: "仅批准本次" })).toBeEnabled();
});

test("group mentions submit member IDs in textual order and default sends have no mentions", async () => {
  const api = mockApi();
  render(<ChatGroupsPage api={api} />);
  const input = await screen.findByPlaceholderText("输入 @ 选择角色，或直接与主持交流");
  fireEvent.change(input, { target: { value: "@" } });
  fireEvent.click(screen.getByRole("option", { name: "评审" }));
  fireEvent.change(input, { target: { value: "@评审 请检查 @" } });
  fireEvent.click(screen.getByRole("option", { name: "主持" }));
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one/messages", expect.objectContaining({ method: "POST" })));
  const call = api.mock.calls.find(([url]) => url.endsWith("/messages"));
  expect(JSON.parse(call[1].body).mentions).toEqual(["review", "host"]);
  expect(JSON.parse(call[1].body).client_message_id).toBeTruthy();
  await waitFor(() => expect(input).toHaveValue(""));
  fireEvent.change(input, { target: { value: "请安排工作" } });
  fireEvent.click(screen.getByRole("button", { name: "开始协作" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one/collaborations", expect.anything()));
});

test("editing uses PUT and stable member keys preserve typing focus", async () => {
  const api = mockApi();
  render(<ChatGroupsPage api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑群聊" }));
  const names = screen.getAllByLabelText("群内名称");
  await userEvent.type(names[0], "负责人");
  expect(names[0]).toHaveFocus();
  expect(names[0]).toHaveValue("主持负责人");
  fireEvent.click(screen.getByRole("button", { name: "保存群聊" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one", expect.objectContaining({ method: "PUT" })));
  const call = api.mock.calls.find(([, options]) => options?.method === "PUT");
  expect(JSON.parse(call[1].body)).not.toHaveProperty("_groupId");
});

test("restored group execution uses shared thinking and approval components and can stop", async () => {
  const group = base();
  group.executions = [{ id: "exec", status: "waiting_approval", round: 1, automatic: true, current_step: { name: "评审" } }];
  group.active_run = { run_id: "run-1", state: { status: "waiting_approval" }, partial: { content: "初步分析", thinking: ["检查共享文档"] }, approval: { status: "pending", request: { interrupts: [{ actions: [{ name: "write_file", args: { path: "/webdav/a.md" } }] }] } } };
  const api = mockApi(group);
  render(<ChatGroupsPage api={api} />);
  expect(await screen.findByText("初步分析")).toBeVisible();
  expect(screen.getByText("检查共享文档")).toBeVisible();
  expect(screen.getByText("等待操作审批")).toBeVisible();
  expect(screen.getByPlaceholderText("输入 @ 选择角色，或直接与主持交流")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "仅批准本次" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/runs/run-1/resume", expect.objectContaining({ method: "POST" })));
  await waitFor(() => expect(screen.getByRole("button", { name: "停止" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "停止" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one/collaborations/exec/stop", expect.anything()));
});

test("mention matching does not accidentally target a member with a prefix name", async () => {
  const { mentionPosition } = await import("./chatGroups.jsx");
  expect(mentionPosition("@产品经理 请设计", "@产品")).toBe(-1);
  expect(mentionPosition("@产品经理 @产品 请设计", "@产品")).toBe(6);
});

test("group approval failure stays actionable and successful retry survives stale snapshot", async () => {
  const group = base();
  group.executions = [{ id: "exec", status: "waiting_approval", current_step: { name: "主持" } }];
  group.active_run = { run_id: "r", state: { status: "waiting_approval" }, approval: { status: "pending", request: { interrupts: [{ id: "i", actions: [{ name: "write_file" }] }] } } };
  const fallback = mockApi(group);
  let calls = 0;
  const api = vi.fn(async (url, opts) => {
    if (url === "/api/runs/r/resume") { if (++calls === 1) throw new Error("连接失败"); return {}; }
    return fallback(url, opts);
  });
  render(<ChatGroupsPage api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "仅批准本次" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("连接失败");
  fireEvent.click(screen.getByRole("button", { name: "仅批准本次" }));
  expect(await screen.findByRole("status")).toHaveTextContent("审批已提交");
  expect(screen.queryByRole("button", { name: "仅批准本次" })).not.toBeInTheDocument();
  expect(calls).toBe(2);
});

test("reading history keeps scroll position until user chooses latest", async () => {
  const { ChatMessageList } = await import("./chatComponents.jsx");
  const messages = [{ id: "a", role: "assistant", content: "旧消息" }];
  const { container, rerender } = render(<ChatMessageList messages={messages} />);
  const node = container.querySelector(".chat-messages");
  Object.defineProperties(node, { scrollHeight: { configurable: true, value: 1200 }, clientHeight: { configurable: true, value: 300 } });
  node.scrollTop = 200;
  fireEvent.scroll(node);
  rerender(<ChatMessageList messages={[...messages, { id: "b", role: "assistant", content: "新消息" }]} />);
  expect(node.scrollTop).toBe(200);
  fireEvent.click(screen.getByRole("button", { name: "有新消息 · 回到底部" }));
  expect(node.scrollTop).toBe(1200);
});

test("quote selection preserves draft, focuses input and retries identical group request", async () => {
  const group = base();
  group.messages = [{ id: "m1", seq: 1, role: "assistant", speaker: "评审", content: "完整引用原文" }, { id: "m2", seq: 2, role: "user", speaker: "你", content: "第二条" }];
  const fallback = mockApi(group);
  const bodies = [];
  const api = vi.fn(async (url, options) => {
    if (url.endsWith("/messages") && options?.method === "POST") {
      bodies.push(JSON.parse(options.body));
      if (bodies.length === 1) throw new Error("发送失败");
      return group;
    }
    return fallback(url, options);
  });
  render(<ChatGroupsPage api={api} />);
  const input = await screen.findByPlaceholderText("输入 @ 选择角色，或直接与主持交流");
  fireEvent.change(input, { target: { value: "草稿保留" } });
  fireEvent.click(screen.getAllByRole("button", { name: "引用消息" })[0]);
  expect(input).toHaveValue("草稿保留");
  expect(input).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "取消引用" }));
  expect(screen.queryByRole("button", { name: "取消引用" })).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "引用消息" })[1]);
  fireEvent.click(screen.getAllByRole("button", { name: "引用消息" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await screen.findByText("发送失败");
  expect(screen.getByRole("button", { name: "取消引用" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "取消引用" })).not.toBeInTheDocument());
  expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[0].reply_to_message_id).toBe("m1");
});

test("selected quote excludes other messages and preserves source identity", async () => {
  const { ChatMessageList } = await import("./chatComponents.jsx");
  const onQuote = vi.fn();
  const { container } = render(<ChatMessageList messages={[{ id: "a", seq: 1, role: "assistant", content: "前文选中片段后文" }, { id: "b", seq: 2, role: "user", content: "其他消息" }]} onQuote={onQuote} />);
  const node = container.querySelector('[data-quote-body="a"] p').firstChild;
  const range = document.createRange();
  range.setStart(node, 2); range.setEnd(node, 6);
  window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
  fireEvent(document, new Event("selectionchange"));
  fireEvent.click(await screen.findByRole("button", { name: "引用所选内容" }));
  expect(onQuote).toHaveBeenCalledWith(expect.objectContaining({ id: "a", seq: 1, content: "选中片段", excerpt: "选中片段" }));
  const cross = document.createRange();
  cross.setStart(node, 0); cross.setEnd(container.querySelector('[data-quote-body="b"] pre').firstChild, 2);
  window.getSelection().removeAllRanges(); window.getSelection().addRange(cross);
  fireEvent(document, new Event("selectionchange"));
  expect(screen.queryByRole("button", { name: "引用所选内容" })).not.toBeInTheDocument();
  window.getSelection().removeAllRanges();
});

test("file approval sends the ten minute scope and displays the exact file", async () => {
  const group = base();
  group.executions = [{ id: "exec", status: "waiting_approval", current_step: { name: "主持" } }];
  group.active_run = { run_id: "r", state: { status: "waiting_approval" }, approval: { status: "pending", request: { interrupts: [{ id: "i", actions: [{ name: "edit_file", args: { file_path: "/webdav/日记.md" } }] }] } } };
  const api = mockApi(group);
  render(<ChatGroupsPage api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "批准当前文件（10 分钟）" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/runs/r/resume", {
    method: "POST", body: JSON.stringify({ decision: "approve", message: "", scope: "file_10min" }),
  }));
});

test("mixed file approval does not offer a misleading single file grant", async () => {
  const { ApprovalPanel } = await import("./chatComponents.jsx");
  render(<ApprovalPanel approval={{ interrupts: [{ actions: [
    { name: "write_file", args: { file_path: "/webdav/a.md" } },
    { name: "write_file", args: { file_path: "/webdav/b.md" } },
  ] }] }} onDecision={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "批准当前文件（10 分钟）" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "仅批准本次" })).toBeEnabled();
});
