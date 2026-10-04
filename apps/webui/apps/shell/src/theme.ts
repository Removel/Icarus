import { useEffect, useState } from 'react';

export type Theme = 'light' | 'dark' | 'system';
const key = 'icarus.theme';
const validTheme = (value: string | null): Theme =>
  value === 'light' || value === 'dark' ? value : 'system';

function readTheme() {
  try {
    return validTheme(localStorage.getItem(key));
  } catch {
    return 'system' as const;
  }
}
function applyTheme(theme: Theme) {
  const dark =
    theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
  document.body.setAttribute('theme-mode', dark ? 'dark' : 'light');
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', dark ? '#0c0c0e' : '#f7f7f8');
}

export function initializeTheme() {
  applyTheme(readTheme());
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const update = () => applyTheme(theme);
    const sync = (event: StorageEvent) => {
      if (event.key === key || event.key === null) setTheme(readTheme());
    };
    update();
    media.addEventListener('change', update);
    window.addEventListener('storage', sync);
    return () => {
      media.removeEventListener('change', update);
      window.removeEventListener('storage', sync);
    };
  }, [theme]);
  return [
    theme,
    (value: Theme) => {
      applyTheme(value);
      setTheme(value);
      try {
        localStorage.setItem(key, value);
      } catch {
        /* Session preference still works without storage. */
      }
    },
  ] as const;
}
