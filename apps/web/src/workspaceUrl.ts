const EXTERNAL_URL = /^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|\/\/)/;

// Only links that name a file are treated as workspace files; a bare
// relative link such as "www.example.com" keeps its literal meaning.
const FILE_LINK =
  /\.(?:md|markdown|txt|pdf|csv|json|html?|xlsx?|docx?|pptx?|zip|png|jpe?g|gif|webp|bmp)$/i;

export function isRelativeWorkspaceUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith("/api/")) {
    return false;
  }
  return !EXTERNAL_URL.test(trimmed);
}

/** `/api/agents/<id>/files/<workspace-relative path>` or null. */
export function workspaceFileUrl(agentId: string, raw: string): string | null {
  if (!agentId || !isRelativeWorkspaceUrl(raw)) return null;
  let relative = raw.trim().replace(/^\.\//, "");
  if (relative.startsWith("/")) {
    // Old attachments were written as /uploads/<name>; any other rooted
    // path is a server filesystem path, not a workspace file.
    if (!relative.startsWith("/uploads/")) return null;
    relative = relative.slice(1);
  }
  if (!relative) return null;
  const encoded = relative.split("/").map(encodeURIComponent).join("/");
  return `/api/agents/${encodeURIComponent(agentId)}/files/${encoded}`;
}

/** Workspace links only for names that look like files. */
export function workspaceLinkUrl(agentId: string, raw: string): string | null {
  if (!FILE_LINK.test(raw.trim())) return null;
  return workspaceFileUrl(agentId, raw);
}
