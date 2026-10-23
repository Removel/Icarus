// Adapted from dswui Select @ 3eaf631; keep popups inside the active dialog.
import { useCallback, useId, useLayoutEffect, useRef, useState, type SVGProps } from 'react';
import { createPortal } from 'react-dom';
const IconSelectArrow = (p: SVGProps<SVGSVGElement>) => (
  <svg width="14" height="14" viewBox="0 0 512 512" fill="none" {...p}>
    <path
      d="M256 294.1L383 167c9.4-9.4 24.6-9.4 33.9 0s9.3 24.6 0 34L273 345c-9.1 9.1-23.7 9.3-33.1.7L95 201.1c-4.7-4.7-7-10.9-7-17s2.3-12.3 7-17c9.4-9.4 24.6-9.4 33.9 0L256 294.1z"
      fill="currentColor"
    />
  </svg>
);
const IconSelectCheck = (p: SVGProps<SVGSVGElement>) => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" {...p}>
    <path
      d="M15.0498 3.92579L8.49512 12.3818C8.25774 12.6881 8.04517 12.9645 7.84668 13.1689C7.63957 13.3823 7.38732 13.5841 7.04492 13.6719C6.86373 13.7183 6.6757 13.7346 6.48926 13.7197C6.13666 13.6915 5.8528 13.5355 5.6123 13.3604C5.38201 13.1926 5.12573 12.9567 4.83984 12.6953L1.03125 9.21289L1.96875 8.1875L5.77734 11.6699C6.08684 11.9529 6.27773 12.1249 6.43066 12.2363C6.50183 12.2882 6.54699 12.3135 6.57324 12.3252C6.58525 12.3305 6.59269 12.3322 6.5957 12.333C6.59802 12.3336 6.59961 12.334 6.59961 12.334C6.63317 12.3367 6.66758 12.3335 6.7002 12.3252C6.7002 12.3252 6.70211 12.3251 6.7041 12.3242C6.70698 12.3229 6.71348 12.319 6.72461 12.3115C6.74849 12.2956 6.78843 12.2642 6.84961 12.2012C6.98138 12.0654 7.13957 11.8628 7.39648 11.5313L13.9502 3.07422L15.0498 3.92579Z"
      fill="currentColor"
    />
  </svg>
);

interface Props {
  'aria-label': string;
  value: string;
  optionList: readonly { value: string; label: string }[];
  onChange: (value: string) => void;
  className?: string;
  disabled?: boolean;
}

/** 设置与管理页面共用的选择器，弹出层不受内容滚动区裁切。 */
export function Select({
  'aria-label': label,
  value,
  optionList: options,
  onChange,
  className = '',
  disabled = false,
}: Props) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0, minWidth: 0, maxHeight: 320 });
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = useCallback(() => {
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  }, []);

  useLayoutEffect(() => {
    if (!open || !popup.current || !trigger.current) return;
    const el = popup.current;
    const positionMenu = () => {
      const r = trigger.current!.getBoundingClientRect();
      const height = Math.min(el.scrollHeight, 320, window.innerHeight - 16);
      const width = Math.min(Math.max(r.width, el.offsetWidth), window.innerWidth - 16);
      const below = window.innerHeight - r.bottom - 12;
      const above = r.top - 12;
      const toTop = height > below && above > below;
      const available = Math.max(40, toTop ? above : below);
      setPosition({
        left: Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8)),
        top: toTop ? Math.max(8, r.top - Math.min(height, available) - 4) : r.bottom + 4,
        minWidth: Math.min(r.width, window.innerWidth - 16),
        maxHeight: Math.min(320, available),
      });
    };
    positionMenu();
    (
      el.querySelector<HTMLButtonElement>('[aria-selected="true"]') ??
      el.querySelector<HTMLButtonElement>('button')
    )?.focus({ preventScroll: true });
    const onScroll = (e: Event) => {
      if (!el.contains(e.target as Node)) positionMenu();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopImmediatePropagation();
        close();
      }
    };
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', close);
    };
  }, [open, close]);

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={`icarus-select ${className} ${open ? 'open' : ''}`}
        role="combobox"
        disabled={disabled}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span className="icarus-select-label">
          {options.find((o) => o.value === value)?.label ?? value}
        </span>
        <IconSelectArrow aria-hidden="true" />
      </button>
      {open &&
        createPortal(
          <>
            <div
              className="icarus-select-mask"
              onClick={(e) => {
                e.stopPropagation();
                close();
              }}
            />
            <div
              ref={popup}
              id={id}
              role="listbox"
              aria-label={label}
              className="icarus-select-popup"
              style={position}
              onKeyDown={(e) => {
                const items = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('button')];
                const index = items.indexOf(document.activeElement as HTMLButtonElement);
                if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
                  e.preventDefault();
                  const next =
                    e.key === 'Home'
                      ? 0
                      : e.key === 'End'
                        ? items.length - 1
                        : (index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
                  items[next]?.focus();
                }
                if (e.key === 'Tab') setOpen(false);
              }}
            >
              {options.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  role="option"
                  aria-selected={value === o.value}
                  tabIndex={value === o.value ? 0 : -1}
                  onClick={() => {
                    onChange(o.value);
                    close();
                  }}
                >
                  <span>{o.label}</span>
                  {value === o.value && <IconSelectCheck aria-hidden="true" />}
                </button>
              ))}
            </div>
          </>,
          trigger.current?.closest('dialog, [role="dialog"]') ?? document.body,
        )}
    </>
  );
}
