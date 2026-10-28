import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** dswui overlay markup with native modal focus/escape handling for Icarus. */
export default function ChatDialog({
  open,
  label,
  onClose,
  children,
  search = false,
  side = false,
  className = '',
}: {
  open: boolean;
  label: string;
  onClose: () => void;
  children: ReactNode;
  search?: boolean;
  side?: boolean;
  className?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    const previousFocus = document.activeElement;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
    return () => {
      if (node.open) node.close();
      if (open && previousFocus instanceof HTMLElement && previousFocus.isConnected)
        previousFocus.focus({ preventScroll: true });
    };
  }, [open]);
  if (!open) return null;
  return createPortal(
    <div className="dswui chat-overlay-root">
      <dialog
        ref={dialog}
        className={`dswui-modal-host ${className}`}
        aria-label={label}
        onCancel={(event) => {
          event.preventDefault();
          onClose();
        }}
      >
        {open && (
          <div
            className={search ? 'search-overlay' : side ? 'overlay web-sources-overlay' : 'overlay'}
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) onClose();
            }}
          >
            {children}
          </div>
        )}
      </dialog>
    </div>,
    document.body,
  );
}
