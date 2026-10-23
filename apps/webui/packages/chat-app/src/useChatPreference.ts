import { useState } from 'react';

export default function useChatPreference<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      return JSON.parse(localStorage.getItem(key) ?? 'null') ?? initial;
    } catch {
      return initial;
    }
  });
  return [
    value,
    (next: T) => {
      setValue(next);
      try {
        localStorage.setItem(key, JSON.stringify(next));
      } catch {
        /* Keep the preference for this page when storage is unavailable. */
      }
    },
  ] as const;
}
