import React, { act } from "react";
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

test("shared composer preserves IME and shift-enter, prevents sends while busy", async () => {
  const send = vi.fn();
  const { rerender } = render(<ChatComposer value="你好" onChange={() => {}} onSend={send} />);
  const input = await screen.findByRole("textbox");
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
  const input = await screen.findByRole("textbox", { name: "输入 @ 选择角色，或直接与主持交流" });
  changeInput(input, { target: { value: "@" } });
  fireEvent.click(screen.getByRole("option", { name: "评审" }));
  changeInput(input, { target: { value: "@评审 请检查 @" } });
  fireEvent.click(screen.getByRole("option", { name: "主持" }));
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one/messages", expect.objectContaining({ method: "POST" })));
  const call = api.mock.calls.find(([url]) => url.endsWith("/messages"));
  expect(JSON.parse(call[1].body).mentions).toEqual(["review", "host"]);
  expect(JSON.parse(call[1].body).client_message_id).toBeTruthy();
  await waitFor(() => expect(input).toHaveTextContent(""));
  changeInput(input, { target: { value: "请安排工作" } });
  fireEvent.click(screen.getByRole("button", { name: "开始协作" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one/collaborations", expect.anything()));
});

test("copying a group creates and opens the duplicate", async () => {
  const source = base();
  const copy = { ...source, id: "group_copy", name: "设计讨论 副本" };
  const api = vi.fn(async (url, options) => {
    if (url === "/api/workspace/read") return { content: config };
    if (url === "/api/chat-groups") return { groups: [{ id: source.id, name: source.name }] };
    if (url === `/api/chat-groups/${source.id}/duplicate` && options?.method === "POST") return copy;
    if (url === `/api/chat-groups/${copy.id}`) return copy;
    return source;
  });
  render(<ChatGroupsPage api={api} />);

  fireEvent.click(await screen.findByRole("button", { name: `复制群聊 ${source.name}` }));

  await waitFor(() => expect(api).toHaveBeenCalledWith(`/api/chat-groups/${source.id}/duplicate`, expect.objectContaining({ method: "POST" })));
  expect(await screen.findByText("设计讨论 副本")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: `复制群聊 ${source.name}` })).toBeInTheDocument();
});

test("adding a member from the member library copies it into the draft without leaking editor fields", async () => {
  const group = base();
  const preset = { id: "lib_writer", agent_id: "assistant", name: "文档整理", prompt: "整理文档" };
  const api = vi.fn(async (url, options) => {
    if (url === "/api/workspace/read") return { content: config };
    if (url === "/api/chat-groups" && !options) return { groups: [{ id: group.id, name: group.name }] };
    if (url === "/api/chat-groups/members" && !options) return { members: [preset] };
    return group;
  });
  render(<ChatGroupsPage api={api} />);

  fireEvent.click(await screen.findByRole("button", { name: "编辑群聊" }));
  fireEvent.click(screen.getByRole("button", { name: "从成员库添加" }));
  fireEvent.click(screen.getByRole("option", { name: "使用角色 文档整理" }));

  const names = screen.getAllByLabelText("群内名称");
  expect(names).toHaveLength(3);
  expect(names[2]).toHaveValue("文档整理");
  fireEvent.click(screen.getByRole("button", { name: "保存群聊" }));

  await waitFor(() => expect(api).toHaveBeenCalledWith(`/api/chat-groups/${group.id}`, expect.objectContaining({ method: "PUT" })));
  const body = JSON.parse(api.mock.calls.find(([, options]) => options?.method === "PUT")[1].body);
  expect(body.members).toHaveLength(3);
  body.members.forEach((member) => expect(member).not.toHaveProperty("_libraryId"));
  expect(body.members[2]).toMatchObject({ agent_id: "assistant", name: "文档整理", prompt: "整理文档" });
});

test("saving a member into the member library posts the role and reuses its id", async () => {
  const group = base();
  const posted = [];
  const api = vi.fn(async (url, options) => {
    if (url === "/api/workspace/read") return { content: config };
    if (url === "/api/chat-groups" && !options) return { groups: [{ id: group.id, name: group.name }] };
    if (url === "/api/chat-groups/members" && !options) return { members: posted };
    if (url === "/api/chat-groups/members" && options?.method === "POST") {
      const entry = JSON.parse(options.body);
      posted.push(entry);
      return entry;
    }
    return group;
  });
  render(<ChatGroupsPage api={api} />);

  fireEvent.click(await screen.findByRole("button", { name: "编辑群聊" }));
  fireEvent.click(screen.getByRole("button", { name: "存入成员库 主持" }));
  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toMatchObject({ agent_id: "assistant", name: "主持", prompt: "组织讨论" });

  fireEvent.click(screen.getByRole("button", { name: "存入成员库 主持" }));
  await waitFor(() => expect(posted).toHaveLength(2));
  expect(posted[1].id).toBe(posted[0].id);
});

test("member library dialog saves and deletes presets", async () => {
  const group = base();
  let stored = [{ id: "lib_review", agent_id: "assistant", name: "评审", prompt: "检查问题" }];
  const api = vi.fn(async (url, options) => {
    if (url === "/api/workspace/read") return { content: config };
    if (url === "/api/chat-groups" && !options) return { groups: [{ id: group.id, name: group.name }] };
    if (url === "/api/chat-groups/members" && !options) return { members: stored };
    if (url === "/api/chat-groups/members" && options?.method === "POST") {
      const entry = JSON.parse(options.body);
      stored = [entry];
      return entry;
    }
    if (url === "/api/chat-groups/members/lib_review" && options?.method === "DELETE") {
      stored = [];
      return { deleted: "lib_review" };
    }
    return group;
  });
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ChatGroupsPage api={api} />);

  fireEvent.click(await screen.findByRole("button", { name: "成员库" }));
  const name = screen.getByLabelText("群内名称");
  await userEvent.clear(name);
  await userEvent.type(name, "资深评审");
  fireEvent.click(screen.getByRole("button", { name: "保存角色 资深评审" }));

  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/members", expect.objectContaining({ method: "POST" })));
  const posted = api.mock.calls.find(([url, options]) => url === "/api/chat-groups/members" && options?.method === "POST")[1];
  expect(JSON.parse(posted.body)).toMatchObject({ id: "lib_review", agent_id: "assistant", name: "资深评审" });

  fireEvent.click(screen.getByRole("button", { name: "删除角色 资深评审" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/members/lib_review", expect.objectContaining({ method: "DELETE" })));
  expect(confirmSpy).toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole("button", { name: "删除角色 资深评审" })).not.toBeInTheDocument());
  confirmSpy.mockRestore();
});

test("renaming a group from the list posts the trimmed name and closes the dialog", async () => {
  const group = base();
  const api = vi.fn(async (url, options) => {
    if (url === "/api/workspace/read") return { content: config };
    if (url === "/api/chat-groups" && !options) return { groups: [{ id: group.id, name: group.name }] };
    if (url === `/api/chat-groups/${group.id}/rename`) return { ...group, name: JSON.parse(options.body).name };
    return group;
  });
  render(<ChatGroupsPage api={api} />);

  fireEvent.click(await screen.findByRole("button", { name: `重命名群聊 ${group.name}` }));
  const input = screen.getByLabelText("群名称");
  await userEvent.clear(input);
  await userEvent.type(input, "  改名后的群  ");
  fireEvent.click(screen.getByRole("button", { name: "保存名称" }));

  const renameCall = await waitFor(() => {
    const call = api.mock.calls.find(([url]) => url === `/api/chat-groups/${group.id}/rename`);
    expect(call).toBeTruthy();
    return call;
  });
  expect(renameCall[1].method).toBe("POST");
  expect(JSON.parse(renameCall[1].body)).toEqual({ name: "改名后的群" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "保存名称" })).not.toBeInTheDocument());
});

test("deleting a group asks for confirmation and selects the remaining group", async () => {
  const first = base();
  const second = { ...base(), id: "group_two", name: "第二个群" };
  let list = [{ id: first.id, name: first.name }, { id: second.id, name: second.name }];
  const api = vi.fn(async (url, options) => {
    if (url === "/api/workspace/read") return { content: config };
    if (url === "/api/chat-groups" && !options) return { groups: list };
    if (url === `/api/chat-groups/${first.id}` && options?.method === "DELETE") {
      list = list.filter((entry) => entry.id !== first.id);
      return { deleted: first.id, name: first.name };
    }
    if (url === `/api/chat-groups/${second.id}`) return second;
    return first;
  });
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ChatGroupsPage api={api} />);

  fireEvent.click(await screen.findByRole("button", { name: `删除群聊 ${first.name}` }));

  await waitFor(() => expect(api).toHaveBeenCalledWith(`/api/chat-groups/${first.id}`, expect.objectContaining({ method: "DELETE" })));
  expect(confirmSpy).toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole("button", { name: `删除群聊 ${first.name}` })).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: `删除群聊 ${second.name}` })).toBeInTheDocument();
  confirmSpy.mockRestore();
});

test("cancelling the delete confirmation keeps the group", async () => {
  const group = base();
  const api = mockApi(group);
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<ChatGroupsPage api={api} />);

  fireEvent.click(await screen.findByRole("button", { name: `删除群聊 ${group.name}` }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(api.mock.calls.some(([, options]) => options?.method === "DELETE")).toBe(false);
  expect(screen.getByRole("button", { name: `删除群聊 ${group.name}` })).toBeInTheDocument();
  confirmSpy.mockRestore();
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
  expect(screen.getByRole("textbox", { name: "输入 @ 选择角色，或直接与主持交流" })).toHaveAttribute("contenteditable", "false");
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
  const input = await screen.findByRole("textbox", { name: "输入 @ 选择角色，或直接与主持交流" });
  changeInput(input, { target: { value: "草稿保留" } });
  fireEvent.click(screen.getAllByRole("button", { name: "引用消息" })[0]);
  expect(input).toHaveTextContent("草稿保留");
  await waitFor(() => expect(input).toHaveFocus());
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

function changeInput(node, event) {
  if (node.editor) {
    act(() => node.editor.commands.setContent(event.target.value, { contentType: "markdown" }));
  } else {
    fireEvent.change(node, event);
  }
}
