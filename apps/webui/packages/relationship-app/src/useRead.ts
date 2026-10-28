import { useEffect, useState } from 'react';
import { request } from './rela';

/** Every result belongs to a URL; cancellation prevents stale people/filters leaking through. */
export default function useRead<T>(url: string | null, revision = 0, initialData?: T) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    url: string | null;
    data?: T;
    error?: Error;
    loading: boolean;
  }>({ url, data: initialData, loading: Boolean(url) });
  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();
    setState((old) => ({ url, data: old.url === url ? old.data : undefined, loading: true }));
    request<T>(url, { signal: controller.signal }).then(
      (data) => {
        if (!controller.signal.aborted) setState({ url, data, loading: false });
      },
      (error: Error) => {
        if (!controller.signal.aborted) setState((old) => ({ ...old, url, error, loading: false }));
      },
    );
    return () => controller.abort();
  }, [url, revision, attempt]);
  return {
    data: state.url === url ? state.data : undefined,
    error: state.url === url ? state.error : undefined,
    loading: Boolean(url) && (state.url !== url || state.loading),
    refresh: () => setAttempt((value) => value + 1),
  };
}
