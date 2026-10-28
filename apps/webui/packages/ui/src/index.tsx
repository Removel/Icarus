import '@douyinfe/semi-ui/react19-adapter';
import {
  cloneElement,
  isValidElement,
  useRef,
  useState,
  type ComponentProps,
  type ReactElement,
  type ReactNode,
} from 'react';
import { ArrowDown, Info, Search, X, SearchX } from 'lucide-react';
import { flushSync } from 'react-dom';
import { Button, Input, Modal as SemiModal, Spin, Tooltip } from '@douyinfe/semi-ui';

export {
  CodeHighlight,
  Button,
  Input,
  TextArea,
  Toast,
  Switch,
  Tooltip,
  Progress,
  Spin,
  Checkbox,
  Layout,
  Dropdown,
} from '@douyinfe/semi-ui';
export { navigate, useHashLocation, useUnsavedChanges } from './navigation';
export { NavigationDrawer, useNarrowLayout } from './navigation-drawer';
export { Select } from './Select';
export { serviceFailure, taskFailure } from './serviceErrors';

export function InfoTip({ label, content }: { label: string; content: string }) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <Tooltip
      content={content}
      trigger="custom"
      visible={open}
      onClickOutSide={() => setOpen(false)}
      onEscKeyDown={() => setOpen(false)}
      position="topLeft"
      motion={false}
      closeOnEsc
      getPopupContainer={() => trigger.current?.closest('dialog') ?? document.body}
    >
      <button
        ref={trigger}
        type="button"
        className="info-tip"
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && open) {
            event.stopPropagation();
            setOpen(false);
          }
        }}
      >
        <Info size={14} aria-hidden="true" />
      </button>
    </Tooltip>
  );
}

let layoutTransition: ViewTransition | undefined;

export function transitionLayout(update: () => void, animate = true) {
  layoutTransition?.skipTransition();
  layoutTransition = undefined;
  if (
    !animate ||
    !document.startViewTransition ||
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  ) {
    update();
    return;
  }
  const transition = document.startViewTransition(() => flushSync(update));
  layoutTransition = transition;
  void transition.ready.catch(() => {});
  const clear = () => {
    if (layoutTransition === transition) {
      layoutTransition = undefined;
    }
  };
  void transition.finished.then(clear, clear);
}

export function LoadingIndicator({
  label = '正在加载',
  overlay = false,
}: {
  label?: string;
  overlay?: boolean;
}) {
  return (
    <div
      className={`loading-indicator${overlay ? ' is-overlay' : ''}`}
      role="status"
      aria-label={label}
    >
      <Spin />
    </div>
  );
}

export function Modal({
  okText = '确认',
  cancelText = '取消',
  okButtonProps,
  cancelButtonProps,
  centered = true,
  className,
  modalRender,
  ...props
}: ComponentProps<typeof SemiModal>) {
  return (
    <SemiModal
      motion={false}
      preventScroll
      {...props}
      centered={centered}
      className={['icarus-modal', className].filter(Boolean).join(' ')}
      okText={okText}
      cancelText={cancelText}
      modalRender={(node) => {
        // Semi uses fixed label IDs; nested dialogs must announce their own title.
        const labeled =
          typeof props.title === 'string' && isValidElement(node)
            ? cloneElement(node as ReactElement<Record<string, unknown>>, {
                'aria-label': props.title,
                'aria-labelledby': undefined,
                'aria-describedby': undefined,
              })
            : node;
        return modalRender ? modalRender(labeled) : labeled;
      }}
      okButtonProps={{
        'aria-label': typeof okText === 'string' ? okText : '确认',
        ...okButtonProps,
      }}
      cancelButtonProps={{
        'aria-label': typeof cancelText === 'string' ? cancelText : '取消',
        ...cancelButtonProps,
      }}
    />
  );
}

export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <img
      className="icarus-brand-mark"
      src="/icarus.svg"
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
    />
  );
}

export function PageHeading({
  title,
  hideTitle = false,
  description,
  context,
  children,
}: {
  title: string;
  hideTitle?: boolean;
  description?: string;
  context?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div className="page-heading-main">
        <div className="page-title-row">
          <h1 className={hideTitle ? 'visually-hidden' : undefined}>{title}</h1>
          {context}
        </div>
        {description && <p>{description}</p>}
      </div>
      {children && <div className="heading-actions">{children}</div>}
    </header>
  );
}

export function Status({
  children,
  tone = 'green',
}: {
  children: ReactNode;
  tone?: 'green' | 'blue' | 'gray' | 'amber' | 'pink';
}) {
  return (
    <span className={`status status-${tone === 'pink' ? 'blue' : tone}`}>
      <span className="status-dot" aria-hidden="true" />
      {children}
    </span>
  );
}

export function Tabs({
  items,
  value,
  onChange,
}: {
  items: { id: string; label: string; count?: number }[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="content-tabs" role="tablist" aria-label="记忆状态">
      {items.map((item, index) => (
        <button
          type="button"
          role="tab"
          className="content-tab"
          key={item.id}
          aria-selected={value === item.id}
          tabIndex={value === item.id ? 0 : -1}
          onClick={() => onChange(item.id)}
          onKeyDown={(event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? items.length - 1
                  : (index + (event.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length;
            const buttons =
              event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('button');
            buttons?.[next]?.focus();
            onChange(items[next].id);
          }}
        >
          {item.label}
          {item.count !== undefined && <span className="tab-count">{item.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function SearchField({
  value,
  onChange,
  placeholder = '搜索当前列表…',
  autoFocus = false,
  ...props
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  'aria-controls'?: string;
  'aria-activedescendant'?: string;
}) {
  return (
    <Input
      {...props}
      className="search-field"
      prefix={<Search size={16} />}
      aria-label={placeholder}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      showClear
      autoFocus={autoFocus}
    />
  );
}

export function EmptyState({
  title = '没有找到匹配的内容',
  icon = <SearchX size={26} strokeWidth={1.5} aria-hidden="true" />,
  description,
  children,
}: {
  title?: string;
  icon?: ReactNode;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon}
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {children}
    </div>
  );
}

export function DetailHeading({ label, onClose }: { label: string; onClose: () => void }) {
  return (
    <div className="detail-heading">
      <span>{label}</span>
      <Button
        className="icon-button"
        theme="borderless"
        type="tertiary"
        icon={<X size={17} />}
        aria-label="关闭详情"
        title="关闭详情"
        onClick={onClose}
      />
    </div>
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div className="form-field">
      <div className="form-label">{label}</div>
      {children}
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}

export function SortButton({ descending, onClick }: { descending: boolean; onClick: () => void }) {
  return (
    <Button
      type="tertiary"
      theme="borderless"
      className="sort-button"
      icon={
        <ArrowDown size={15} style={{ transform: descending ? undefined : 'rotate(180deg)' }} />
      }
      onClick={onClick}
      aria-label={descending ? '更新时间：从新到旧' : '更新时间：从旧到新'}
    >
      {descending ? '最近更新' : '最早更新'}
    </Button>
  );
}
