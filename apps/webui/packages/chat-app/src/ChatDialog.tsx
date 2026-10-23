import { useEffect, useRef, type ReactNode } from 'react';

/** dswui overlay markup with native modal focus/escape handling for Icarus. */
export default function ChatDialog({
  open,
  label,
  onClose,
  children,
  search = false,
  side = false,
}: {
  open: boolean;
  label: string;
  onClose: () => void;
  children: ReactNode;
  search?: boolean;
  side?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current!;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
    return () => {
      if (node.open) node.close();
    };
  }, [open]);
  return (
    <dialog
      ref={dialog}
      className="dswui-modal-host"
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
  );
}
