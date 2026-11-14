import { useEffect, useState } from "react";

import { api } from "../api";
import { artifactBadge, isMarkdownArtifact, isTextArtifact } from "../artifactTypes";
import { formatBytes } from "../messageContent";
import type { ArtifactPreview, MessageArtifact } from "../types";
import { workspaceFileUrl } from "../workspaceUrl";
import { Markdown } from "./Markdown";

const PREVIEW_CACHE_LIMIT = 40;
const previewCache = new Map<string, ArtifactPreview>();

function previewKey(agentId: string, artifact: MessageArtifact): string {
  return `${agentId}:${artifact.path}:${artifact.size ?? 0}`;
}

function cachePreview(key: string, preview: ArtifactPreview) {
  if (previewCache.size >= PREVIEW_CACHE_LIMIT) {
    const oldest = previewCache.keys().next().value;
    if (oldest) previewCache.delete(oldest);
  }
  previewCache.set(key, preview);
}

type PreviewState =
  | { status: "loading" }
  | { status: "ready"; preview: ArtifactPreview }
  | { status: "error" };

function useArtifactPreview(
  agentId: string,
  artifact: MessageArtifact,
  enabled: boolean,
): PreviewState {
  const key = previewKey(agentId, artifact);
  const [state, setState] = useState<PreviewState>(() => {
    const cached = previewCache.get(key);
    return cached ? { status: "ready", preview: cached } : { status: "loading" };
  });

  useEffect(() => {
    if (!enabled) return;
    const cached = previewCache.get(key);
    if (cached) {
      setState({ status: "ready", preview: cached });
      return;
    }
    let cancelled = false;
    let retry: number | undefined;
    const controller = new AbortController();
    setState({ status: "loading" });

    const load = async (attempt: number) => {
      try {
        const preview = await api.previewWorkspaceFile(
          agentId,
          artifact.path,
          controller.signal,
        );
        if (cancelled) return;
        cachePreview(key, preview);
        setState({ status: "ready", preview });
      } catch {
        if (cancelled) return;
        if (attempt === 0) {
          retry = window.setTimeout(() => void load(1), 1000);
          return;
        }
        setState({ status: "error" });
      }
    };

    void load(0);
    return () => {
      cancelled = true;
      controller.abort();
      if (retry !== undefined) window.clearTimeout(retry);
    };
  }, [agentId, artifact.path, enabled, key]);

  return state;
}

function FileGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="26"
      height="26"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

function TileHead({ artifact }: { artifact: MessageArtifact }) {
  return (
    <div className="artifact-tile-head">
      <span className="artifact-badge">{artifactBadge(artifact.name)}</span>
      <span className="artifact-name" title={artifact.path}>
        {artifact.name}
      </span>
    </div>
  );
}

function TileFoot({
  artifact,
  href,
  note,
}: {
  artifact: MessageArtifact;
  href: string;
  note?: string;
}) {
  return (
    <div className="artifact-tile-foot">
      <span className="artifact-size">{formatBytes(artifact.size)}</span>
      {note && <span className="artifact-note">{note}</span>}
      <a
        className="artifact-open"
        href={href}
        target="_blank"
        rel="noreferrer"
        aria-label={`打开 ${artifact.name}`}
      >
        打开
      </a>
    </div>
  );
}

function TextArtifactTile({
  artifact,
  agentId,
  href,
}: {
  artifact: MessageArtifact;
  agentId: string;
  href: string;
}) {
  const markdown = isMarkdownArtifact(artifact.name);
  const preview = useArtifactPreview(agentId, artifact, true);

  return (
    <div className="artifact-tile">
      <TileHead artifact={artifact} />
      <div
        className="artifact-tile-body"
        tabIndex={0}
        aria-label={`${artifact.name} 内容预览`}
      >
        {preview.status === "loading" && (
          <div className="artifact-skeleton" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>
        )}
        {preview.status === "error" && (
          <div className="artifact-empty">无法预览</div>
        )}
        {preview.status === "ready" &&
          (preview.preview.content ? (
            markdown ? (
              <div className="artifact-tile-markdown">
                <Markdown
                  content={preview.preview.content}
                  agentId={agentId}
                  basePath={artifact.path}
                />
              </div>
            ) : (
              <pre className="artifact-tile-text">
                {preview.preview.content}
              </pre>
            )
          ) : (
            <div className="artifact-empty">空文件</div>
          ))}
      </div>
      <TileFoot
        artifact={artifact}
        href={href}
        note={
          preview.status === "ready" && preview.preview.truncated
            ? "已截断前 512 KB"
            : undefined
        }
      />
    </div>
  );
}

function ImageArtifactTile({
  artifact,
  href,
}: {
  artifact: MessageArtifact;
  href: string;
}) {
  return (
    <div className="artifact-tile">
      <TileHead artifact={artifact} />
      <a
        className="artifact-tile-image"
        href={href}
        target="_blank"
        rel="noreferrer"
        title={`打开 ${artifact.name}`}
      >
        <img src={href} alt={artifact.name} loading="lazy" />
      </a>
      <TileFoot artifact={artifact} href={href} />
    </div>
  );
}

function IconArtifactTile({
  artifact,
  href,
}: {
  artifact: MessageArtifact;
  href: string;
}) {
  return (
    <div className="artifact-tile">
      <TileHead artifact={artifact} />
      <a
        className="artifact-tile-icon"
        href={href}
        target="_blank"
        rel="noreferrer"
        title={`打开 ${artifact.name}`}
      >
        <FileGlyph />
        <span>{artifactBadge(artifact.name)}</span>
      </a>
      <TileFoot artifact={artifact} href={href} />
    </div>
  );
}

export function ArtifactGrid({
  artifacts,
  agentId,
}: {
  artifacts: MessageArtifact[];
  agentId: string;
}) {
  if (artifacts.length === 0) return null;
  return (
    <div className="message-artifacts">
      <div className="message-artifacts-label">产物</div>
      <div className="artifact-grid" aria-label="产物文件">
        {artifacts.map((artifact) => {
          const href = workspaceFileUrl(agentId, artifact.path) ?? "";
          if (artifact.kind === "image") {
            return (
              <ImageArtifactTile
                key={artifact.path}
                artifact={artifact}
                href={href}
              />
            );
          }
          if (isTextArtifact(artifact.name)) {
            return (
              <TextArtifactTile
                key={artifact.path}
                artifact={artifact}
                agentId={agentId}
                href={href}
              />
            );
          }
          return (
            <IconArtifactTile
              key={artifact.path}
              artifact={artifact}
              href={href}
            />
          );
        })}
      </div>
    </div>
  );
}
