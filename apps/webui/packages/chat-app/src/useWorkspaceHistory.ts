import { useCallback, useEffect, useState } from 'react';

const key = 'icarus.chat.workspaceHistory';
const limit = 5;

function readHistory(): string[] {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(key) ?? '[]');
    if (!Array.isArray(stored)) return [];
    return [
      ...new Set(
        stored
          .filter((path): path is string => typeof path === 'string')
          .map((path) => path.trim())
          .filter(Boolean),
      ),
    ].slice(0, limit);
  } catch {
    return [];
  }
}

export default function useWorkspaceHistory() {
  const [history, setHistory] = useState(readHistory);
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(history));
    } catch {
      /* Keep history for this page when storage is unavailable. */
    }
  }, [history]);
  const remember = useCallback((path: string) => {
    const value = path.trim();
    if (value)
      setHistory((previous) =>
        [value, ...previous.filter((item) => item !== value)].slice(0, limit),
      );
  }, []);
  return { history, remember };
}
