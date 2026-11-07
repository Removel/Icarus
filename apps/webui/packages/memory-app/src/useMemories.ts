import { useCallback, useEffect, useRef, useState } from 'react';
import type { MemoryEntry } from './types';
import * as api from './mem0';
import { transitionLayout } from '@icarus/ui';

export default function useMemories(active: boolean, selectedId: string | null, query: string) {
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [listVersion, setListVersion] = useState(0);
  const listing = useRef(false);
  const refreshList = useCallback(() => {
    if (listing.current) return;
    listing.current = true;
    setListVersion((value) => value + 1);
  }, []);
  const [detail, setDetail] = useState<MemoryEntry>();
  const [detailError, setDetailError] = useState('');
  const [detailLoading, setDetailLoading] = useState(false);
  const [missingId, setMissingId] = useState('');
  const displayedQuery = useRef('');
  const [page, setPage] = useState<Omit<api.MemoryPage, 'results'>>({
    page: 1,
    page_size: 0,
    total: 0,
    counts: { all: 0, active: 0, expired: 0 },
    categories: [],
    users: [],
    scopes: [],
    truncated: false,
  });
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    listing.current = true;
    setLoading(true);
    setError('');
    api
      .list(query, controller.signal)
      .then(({ results, ...page }) => {
        if (controller.signal.aborted) return;
        const previous = new URLSearchParams(displayedQuery.current);
        const next = new URLSearchParams(query);
        const update = () => {
          if (controller.signal.aborted) return;
          displayedQuery.current = query;
          setMemories(results);
          setPage(page);
          setLoading(false);
        };
        transitionLayout(
          update,
          Boolean(displayedQuery.current) && previous.get('page') !== next.get('page'),
        );
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(api.message(error));
          setLoading(false);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) listing.current = false;
      });
    return () => {
      controller.abort();
      listing.current = false;
    };
  }, [active, version, listVersion, query]);
  useEffect(() => {
    if (!active) return;
    let timer: ReturnType<typeof setInterval> | undefined;
    const resume = () => {
      clearInterval(timer);
      if (document.visibilityState !== 'visible') return;
      refreshList();
      timer = setInterval(refreshList, 15000);
    };
    if (document.visibilityState === 'visible') timer = setInterval(refreshList, 15000);
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    return () => {
      clearInterval(timer);
      window.removeEventListener('focus', resume);
      document.removeEventListener('visibilitychange', resume);
    };
  }, [active, refreshList]);
  useEffect(() => {
    setDetail(undefined);
    setDetailError('');
    setMissingId('');
    if (!active || !selectedId) {
      setDetailLoading(false);
      return;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    Promise.allSettled([
      api.get(selectedId, controller.signal),
      api.history(selectedId, controller.signal),
    ]).then(([item, history]) => {
      if (controller.signal.aborted) return;
      if (item.status === 'fulfilled') {
        if (item.value)
          setDetail({
            ...item.value,
            history: history.status === 'fulfilled' ? history.value : [],
          });
        else setMissingId(selectedId);
      } else setDetailError(api.message(item.reason));
      if (history.status === 'rejected') setDetailError(api.message(history.reason));
      setDetailLoading(false);
    });
    return () => controller.abort();
  }, [active, selectedId, version]);
  return {
    memories,
    ...page,
    loading,
    error,
    detailError,
    detailLoading,
    missingId,
    refreshList,
    selected: detail?.id === selectedId ? detail : memories.find((item) => item.id === selectedId),
    refresh: () => {
      // Consumers must wait for the refreshed list before restoring focus.
      setLoading(true);
      setVersion((value) => value + 1);
    },
    upsert: (item: MemoryEntry) => {
      // A deep-linked detail can belong to another page; keep the current page bounded.
      setMemories((rows) => rows.map((row) => (row.id === item.id ? item : row)));
      setDetail(item);
    },
    removed: (id: string) => {
      setMemories((rows) => rows.filter((row) => row.id !== id));
      setDetail(undefined);
    },
  };
}
