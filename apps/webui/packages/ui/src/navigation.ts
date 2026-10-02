import { useEffect, useRef, useState } from 'react';

const guards = new Set<(leave: () => void) => boolean>();
const indexKey = 'icarusNavigationIndex';
function requestNavigation(leave: () => void) {
  for (const guard of guards) if (guard(leave)) return false;
  leave();
  return true;
}

export function navigate(hash: string, replace = false) {
  if (window.location.hash === hash) return;
  requestNavigation(() => {
    const index = window.history.state?.[indexKey] ?? 0;
    const state = { ...window.history.state, [indexKey]: replace ? index : index + 1 };
    if (replace) window.history.replaceState(state, '', hash);
    else window.history.pushState(state, '', hash);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}

export function useHashLocation() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    let currentHash = window.location.hash;
    let currentIndex = window.history.state?.[indexKey] ?? 0;
    window.history.replaceState({ ...window.history.state, [indexKey]: currentIndex }, '');
    const sync = () => {
      const destination = window.location.hash;
      if (destination === currentHash) return;
      // Native hash links create entries without our index; tag them before rollback.
      const nextIndex = window.history.state?.[indexKey] ?? currentIndex + 1;
      window.history.replaceState({ ...window.history.state, [indexKey]: nextIndex }, '');
      const delta = nextIndex - currentIndex;
      for (const guard of guards) {
        if (guard(() => (delta ? window.history.go(delta) : navigate(destination, true)))) {
          if (delta) window.history.go(-delta);
          else
            window.history.replaceState(
              window.history.state,
              '',
              currentHash || window.location.pathname + window.location.search,
            );
          return;
        }
      }
      currentHash = destination;
      currentIndex = nextIndex;
      setHash(destination);
    };
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);
  return hash;
}

export function useUnsavedChanges(dirty: boolean) {
  const [pending, setPending] = useState<(() => void) | null>(null);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  useEffect(() => {
    if (!dirty) {
      setPending(null);
      return;
    }
    function protectNavigation(leave: () => void) {
      if (!dirtyRef.current) return false;
      setPending(() => leave);
      return true;
    }
    function protectReload(event: BeforeUnloadEvent) {
      if (!dirtyRef.current) return;
      event.preventDefault();
      event.returnValue = '';
    }
    guards.add(protectNavigation);
    window.addEventListener('beforeunload', protectReload);
    return () => {
      guards.delete(protectNavigation);
      window.removeEventListener('beforeunload', protectReload);
    };
  }, [dirty]);

  function requestLeave(leave: () => void) {
    if (dirtyRef.current) setPending(() => leave);
    else leave();
  }
  function markSaved() {
    dirtyRef.current = false;
    setPending(null);
  }
  function discardChanges() {
    markSaved();
    pending?.();
  }
  return {
    confirming: Boolean(pending),
    requestLeave,
    keepEditing: () => setPending(null),
    discardChanges,
    markSaved,
  };
}
