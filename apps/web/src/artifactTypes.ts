const MARKDOWN_SUFFIXES = new Set([".md", ".markdown"]);

// Keep in sync with _TEXT_EXTENSIONS in apps/api/app/services/workspace_files.py.
const TEXT_SUFFIXES = new Set([
  ".md", ".markdown", ".txt", ".log", ".csv", ".tsv",
  ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
  ".xml", ".svg", ".html", ".htm", ".css", ".scss",
  ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".bash", ".zsh",
  ".sql", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp",
  ".rb", ".php", ".lua", ".swift", ".kt", ".r", ".m", ".tex",
]);

export function artifactSuffix(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

export function isMarkdownArtifact(name: string): boolean {
  return MARKDOWN_SUFFIXES.has(artifactSuffix(name));
}

export function isTextArtifact(name: string): boolean {
  return TEXT_SUFFIXES.has(artifactSuffix(name));
}

export function artifactBadge(name: string): string {
  const suffix = artifactSuffix(name);
  if (suffix === ".md" || suffix === ".markdown") return "MD";
  if (!suffix || suffix === ".") return "FILE";
  return suffix.slice(1).toUpperCase().slice(0, 4);
}
