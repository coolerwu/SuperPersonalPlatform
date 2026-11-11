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
  const relative = raw.trim().replace(/^\.\//, "").replace(/^\//, "");
  if (!relative) return null;
  const encoded = relative.split("/").map(encodeURIComponent).join("/");
  return `/api/agents/${encodeURIComponent(agentId)}/files/${encoded}`;
}

/** Workspace links only for names that look like files. */
export function workspaceLinkUrl(agentId: string, raw: string): string | null {
  if (!FILE_LINK.test(raw.trim())) return null;
  return workspaceFileUrl(agentId, raw);
}
