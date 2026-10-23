// Adapted from dswui Select @ 3eaf631; keep popups inside the native modal.
import { useCallback, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { IconSelectCheck, IconSelectArrow } from './Icons';

interface Props {
  label: string;
  value: string;
  options: readonly { value: string; label: string }[];
  onChange: (value: string) => void;
  className?: string;
}

/** 设置与输入框共用的选择器，弹出层不受设置内容区裁切。 */
export function Select({ label, value, options, onChange, className = '' }: Props) {
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
        className={`setting-select ${className} ${open ? 'open' : ''}`}
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
        <span>{options.find((o) => o.value === value)?.label ?? value}</span>
        <IconSelectArrow />
      </button>
      {open &&
        createPortal(
          <>
            <div
              className="popup-menu-mask"
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
              className="popup-menu select-menu"
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
                  {value === o.value && <IconSelectCheck />}
                </button>
              ))}
            </div>
          </>,
          trigger.current?.closest('dialog') ?? document.body,
        )}
    </>
  );
}
