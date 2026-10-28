import { Button, Toast } from '@icarus/ui';
import { Copy } from 'lucide-react';
import type { Page } from './types';

export function CopyId({ id, label }: { id: string; label: 'Person ID' | 'Circle ID' }) {
  return (
    <Button
      className="rela-copy-id"
      theme="borderless"
      aria-label={`复制完整 ${label}`}
      onClick={() => {
        void navigator.clipboard.writeText(id).then(
          () => Toast.success(`已复制 ${label}`),
          () => Toast.error('复制失败，请手动选择 ID'),
        );
      }}
    >
      <span>ID</span>
      <code>{id}</code>
      <Copy size={13} aria-hidden="true" />
    </Button>
  );
}

export function ReadError({
  error,
  retry,
  reset,
}: {
  error?: Error;
  retry: () => void;
  reset?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="rela-error" role="alert">
      <span>{error.message}</span>
      <Button onClick={retry}>重试</Button>
      {reset && <Button onClick={reset}>回到首屏</Button>}
    </div>
  );
}

export function Pagination({
  page,
  previous,
  next,
  disabled,
}: {
  page?: Page<unknown>;
  previous?: () => void;
  next: (cursor: string) => void;
  disabled?: boolean;
}) {
  if (!previous && !page?.has_more) return null;
  return (
    <nav className="rela-pagination" aria-label="分页">
      <Button size="small" disabled={disabled || !previous} onClick={previous}>
        上一页
      </Button>
      <Button
        size="small"
        disabled={disabled || !page?.has_more || !page.next_cursor}
        onClick={() => page?.next_cursor && next(page.next_cursor)}
      >
        下一页
      </Button>
    </nav>
  );
}
