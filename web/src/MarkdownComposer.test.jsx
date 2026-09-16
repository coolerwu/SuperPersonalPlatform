import React, { useState } from "react";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { MarkdownComposer } from "./MarkdownComposer.jsx";

afterEach(cleanup);
function Harness({ initial = "", onSend = () => {} }) {
  const [value, setValue] = useState(initial);
  return <><MarkdownComposer value={value} onChange={setValue} onSend={onSend} placeholder="消息" />
    <output data-testid="draft">{value}</output><button onClick={() => setValue("")}>清空</button></>;
}
function paste(text) {
  fireEvent.paste(screen.getByRole("textbox"), { clipboardData: { getData: (type) => type === "text/plain" ? text : "<script>bad()</script>" } });
}

test("pasted Markdown is editable and serialized without losing tables, tasks, or image syntax", () => {
  render(<Harness />);
  paste("# 标题\n\n**加粗**\n\n- [x] 完成\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n![图片](https://example.com/a.png)");
  const editor = screen.getByRole("textbox");
  expect(editor.querySelector("h1")).toHaveTextContent("标题");
  expect(editor.querySelector("strong")).toHaveTextContent("加粗");
  expect(editor.querySelector("table")).toHaveTextContent("A");
  expect(editor.querySelector('input[type="checkbox"]')).toBeChecked();
  expect(editor.querySelector("img")).toBeNull();
  expect(screen.getByTestId("draft")).toHaveTextContent("https://example.com/a.png");
  expect(screen.getByTestId("draft")).toHaveTextContent("**加粗**");
  fireEvent.click(screen.getByRole("button", { name: "清空" }));
  expect(editor).toHaveTextContent("");
  expect(editor.querySelector("h1")).toBeNull();
});

test("external draft replacement preserves Markdown formatting and disabled state", () => {
  const change = vi.fn();
  const { rerender } = render(<MarkdownComposer value="草稿" onChange={change} placeholder="消息" />);
  rerender(<MarkdownComposer value="> 引用\n\n**恢复**" onChange={change} placeholder="消息" disabled />);
  const editor = screen.getByRole("textbox");
  expect(editor.querySelector("blockquote")).toHaveTextContent("引用");
  expect(editor.querySelector("strong")).toHaveTextContent("恢复");
  expect(editor).toHaveAttribute("contenteditable", "false");
  expect(change).not.toHaveBeenCalled();
});

test("Shift Enter stays inside the code block", () => {
  render(<Harness initial={'```js\nconst a = 1;\n```'} />);
  const node = screen.getByRole("textbox");
  act(() => node.editor.commands.setTextSelection(node.editor.state.doc.content.size - 1));
  fireEvent.keyDown(node, { key: "Enter", shiftKey: true });
  expect(node.querySelector("pre")).toHaveTextContent("const a = 1;");
  expect(screen.getByTestId("draft")).toHaveTextContent("```js");
});
