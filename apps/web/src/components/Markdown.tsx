import { Children, isValidElement, memo, ReactNode, useMemo, useState } from "react";
import ReactMarkdown, { Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

import { copyText } from "../clipboard";
import { workspaceFileUrl, workspaceLinkUrl } from "../workspaceUrl";

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  const text = textOf(children);
  const first = Children.toArray(children)[0];
  const className = isValidElement<{ className?: string }>(first) ? first.props.className ?? "" : "";
  const language = /language-([\w-]+)/.exec(className)?.[1] ?? "";

  async function copy() {
    const copied = await copyText(text);
    setStatus(copied ? "copied" : "failed");
    window.setTimeout(() => setStatus("idle"), 1500);
  }

  return (
    <div className="code-block">
      <div className="code-block-head">
        <span>{language || "code"}</span>
        <button type="button" onClick={() => void copy()}>
          {status === "copied" ? "已复制" : status === "failed" ? "复制失败" : "复制"}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

function buildComponents(agentId?: string): Components {
  // Relative image paths are the Agent's workspace files. Without the
  // rewrite the browser asks the SPA route for them and gets index.html,
  // so the image silently degrades to its alt text.
  const imageSrc = (src?: string) =>
    agentId && src ? workspaceFileUrl(agentId, src) ?? src : src;
  const linkHref = (href?: string) =>
    agentId && href ? workspaceLinkUrl(agentId, href) ?? href : href;
  return {
    pre({ children }) {
      return <CodeBlock>{children}</CodeBlock>;
    },
    a({ href, children }) {
      return (
        <a href={linkHref(href)} target="_blank" rel="noreferrer">
          {children}
        </a>
      );
    },
    img({ src, alt }) {
      return <img src={imageSrc(src)} alt={alt ?? ""} loading="lazy" />;
    },
  };
}

export const Markdown = memo(function Markdown({
  content,
  streaming = false,
  agentId,
}: {
  content: string;
  streaming?: boolean;
  /** Owner of workspace-relative image and file links. */
  agentId?: string;
}) {
  const components = useMemo(() => buildComponents(agentId), [agentId]);
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          // Only $$...$$ is math; single-dollar text such as "$100 and $120"
          // must stay plain text.
          [remarkMath, { singleDollarTextMath: false }],
          remarkBreaks,
        ]}
        rehypePlugins={
          streaming
            ? [[rehypeKatex, { throwOnError: false, strict: false }]]
            : [
                [rehypeKatex, { throwOnError: false, strict: false }],
                [rehypeHighlight, { detect: true, ignoreMissing: true }],
              ]
        }
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
