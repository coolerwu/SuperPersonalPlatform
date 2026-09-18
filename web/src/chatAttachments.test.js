import { describe, expect, test } from "vitest";

import {
  MAX_CHAT_IMAGES,
  chatAttachmentSource,
  chatAttachmentUrl,
  chatImageMime,
  imageFilesFromTransfer,
  isImageAttachment,
  readChatImageFiles,
} from "./chatAttachments.js";

const PNG_BYTES = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
]);

function imageFile(name = "shot.png", type = "image/png", content = PNG_BYTES) {
  return new File([content], name, { type });
}

describe("chat image helpers", () => {
  test("recognizes supported images by declared type or extension", () => {
    expect(chatImageMime(imageFile())).toBe("image/png");
    expect(chatImageMime(imageFile("photo.HEIC", "image/heic"))).toBe("");
    expect(chatImageMime(new File([PNG_BYTES], "clipboard.webp", { type: "" }))).toBe("image/webp");
    expect(isImageAttachment({ type: "image" })).toBe(true);
    expect(isImageAttachment({ mime: "image/jpeg" })).toBe(true);
    expect(isImageAttachment({ mime: "text/plain" })).toBe(false);
  });

  test("reads picked files into data-url attachments and reports rejections", async () => {
    const { attachments, errors } = await readChatImageFiles([
      imageFile("one.png"),
      new File(["notes"], "notes.txt", { type: "text/plain" }),
    ]);

    expect(errors).toEqual(["notes.txt 不是支持的图片（PNG/JPEG/GIF/WebP）"]);
    expect(attachments).toHaveLength(1);
    expect(attachments[0].type).toBe("image");
    expect(attachments[0].mime).toBe("image/png");
    expect(attachments[0].filename).toBe("one.png");
    expect(attachments[0].data_url.startsWith("data:image/png;base64,")).toBe(true);
  });

  test("stops at the per-message image limit", async () => {
    const { attachments, errors } = await readChatImageFiles(
      [imageFile("one.png"), imageFile("two.png")],
      { existing: MAX_CHAT_IMAGES - 1 },
    );

    expect(attachments).toHaveLength(1);
    expect(errors).toEqual([`一次最多上传 ${MAX_CHAT_IMAGES} 张图片`]);
  });

  test("collects image files from drag transfers and builds an authenticated URL", () => {
    const transfer = { files: [imageFile("drop.png"), new File(["x"], "notes.txt", { type: "text/plain" })] };
    expect(imageFilesFromTransfer(transfer).map((file) => file.name)).toEqual(["drop.png"]);
    expect(imageFilesFromTransfer({ files: [], items: [] })).toEqual([]);

    const stored = { id: "image_1", type: "image", session_path: "attachments/2/shot 1.png" };
    expect(chatAttachmentSource(stored, "session a", "assistant")).toBe(
      "/api/chat/sessions/session%20a/attachments?agent_id=assistant&path=attachments%2F2%2Fshot%201.png",
    );
    expect(chatAttachmentUrl("", "assistant", stored)).toBe("");
    expect(chatAttachmentSource({ ...stored, data_url: "data:image/png;base64,AAA" }, "session a", "assistant"))
      .toBe("data:image/png;base64,AAA");
  });
});
