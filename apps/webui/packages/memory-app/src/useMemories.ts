import { useEffect, useRef, useState } from 'react';
import type { MemoryEntry } from './types';
import * as api from './mem0';
import { transitionLayout } from '@icarus/ui';

export default function useMemories(active: boolean, selectedId: string | null, query: string) {
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
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
      });
    return () => controller.abort();
  }, [active, version, query]);
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
    selected: detail?.id === selectedId ? detail : memories.find((item) => item.id === selectedId),
    refresh: () => setVersion((value) => value + 1),
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
