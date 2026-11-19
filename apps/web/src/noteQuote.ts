import type { RandomNote } from "./types";

export const NOTE_QUOTE_LIMIT = 6000;
export const KNOWLEDGE_DIR_LABEL = "~/.super-personal-platform/knowledge";

/** Quote block handed to the composer when asking the Agent about a note. */
export function noteQuote(note: RandomNote): string {
  const truncated = note.content.length > NOTE_QUOTE_LIMIT;
  const body = truncated
    ? `${note.content.slice(0, NOTE_QUOTE_LIMIT)}\n（已截断，完整内容见上方路径）`
    : note.content;
  return `笔记：${note.path}（完整原文：${KNOWLEDGE_DIR_LABEL}/${note.path}）\n\n${body}`;
}
