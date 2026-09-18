// Client-side rules for Chat image uploads; the backend re-validates every file.
export const MAX_CHAT_IMAGES = 6;
export const MAX_CHAT_IMAGE_BYTES = 20 * 1024 * 1024;
export const CHAT_IMAGE_ACCEPT = "image/png,image/jpeg,image/gif,image/webp";

const SUPPORTED_IMAGE_MIMES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const IMAGE_MIME_BY_EXTENSION = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
};

function extensionOf(name) {
  const match = String(name || "").toLowerCase().match(/\.([a-z0-9]+)$/);
  return match ? match[1] : "";
}

export function chatImageMime(file) {
  const declared = String(file?.type || "").toLowerCase();
  if (SUPPORTED_IMAGE_MIMES.has(declared)) return declared;
  return IMAGE_MIME_BY_EXTENSION[extensionOf(file?.name)] || "";
}

export function isImageAttachment(attachment) {
  if (!attachment) return false;
  return String(attachment.type || "").toLowerCase() === "image"
    || String(attachment.mime || "").toLowerCase().startsWith("image/");
}

export function imageFilesFromTransfer(dataTransfer) {
  const files = Array.from(dataTransfer?.files || []);
  if (files.length) return files.filter((file) => chatImageMime(file));
  return Array.from(dataTransfer?.items || [])
    .filter((item) => item?.kind === "file" && String(item.type || "").toLowerCase().startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error(`读取 ${file.name || "图片"} 失败`));
    reader.readAsDataURL(file);
  });
}

export function newChatAttachmentId() {
  if (globalThis.crypto?.randomUUID) return `chat_image_${globalThis.crypto.randomUUID()}`;
  return `chat_image_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`;
}

export async function readChatImageFiles(fileList, { existing = 0 } = {}) {
  const files = Array.from(fileList || []);
  const attachments = [];
  const errors = [];
  let count = Number(existing) || 0;
  for (const file of files) {
    const mime = chatImageMime(file);
    if (!mime) {
      errors.push(`${file.name || "文件"} 不是支持的图片（PNG/JPEG/GIF/WebP）`);
      continue;
    }
    if (Number(file.size || 0) > MAX_CHAT_IMAGE_BYTES) {
      errors.push(`${file.name || "图片"} 超过 20MB`);
      continue;
    }
    if (count >= MAX_CHAT_IMAGES) {
      errors.push(`一次最多上传 ${MAX_CHAT_IMAGES} 张图片`);
      continue;
    }
    count += 1;
    try {
      attachments.push({
        id: newChatAttachmentId(),
        type: "image",
        mime,
        filename: file.name || "image",
        size: Number(file.size || 0),
        data_url: await readAsDataUrl(file),
      });
    } catch (exc) {
      errors.push(exc.message || "读取图片失败");
    }
  }
  return { attachments, errors };
}

export function chatAttachmentUrl(sessionId, agentId, attachment) {
  const path = String(attachment?.session_path || "");
  if (!sessionId || !path) return "";
  return `/api/chat/sessions/${encodeURIComponent(sessionId)}/attachments`
    + `?agent_id=${encodeURIComponent(agentId || "")}&path=${encodeURIComponent(path)}`;
}

// Prefer the in-memory data URL so just-sent images render before the next refresh.
export function chatAttachmentSource(attachment, sessionId, agentId) {
  const inline = String(attachment?.data_url || attachment?.url || "");
  if (inline) return inline;
  return chatAttachmentUrl(sessionId, agentId, attachment);
}
