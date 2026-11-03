const MAX_IMAGE_EDGE = 1600;
const COMPRESS_OVER_BYTES = 2 * 1024 * 1024;

function jpegName(name: string): string {
  const dot = name.lastIndexOf(".");
  return `${dot > 0 ? name.slice(0, dot) : name}.jpg`;
}

async function loadBitmap(
  file: File,
): Promise<{ image: ImageBitmap | HTMLImageElement; cleanup: () => void }> {
  if ("createImageBitmap" in window) {
    const image = await createImageBitmap(file);
    return { image, cleanup: () => image.close() };
  }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return { image, cleanup: () => URL.revokeObjectURL(url) };
  } catch (exc) {
    URL.revokeObjectURL(url);
    throw exc;
  }
}

/**
 * Phone photos are often 4-10 MB. Sending those into the model would bloat the
 * checkpoint, so large images are downscaled before upload.
 */
export async function prepareImageForUpload(file: File): Promise<File> {
  if (!file.type.startsWith("image/") || file.size <= COMPRESS_OVER_BYTES) {
    return file;
  }
  try {
    const { image, cleanup } = await loadBitmap(file);
    try {
      const width = "width" in image ? image.width : 0;
      const height = "height" in image ? image.height : 0;
      if (!width || !height) return file;
      const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(width, height));
      if (scale === 1) return file;
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      const context = canvas.getContext("2d");
      if (!context) return file;
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", 0.85),
      );
      if (!blob) return file;
      return new File([blob], jpegName(file.name), {
        type: "image/jpeg",
        lastModified: file.lastModified,
      });
    } finally {
      cleanup();
    }
  } catch {
    return file;
  }
}
