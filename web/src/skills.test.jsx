import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import {
  SkillsPage,
  skillFilePath,
  skillTemplate,
  validateSkillDocument,
  validateSkillId,
} from "./skills.jsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const CONFIG = `auth:
  token: secret-token
agents:
  definitions:
    - id: assistant
      name: 个人助理
      system_prompt: Be direct.
    - id: advisor
      name: 投资助手
      system_prompt: Be careful.
`;

const SKILL_A = "---\nname: web-research\ndescription: 结构化网页调研\n---\n\n# web-research\n";

function makeApi({ skills = {}, deleteStatus = 200 } = {}) {
  const calls = [];
  const api = vi.fn(async (path, options = {}) => {
    const payload = options.body ? JSON.parse(options.body) : {};
    calls.push({ path, method: options.method || "POST", payload });
    if (path === "/api/workspace/read" && payload.path === "config.yaml") {
      return { content: CONFIG };
    }
    if (path === "/api/workspace/list") {
      const entries = skills[payload.path];
      if (!entries) {
        const error = new Error("目录不存在");
        error.status = 404;
        throw error;
      }
      return { path: payload.path, entries };
    }
    if (path === "/api/workspace/read") {
      const content = skills[payload.path];
      if (content === undefined) {
        const error = new Error("文件不存在");
        error.status = 404;
        throw error;
      }
      return { path: payload.path, content, size: content.length, modified_at: 1, editable: true };
    }
    if (path === "/api/workspace/write") {
      return { ok: true, message: "文件已保存", file: { path: payload.path, content: payload.content } };
    }
    if (path === "/api/workspace/delete") {
      if (deleteStatus !== 200) {
        const error = new Error("删除失败");
        error.status = deleteStatus;
        throw error;
      }
      return { ok: true };
    }
    throw new Error(`unexpected request ${path}`);
  });
  return { api, calls };
}

function listEntry(name, size = 120) {
  return { name, path: name, type: "directory", size, modified_at: 1_700_000_000, deletable: true };
}

function fileEntry(name, size = 32) {
  return { name, path: name, type: "file", size, modified_at: 1_700_000_000, deletable: true };
}

function renderPage(setup) {
  return render(<SkillsPage api={setup.api} />);
}

test("shows only the selected agent skills and defaults to the first agent", async () => {
  const setup = makeApi({
    skills: {
      "agents/assistant/workspace/skills": [listEntry("web-research")],
      "agents/assistant/workspace/skills/web-research": [fileEntry("SKILL.md"), fileEntry("helper.py")],
      [skillFilePath("assistant", "web-research")]: SKILL_A,
    },
  });

  renderPage(setup);

  expect(await screen.findByText("个人助理")).toBeInTheDocument();
  expect(screen.getByText("投资助手")).toBeInTheDocument();
  // First agent is selected by default, so only its skills are rendered.
  expect(screen.getByText("web-research")).toBeInTheDocument();
  expect(screen.getByText("结构化网页调研")).toBeInTheDocument();
  expect(screen.queryByText("还没有技能。可以让 Agent 在对话里创建，或点右侧 + 新建。")).not.toBeInTheDocument();

  // Switching to an agent without a skills directory shows its empty state, not an error.
  fireEvent.click(screen.getByText("投资助手"));

  expect(screen.getByText("还没有技能。可以让 Agent 在对话里创建，或点右侧 + 新建。")).toBeInTheDocument();
  expect(screen.queryByText("web-research")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("blocks saving when frontmatter name does not match the skill directory", async () => {
  const setup = makeApi({
    skills: {
      "agents/assistant/workspace/skills": [listEntry("web-research")],
      "agents/assistant/workspace/skills/web-research": [fileEntry("SKILL.md")],
      [skillFilePath("assistant", "web-research")]: SKILL_A,
    },
  });

  renderPage(setup);
  fireEvent.click(await screen.findByText("web-research"));
  const editor = await screen.findByLabelText("SKILL.md");
  fireEvent.change(editor, { target: { value: SKILL_A.replace("name: web-research", "name: other-name") } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));

  expect(await screen.findByText(/frontmatter 的 name（other-name）必须与目录名 web-research 一致/)).toBeInTheDocument();
  expect(setup.calls.some((call) => call.path === "/api/workspace/write")).toBe(false);
});

test("saves a valid skill document with the skill file path", async () => {
  const setup = makeApi({
    skills: {
      "agents/assistant/workspace/skills": [listEntry("web-research")],
      "agents/assistant/workspace/skills/web-research": [fileEntry("SKILL.md")],
      [skillFilePath("assistant", "web-research")]: SKILL_A,
    },
  });

  renderPage(setup);
  fireEvent.click(await screen.findByText("web-research"));
  const editor = await screen.findByLabelText("SKILL.md");
  fireEvent.change(editor, { target: { value: `${SKILL_A}\n## 步骤\n1. 打开页面\n` } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));

  await waitFor(() => {
    const write = setup.calls.find((call) => call.path === "/api/workspace/write");
    expect(write).toBeTruthy();
    expect(write.payload.path).toBe("agents/assistant/workspace/skills/web-research/SKILL.md");
    expect(write.payload.content).toContain("## 步骤");
    expect(write.payload.create).toBeUndefined();
  });
  expect(await screen.findByText(/已保存，脚本\/规则会在下一次 Agent 运行时生效。/)).toBeInTheDocument();
});

test("creates a new skill with create flag and template", async () => {
  const setup = makeApi({
    skills: {
      "agents/assistant/workspace/skills": [listEntry("web-research")],
      "agents/assistant/workspace/skills/web-research": [fileEntry("SKILL.md")],
      [skillFilePath("assistant", "web-research")]: SKILL_A,
    },
  });

  renderPage(setup);
  await screen.findByText("web-research");
  fireEvent.click(screen.getByTitle("为 assistant 新建技能"));
  fireEvent.change(screen.getByLabelText(/技能 ID/), { target: { value: "data-cleanup" } });
  fireEvent.change(screen.getByLabelText(/描述/), { target: { value: "清理脏数据" } });
  fireEvent.click(screen.getByRole("button", { name: "创建" }));

  await waitFor(() => {
    const write = setup.calls.find((call) => call.path === "/api/workspace/write");
    expect(write).toBeTruthy();
    expect(write.payload.create).toBe(true);
    expect(write.payload.path).toBe("agents/assistant/workspace/skills/data-cleanup/SKILL.md");
    expect(write.payload.content).toBe(skillTemplate({ name: "data-cleanup", description: "清理脏数据" }));
  });
});

test("rejects duplicate or malformed skill ids when creating", async () => {
  const setup = makeApi({
    skills: {
      "agents/assistant/workspace/skills": [listEntry("web-research")],
      "agents/assistant/workspace/skills/web-research": [fileEntry("SKILL.md")],
      [skillFilePath("assistant", "web-research")]: SKILL_A,
    },
  });

  renderPage(setup);
  await screen.findByText("web-research");
  fireEvent.click(screen.getByTitle("为 assistant 新建技能"));
  fireEvent.change(screen.getByLabelText(/技能 ID/), { target: { value: "web-research" } });
  fireEvent.change(screen.getByLabelText(/描述/), { target: { value: "重复" } });
  fireEvent.click(screen.getByRole("button", { name: "创建" }));
  expect(await screen.findByText(/已经有同名技能 web-research/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText(/技能 ID/), { target: { value: "Bad Id" } });
  fireEvent.click(screen.getByRole("button", { name: "创建" }));
  expect(await screen.findByText(/技能 ID 只能用小写字母、数字和连字符/)).toBeInTheDocument();
  expect(setup.calls.some((call) => call.path === "/api/workspace/write")).toBe(false);
});

test("deletes a skill directory after confirmation and shows the contract notice", async () => {
  const withContract = `${SKILL_A}\n<!-- BEGIN USER CONTRACT -->\n- 必须中文回答\n<!-- END USER CONTRACT -->\n`;
  const setup = makeApi({
    skills: {
      "agents/assistant/workspace/skills": [listEntry("web-research"), listEntry("data-cleanup")],
      "agents/assistant/workspace/skills/web-research": [fileEntry("SKILL.md")],
      "agents/assistant/workspace/skills/data-cleanup": [fileEntry("SKILL.md")],
      [skillFilePath("assistant", "web-research")]: withContract,
      [skillFilePath("assistant", "data-cleanup")]: SKILL_A.replace(/web-research/g, "data-cleanup"),
    },
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);

  renderPage(setup);
  fireEvent.click(await screen.findByText("web-research"));

  expect(await screen.findByText(/这是用户硬约束/)).toBeInTheDocument();
  fireEvent.click(screen.getByTitle("删除技能 data-cleanup"));

  await waitFor(() => {
    const removal = setup.calls.find((call) => call.path === "/api/workspace/delete");
    expect(removal).toBeTruthy();
    expect(removal.payload.path).toBe("agents/assistant/workspace/skills/data-cleanup");
  });
  expect(await screen.findByText(/已删除技能 data-cleanup/)).toBeInTheDocument();
});

test("skill document validation follows Agent Skills naming rules", () => {
  expect(validateSkillId("web-research")).toBe("");
  expect(validateSkillId("Web_Research")).toMatch(/小写字母/);
  expect(validateSkillId("a".repeat(65))).toMatch(/64/);
  expect(validateSkillDocument({ content: SKILL_A, skillId: "web-research" })).toBe("");
  expect(validateSkillDocument({ content: "no frontmatter", skillId: "web-research" })).toMatch(/frontmatter/);
  expect(validateSkillDocument({ content: "---\nname: web-research\n---\n", skillId: "web-research" })).toMatch(/description/);
  expect(
    validateSkillDocument({
      content: `---\nname: web-research\ndescription: ${"x".repeat(1025)}\n---\n`,
      skillId: "web-research",
    })
  ).toMatch(/1024/);
});
