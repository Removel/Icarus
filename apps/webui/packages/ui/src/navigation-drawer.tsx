import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Button } from '@douyinfe/semi-ui';
import { X } from 'lucide-react';

export function useNarrowLayout() {
  const [narrow, setNarrow] = useState(() => matchMedia('(max-width: 767px)').matches);
  useEffect(() => {
    const media = matchMedia('(max-width: 767px)');
    const update = () => setNarrow(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return narrow;
}

export function NavigationDrawer({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
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
      className="navigation-drawer"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom
        )
          onClose();
      }}
    >
      <div className="navigation-drawer-heading">
        <h2 id={titleId}>{title}</h2>
        <Button
          theme="borderless"
          type="tertiary"
          icon={<X size={18} />}
          aria-label={`关闭${title}`}
          onClick={onClose}
        />
      </div>
      <div className="navigation-drawer-content">{children}</div>
    </dialog>
  );
}
