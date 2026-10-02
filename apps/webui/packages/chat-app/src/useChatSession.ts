import { useEffect, useRef, useState } from 'react';
import { navigate, useUnsavedChanges } from '@icarus/ui';
import {
  Gateway,
  RpcError,
  type History,
  type Session,
  type SessionStatus,
  type RuntimeUpdate,
} from './gateway';
import { applyUpdate, emptyTranscript } from './updates';

export default function useChatSession(hash: string) {
  const params = new URLSearchParams(hash.split('?')[1]);
  const workspace = params.get('workspace') ?? '';
  const sessionId = params.get('session') ?? '';
  const [path, setPath] = useState(workspace);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [transcript, setTranscript] = useState(emptyTranscript);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const [unknown, setUnknown] = useState(false);
  const client = useRef<Gateway | undefined>(undefined);
  const pending = useRef<
    { id: string; text: string; workspace: string; session: string } | undefined
  >(undefined);
  const working = useRef(false);
  const guard = useUnsavedChanges(Boolean(draft));
  function go(fields: { workspace: string; session?: string }) {
    navigate('#/chat?' + new URLSearchParams(fields));
  }
  useEffect(() => {
    setPath(workspace);
    setDraft('');
    setUnknown(false);
    pending.current = undefined;
  }, [workspace, sessionId]);
  useEffect(() => {
    setConnected(false);
    setError('');
    setTranscript(emptyTranscript());
    setSessions([]);
    if (!workspace) return;
    let stale = false;
    let ready = false;
    let key = '';
    const buffered: RuntimeUpdate[] = [];
    let reconnect: ReturnType<typeof setTimeout>;
    const gateway = new Gateway(
      (update) => {
        if (stale || update.session_id !== sessionId || (key && update.workspace_key !== key))
          return;
        if (!ready) buffered.push(update);
        else setTranscript((previous) => applyUpdate(previous, update));
      },
      () => {
        if (stale) return;
        setConnected(false);
        setError('Agent 连接已断开，正在重连并恢复历史。');
        reconnect = setTimeout(() => setVersion((value) => value + 1), 2000);
      },
    );
    client.current = gateway;
    async function connect() {
      await gateway.ready;
      const list = await gateway.request<{ sessions: Session[] }>('session.list', {
        workspace_path: workspace,
      });
      if (stale) return;
      setSessions(list.sessions);
      let state = emptyTranscript();
      if (sessionId) {
        const status = await gateway.request<SessionStatus>('session.get', {
          workspace_path: workspace,
          session_id: sessionId,
        });
        key = status.workspace_key;
        await gateway.request('session.subscribe', { workspace_key: key, session_id: sessionId });
        let cursor = 0;
        while (true) {
          const history = await gateway.request<History>('session.get_history', {
            workspace_path: workspace,
            session_id: sessionId,
            after_sequence: cursor,
            limit: 500,
          });
          for (const update of history.records) state = applyUpdate(state, update);
          if (!history.has_more) break;
          if (history.next_after_sequence <= cursor)
            throw new Error('会话历史游标未推进，请检查 Gateway。');
          cursor = history.next_after_sequence;
        }
        state.tasks = status.active_task_ids.filter((id) => !state.finished.has(id));
        for (const update of buffered)
          if (update.workspace_key === key) state = applyUpdate(state, update);
      }
      if (!stale) {
        ready = true;
        setTranscript(state);
        setConnected(true);
        setError('');
      }
    }
    connect().catch((error) => {
      if (!stale) setError(error instanceof Error ? error.message : '连接失败');
    });
    return () => {
      stale = true;
      clearTimeout(reconnect);
      gateway.close();
      if (client.current === gateway) client.current = undefined;
    };
  }, [workspace, sessionId, version]);

  async function createSession() {
    if (!client.current || working.current) return;
    working.current = true;
    setBusy(true);
    setError('');
    try {
      const session = await client.current.request<SessionStatus>('session.create', {
        workspace_path: workspace,
        session_id: crypto.randomUUID(),
      });
      go({ workspace, session: session.session_id });
    } catch (error) {
      setError(error instanceof Error ? error.message : '创建失败');
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  async function send() {
    if (
      !client.current ||
      !connected ||
      !sessionId ||
      !draft.trim() ||
      working.current ||
      (transcript.tasks.length > 0 && !unknown)
    )
      return;
    working.current = true;
    setBusy(true);
    setError('');
    const submission =
      unknown && pending.current
        ? pending.current
        : { id: crypto.randomUUID(), text: draft.trim(), workspace, session: sessionId };
    pending.current = submission;
    const gateway = client.current;
    try {
      const result = await gateway.request<{ task_id: string }>('session.submit', {
        workspace_path: submission.workspace,
        session_id: submission.session,
        prompt: submission.text,
        submission_id: submission.id,
      });
      if (client.current !== gateway) return;
      guard.markSaved();
      setDraft('');
      pending.current = undefined;
      setUnknown(false);
      setTranscript((previous) =>
        previous.finished.has(result.task_id)
          ? previous
          : { ...previous, tasks: [...new Set([...previous.tasks, result.task_id])] },
      );
    } catch (error) {
      if (client.current !== gateway) return;
      const uncertain = !(error instanceof RpcError);
      setUnknown(uncertain);
      setError(
        uncertain
          ? '发送结果尚未确认。重连后请核对历史；重试将复用原提交标识，避免同一进程内重复执行。'
          : error.message,
      );
      if (!uncertain) pending.current = undefined;
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  async function cancel() {
    if (!client.current || working.current) return;
    working.current = true;
    setBusy(true);
    try {
      for (const task of transcript.tasks)
        await client.current.request('session.cancel', {
          workspace_path: workspace,
          session_id: sessionId,
          task_id: task,
        });
    } catch (error) {
      setError(error instanceof Error ? error.message : '取消失败');
    } finally {
      working.current = false;
      setBusy(false);
    }
  }

  return {
    workspace,
    sessionId,
    path,
    setPath,
    sessions,
    transcript,
    connected,
    error,
    draft,
    setDraft,
    busy,
    unknown,
    guard,
    go,
    createSession,
    send,
    cancel,
    reconnect: () => setVersion((value) => value + 1),
    clearDraft: () => {
      pending.current = undefined;
      setUnknown(false);
      guard.markSaved();
      setDraft('');
    },
  };
}
