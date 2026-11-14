import { FormEvent, useEffect, useState } from "react";
import { describeApiError } from "../errors";

type RenameSessionDialogProps = {
  open: boolean;
  title: string;
  onClose: () => void;
  onRename: (title: string) => Promise<void>;
};

export function RenameSessionDialog({
  open,
  title,
  onClose,
  onRename,
}: RenameSessionDialogProps) {
  const [value, setValue] = useState(title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setValue(title);
    setBusy(false);
    setError("");
  }, [open, title]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.isComposing) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const trimmed = value.trim();
  const unchanged = trimmed === title.trim();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!trimmed || unchanged || busy) return;
    setBusy(true);
    setError("");
    try {
      await onRename(trimmed);
    } catch (exc) {
      setError(describeApiError(exc));
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rename-session-heading"
        onClick={(event) => event.stopPropagation()}
        onSubmit={submit}
      >
        <div className="modal-heading">
          <h2 id="rename-session-heading">重命名会话</h2>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <label className="field-label" htmlFor="rename-session-title">
          标题
        </label>
        <input
          id="rename-session-title"
          autoFocus
          maxLength={80}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onFocus={(event) => event.target.select()}
        />
        {error && <div className="error-banner">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onClose}>
            取消
          </button>
          <button
            className="primary"
            type="submit"
            disabled={busy || !trimmed || unchanged}
          >
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </form>
    </div>
  );
}
