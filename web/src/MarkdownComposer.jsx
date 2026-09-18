import React, { useEffect, useRef } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import Paragraph from "@tiptap/extension-paragraph";
import { Extension } from "@tiptap/core";
import { TableKit } from "@tiptap/extension-table";
import { TaskList, TaskItem } from "@tiptap/extension-list";
import Placeholder from "@tiptap/extension-placeholder";

// Keep image syntax as text: composing a message must not fetch remote images.
const ImageSyntax = Extension.create({
  name: "imageSyntax", markdownTokenName: "image",
  parseMarkdown: (token) => ({ type: "text", text: token.raw || `![${token.text || ""}](${token.href || ""})` }),
});

// A single document owns selection, composition and undo; React owns the draft.
export function MarkdownComposer({ value, onChange, onSend, busy, disabled, placeholder, quote, canSend }) {
  const current = useRef({ value, onChange, onSend, busy, disabled, canSend });
  current.current = { value, onChange, onSend, busy, disabled, canSend };
  const emitted = useRef(value);
  const composing = useRef(false);
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ paragraph: false, underline: false, link: { openOnClick: false }, trailingNode: false }),
      Paragraph.extend({
        parseMarkdown(token, helpers) {
          if (token.tokens?.length === 1 && token.tokens[0].type === "image") {
            return helpers.createNode("paragraph", undefined, helpers.parseInline(token.tokens));
          }
          return this.parent(token, helpers);
        },
      }),
      TableKit.configure({ table: { resizable: false } }),
      TaskList, TaskItem.configure({ nested: true }), ImageSyntax,
      Markdown.configure({ markedOptions: { breaks: true } }),
      Placeholder.configure({ placeholder }),
    ],
    content: value,
    contentType: "markdown",
    editable: !disabled,
    shouldRerenderOnTransaction: false,
    editorProps: {
      attributes: { class: "chat-markdown-editor", role: "textbox", "aria-multiline": "true", "aria-label": placeholder, "data-placeholder": placeholder },
      handleDOMEvents: {
        compositionstart: () => { composing.current = true; return false; },
        compositionend: () => { composing.current = false; return false; },
      },
      handleKeyDown: (view, event) => {
        if (event.key !== "Enter" || composing.current || view.composing || event.isComposing || event.keyCode === 229) return false;
        event.preventDefault();
        if (event.shiftKey) {
          const { $from, empty } = view.state.selection;
          const fence = $from.parent.textContent.match(/^```([\w+-]*)$/);
          if (empty && fence && $from.parent.type.name === "paragraph") {
            view.dom.editor.chain().deleteRange({ from: $from.start(), to: $from.end() }).setCodeBlock({ language: fence[1] || null }).run();
            return true;
          }
          if (empty && $from.parent.type.name === "codeBlock" && $from.parent.textContent.slice(0, $from.parentOffset).endsWith("\n```")) {
            view.dom.editor.chain().deleteRange({ from: $from.pos - 4, to: $from.pos }).exitCode().run();
            return true;
          }
          view.dom.editor.commands.first(({ commands }) => [
            () => commands.newlineInCode(),
            () => commands.splitListItem("listItem"),
            () => commands.splitListItem("taskItem"),
            () => commands.liftEmptyBlock(),
            () => commands.splitBlock(),
          ]);
        } else if (!current.current.busy && !current.current.disabled && (current.current.canSend ?? Boolean(current.current.value.trim()))) {
          current.current.onSend();
        }
        return true;
      },
      handlePaste: (view, event) => {
        const text = event.clipboardData?.getData("text/plain");
        if (!text || view.state.selection.$from.parent.type.name === "codeBlock") return false;
        event.preventDefault();
        view.dom.editor.commands.insertContent(text, { contentType: "markdown" });
        return true;
      },
    },
    onUpdate: ({ editor }) => {
      const markdown = editor.isEmpty ? "" : editor.getMarkdown();
      emitted.current = markdown;
      current.current.onChange(markdown);
    },
  });
  useEffect(() => {
    if (!editor || value === emitted.current) return;
    emitted.current = value;
    editor.commands.setContent(value, { contentType: "markdown", emitUpdate: false });
  }, [editor, value]);
  useEffect(() => { editor?.setEditable(!disabled, false); }, [editor, disabled]);
  useEffect(() => { if (quote) editor?.commands.focus(); }, [editor, quote]);
  return <EditorContent editor={editor} className="chat-editor-container" />;
}
