import { useEffect, useState } from 'react';
import type { KnowledgeBase } from './types';
import type { GraphData, Target } from './evidence';
import * as api from './openkb';

type Snapshot = {
  name: string;
  base?: KnowledgeBase;
  graph?: GraphData;
  graphError?: string;
  error: string;
  loading: boolean;
  version: number;
};
export default function useKnowledge(active: boolean, requested: string | null, target?: Target) {
  const [names, setNames] = useState<string[] | null>(null);
  const [listError, setListError] = useState('');
  const [listVersion, setListVersion] = useState(0);
  const [version, setVersion] = useState(0);
  const [readVersion, setReadVersion] = useState(0);
  const [snapshot, setSnapshot] = useState<Snapshot>({
    name: '',
    error: '',
    loading: false,
    version: 0,
  });
  const name = requested ?? names?.[0] ?? '';
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setListError('');
    api
      .listBases(controller.signal)
      .then(setNames)
      .catch((error) => {
        if (!controller.signal.aborted) setListError(api.errorMessage(error));
      });
    return () => controller.abort();
  }, [active, listVersion]);
  useEffect(() => {
    if (!active || !name || !names?.includes(name)) return;
    const controller = new AbortController();
    setSnapshot((previous) => ({
      ...(previous.name === name ? previous : { name }),
      loading: true,
      error: '',
      version: previous.version,
    }));
    api
      .loadBase(name, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted)
          setSnapshot({ name, ...result, loading: false, error: '', version: version + 1 });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setSnapshot((previous) => ({
            ...previous,
            loading: false,
            error: api.errorMessage(error),
          }));
      });
    return () => controller.abort();
  }, [active, name, names, version]);
  const current = snapshot.name === name ? snapshot : undefined;
  useEffect(() => {
    if (!active || !target || !current?.base) return;
    const controller = new AbortController();
    const { kind, id } = target;
    const item =
      kind === 'page'
        ? current.base.pages.find((page) => page.path === id)
        : current.base.documents.find((source) => source.hash === id);
    if (!item || item.readState === 'ready') return;
    function update(patch: object) {
      setSnapshot((previous) => {
        if (controller.signal.aborted || previous.name !== name || !previous.base) return previous;
        const base = previous.base;
        return {
          ...previous,
          base: {
            ...base,
            ...(kind === 'page'
              ? {
                  pages: base.pages.map((page) =>
                    page.path === id ? { ...page, ...patch } : page,
                  ),
                }
              : {
                  documents: base.documents.map((source) =>
                    source.hash === id ? { ...source, ...patch } : source,
                  ),
                }),
          },
        };
      });
    }
    update({ readState: 'loading', error: undefined });
    const request =
      kind === 'page'
        ? api.loadPage(name, id, controller.signal)
        : api
            .loadSource(name, id, controller.signal)
            .then((result) => ({ content: result.content, docName: result.doc_name }));
    request
      .then((result) => update({ ...result, readState: 'ready' }))
      .catch((error) => {
        if (!controller.signal.aborted)
          update({ readState: 'failed', error: api.errorMessage(error) });
      });
    return () => controller.abort();
  }, [active, name, target?.kind, target?.id, current?.version, readVersion]);
  return {
    names,
    name,
    base: current?.base,
    graph: current?.graph,
    loading: current?.loading ?? Boolean(name),
    error: current?.error ?? '',
    graphError: current?.graphError ?? '',
    listError,
    refresh: () => setVersion((value) => value + 1),
    retryRead: () => setReadVersion((value) => value + 1),
    refreshNames: () => setListVersion((value) => value + 1),
  };
}
