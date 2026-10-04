import { useEffect, useState } from 'react';
import type { MemoryEntry } from './types';
import * as api from './mem0';

export default function useMemories(active: boolean, selectedId: string | null) {
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [detail, setDetail] = useState<MemoryEntry>();
  const [detailError, setDetailError] = useState('');
  const [detailLoading, setDetailLoading] = useState(false);
  const [missingId, setMissingId] = useState('');
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    api
      .list(controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) setMemories(rows);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(api.message(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [active, version]);
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
    loading,
    error,
    detailError,
    detailLoading,
    missingId,
    selected: detail?.id === selectedId ? detail : memories.find((item) => item.id === selectedId),
    refresh: () => setVersion((value) => value + 1),
    upsert: (item: MemoryEntry) => {
      setMemories((rows) =>
        rows.some((row) => row.id === item.id)
          ? rows.map((row) => (row.id === item.id ? item : row))
          : [item, ...rows],
      );
      setDetail(item);
    },
    removed: (id: string) => {
      setMemories((rows) => rows.filter((row) => row.id !== id));
      setDetail(undefined);
    },
  };
}
