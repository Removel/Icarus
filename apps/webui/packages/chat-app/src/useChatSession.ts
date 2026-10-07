import { useCallback, useEffect, useRef, useState } from 'react';
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

type Submission = {
  id: string;
  text: string;
  workspace: string;
  session: string;
  taskId?: string;
  cancelRequested?: boolean;
};

export default function useChatSession(hash: string) {
  const params = new URLSearchParams(hash.split('?')[1]);
  const workspace = params.get('workspace') ?? '';
  const sessionId = params.get('session') ?? '';
  const [path, setPath] = useState(workspace);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [transcript, setTranscript] = useState(emptyTranscript);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(false);
  const [gateway, setGateway] = useState<Gateway>();
  const receive = useRef<((update: RuntimeUpdate) => void) | undefined>(undefined);
  const selection = useRef({ workspace, sessionId });
  selection.current = { workspace, sessionId };
  const createdStatus = useRef<SessionStatus | undefined>(undefined);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [version, setVersion] = useState(0);
  const [unknown, setUnknown] = useState(false);
  const [queue, setQueue] = useState<Submission[]>([]);
  const [queuePaused, setQueuePaused] = useState(false);
  const client = useRef<Gateway | undefined>(undefined);
  const pending = useRef<Submission | undefined>(undefined);
  const working = useRef(false);
  const listedWorkspace = useRef('');
  const createdSession = useRef<Session | undefined>(undefined);
  const titleRequests = useRef(new Set<string>());
  const guard = useUnsavedChanges(Boolean(draft) || queue.length > 0);
  const requestTitle = useCallback(
    (connection: Gateway, session: string, targetWorkspace: string) => {
      const key = targetWorkspace + '\u0000' + session;
      if (titleRequests.current.has(key)) return;
      titleRequests.current.add(key);
      void connection
        .request<{ title: string | null }>('session.generate_title', {
          workspace_path: targetWorkspace,
          session_id: session,
        })
        .then(({ title }) => {
          if (
            title &&
            client.current === connection &&
            selection.current.workspace === targetWorkspace
          )
            setSessions((items) =>
              items.map((item) => (item.session_id === session ? { ...item, title } : item)),
            );
        })
        .catch(() => {
          // The original message remains a usable title when generation is unavailable.
        })
        .finally(() => titleRequests.current.delete(key));
    },
    [],
  );
  function go(fields: { workspace: string; session?: string }) {
    navigate('#/chat?' + new URLSearchParams(fields));
  }
  useEffect(() => {
    setPath(workspace);
    setDraft('');
    setUnknown(false);
    setQueue([]);
    setQueuePaused(false);
    pending.current = undefined;
  }, [workspace, sessionId]);
  useEffect(() => {
    setConnected(false);
    setError('');
    setGateway(undefined);
    if (listedWorkspace.current !== workspace) {
      setSessions([]);
      listedWorkspace.current = workspace;
      createdSession.current = undefined;
      createdStatus.current = undefined;
    }
    if (!workspace) return;
    let stale = false;
    let reconnect: ReturnType<typeof setTimeout>;
    const connection = new Gateway(
      (update) => {
        if (!stale) receive.current?.(update);
      },
      () => {
        if (stale) return;
        setConnected(false);
        setError('Agent 连接已断开，正在重连并恢复历史。');
        reconnect = setTimeout(() => setVersion((value) => value + 1), 2000);
      },
    );
    client.current = connection;
    async function connect() {
      await connection.ready;
      if (stale) return;
      setGateway(connection);
      setConnected(true);
      const list = await connection.request<{ sessions: Session[] }>('session.list', {
        workspace_path: workspace,
      });
      if (stale) return;
      const created = createdSession.current;
      if (created && !list.sessions.some((row) => row.session_id === created.session_id))
        setSessions([created, ...list.sessions]);
      else {
        createdSession.current = undefined;
        setSessions(list.sessions);
      }
    }
    connect().catch((error) => {
      if (!stale) setError(error instanceof Error ? error.message : '连接失败');
    });
    return () => {
      stale = true;
      clearTimeout(reconnect);
      connection.close();
      if (client.current === connection) client.current = undefined;
    };
  }, [workspace, version]);

  useEffect(() => {
    setTranscript(emptyTranscript());
    receive.current = undefined;
    setLoading(Boolean(workspace && sessionId));
    if (!gateway || client.current !== gateway || !sessionId) return;
    let stale = false;
    let ready = false;
    let key = '';
    const buffered: RuntimeUpdate[] = [];
    receive.current = (update) => {
      if (
        stale ||
        selection.current.sessionId !== sessionId ||
        update.session_id !== sessionId ||
        (key && update.workspace_key !== key)
      )
        return;
      if (!ready) buffered.push(update);
      else setTranscript((previous) => applyUpdate(previous, update, true));
    };
    async function load() {
      const fresh =
        createdStatus.current?.session_id === sessionId ? createdStatus.current : undefined;
      const status =
        fresh ??
        (await gateway!.request<SessionStatus>('session.get', {
          workspace_path: workspace,
          session_id: sessionId,
        }));
      if (stale) return;
      key = status.workspace_key;
      await gateway!.request('session.subscribe', { workspace_key: key, session_id: sessionId });
      if (stale) return;
      let state = emptyTranscript();
      let cursor = 0;
      if (!fresh)
        while (!stale) {
          const history = await gateway!.request<History>('session.get_history', {
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
      if (stale) return;
      createdStatus.current = undefined;
      state.tasks = status.active_task_ids.filter((id) => !state.finished.has(id));
      for (const update of buffered)
        if (update.workspace_key === key) state = applyUpdate(state, update);
      ready = true;
      setTranscript(state);
      setLoading(false);
      setError('');
      if (state.items.some((item) => item.kind === 'user'))
        requestTitle(gateway!, sessionId, workspace);
    }
    load().catch((error) => {
      if (!stale) {
        setLoading(false);
        setError(error instanceof Error ? error.message : '加载失败');
      }
    });
    return () => {
      stale = true;
      receive.current = undefined;
      if (key)
        void gateway
          .request('session.unsubscribe', { workspace_key: key, session_id: sessionId })
          .catch(() => {});
    };
  }, [gateway, workspace, sessionId, version, requestTitle]);

  async function createSession() {
    if (!client.current || working.current) return;
    working.current = true;
    setBusy(true);
    setError('');
    const gateway = client.current;
    try {
      const session = await gateway.request<SessionStatus>('session.create', {
        workspace_path: workspace,
        session_id: crypto.randomUUID(),
        load_runtime: false,
      });
      if (client.current !== gateway) return;
      createdStatus.current = session;
      const fresh = {
        session_id: session.session_id,
        first_user_input: '',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      createdSession.current = fresh;
      setSessions((previous) => [
        fresh,
        ...previous.filter((item) => item.session_id !== session.session_id),
      ]);
      go({ workspace, session: session.session_id });
    } catch (error) {
      setError(error instanceof Error ? error.message : '创建失败');
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  async function deleteSession(id: string) {
    if (!client.current || working.current) return false;
    working.current = true;
    setBusy(true);
    setError('');
    const connection = client.current;
    try {
      const result = await connection.request<{ status: string }>('session.delete', {
        workspace_path: workspace,
        session_id: id,
      });
      if (client.current !== connection) return false;
      if (result.status === 'busy') throw new Error('会话正在执行，请停止后再删除。');
      if (!['discarded', 'not_found'].includes(result.status))
        throw new Error('删除失败，请重试。');
      const remaining = sessions.filter((item) => item.session_id !== id);
      setSessions(remaining);
      if (createdSession.current?.session_id === id) createdSession.current = undefined;
      if (createdStatus.current?.session_id === id) createdStatus.current = undefined;
      if (sessionId === id)
        go({ workspace, ...(remaining[0] ? { session: remaining[0].session_id } : {}) });
      return true;
    } catch (error) {
      setError(error instanceof Error ? error.message : '删除失败');
      return false;
    } finally {
      working.current = false;
      setBusy(false);
    }
  }

  const dispatch = useCallback(
    async (submission: Submission, fromQueue = false) => {
      if (
        !client.current ||
        !connected ||
        loading ||
        !sessionId ||
        working.current ||
        submission.workspace !== workspace ||
        submission.session !== sessionId
      )
        return;
      working.current = true;
      setSubmitting(true);
      setError('');
      pending.current = submission;
      const previousSession = sessions.find((item) => item.session_id === submission.session);
      const optimisticId = `pending:${submission.id}`;
      const nextDraft = fromQueue
        ? draft
        : unknown && draft.startsWith(submission.text + '\n')
          ? draft.slice(submission.text.length + 1)
          : '';
      if (!nextDraft && queue.length <= (fromQueue ? 1 : 0)) guard.markSaved();
      if (fromQueue) setQueue((items) => items.filter((item) => item.id !== submission.id));
      else setDraft(nextDraft);
      setSessions((previous) =>
        previous.map((item) =>
          item.session_id === submission.session
            ? { ...item, first_user_input: item.first_user_input || submission.text }
            : item,
        ),
      );
      setTranscript((previous) => ({
        ...previous,
        items: previous.items.some((item) => item.id === optimisticId)
          ? previous.items
          : [
              ...previous.items,
              {
                id: optimisticId,
                taskId: submission.taskId,
                kind: 'user',
                text: submission.text,
                final: true,
                pending: true,
                label: submission.taskId ? '引导待应用' : undefined,
              },
            ],
      }));
      const gateway = client.current;
      try {
        const result = await gateway.request<{ task_id: string; status?: string }>(
          submission.taskId ? 'session.steer' : 'session.submit',
          {
            workspace_path: submission.workspace,
            session_id: submission.session,
            prompt: submission.text,
            submission_id: submission.id,
            ...(submission.taskId ? { task_id: submission.taskId } : {}),
          },
        );
        if (
          client.current !== gateway ||
          selection.current.workspace !== submission.workspace ||
          selection.current.sessionId !== submission.session
        )
          return;
        if (submission.taskId && result.status !== 'accepted')
          throw new RpcError(
            result.status === 'already_cancelling'
              ? '当前任务正在停止，消息已恢复，请排队发送。'
              : result.status === 'not_running'
                ? '当前任务已结束，消息已恢复，请重新发送。'
                : '引导未被接受，消息已恢复。',
          );
        pending.current = undefined;
        setUnknown(false);
        setTranscript((previous) => ({
          ...previous,
          items: previous.items.map((item) =>
            item.id === optimisticId ? { ...item, taskId: result.task_id } : item,
          ),
          tasks: previous.finished.has(result.task_id)
            ? previous.tasks
            : [...new Set([...previous.tasks, result.task_id])],
        }));
        setSessions((previous) =>
          previous.map((item) =>
            item.session_id === submission.session
              ? {
                  ...item,
                  first_user_input: item.first_user_input || submission.text,
                  updated_at: new Date().toISOString(),
                }
              : item,
          ),
        );
        if (!sessions.find((item) => item.session_id === submission.session)?.title)
          requestTitle(gateway, submission.session, submission.workspace);
        if (submission.cancelRequested && !submission.taskId) {
          try {
            await gateway.request('session.cancel', {
              workspace_path: submission.workspace,
              session_id: submission.session,
              task_id: result.task_id,
            });
          } catch (error) {
            if (
              client.current === gateway &&
              selection.current.sessionId === submission.session &&
              selection.current.workspace === submission.workspace
            )
              setError(error instanceof Error ? error.message : '停止失败');
          }
        }
      } catch (error) {
        if (
          client.current !== gateway ||
          selection.current.workspace !== submission.workspace ||
          selection.current.sessionId !== submission.session
        )
          return;
        const uncertain = !(error instanceof RpcError);
        setUnknown(uncertain);
        setQueuePaused(true);
        if (!uncertain && previousSession && !previousSession.first_user_input)
          setSessions((items) =>
            items.map((item) =>
              item.session_id === submission.session
                ? { ...item, first_user_input: previousSession.first_user_input }
                : item,
            ),
          );
        setDraft((current) => (current ? submission.text + '\n' + current : submission.text));
        setTranscript((previous) => ({
          ...previous,
          items: previous.items.filter((item) => item.id !== optimisticId),
        }));
        setError(
          uncertain
            ? '发送结果尚未确认。重连后请核对历史；重试将复用原提交标识，避免同一进程内重复执行。'
            : error.message,
        );
        if (!uncertain) pending.current = undefined;
      } finally {
        working.current = false;
        setSubmitting(false);
      }
    },
    [
      connected,
      loading,
      sessionId,
      workspace,
      sessions,
      draft,
      unknown,
      guard,
      queue.length,
      requestTitle,
    ],
  );

  useEffect(() => {
    if (
      queue.length &&
      !queuePaused &&
      !unknown &&
      !error &&
      !guard.confirming &&
      connected &&
      !loading &&
      !busy &&
      !submitting &&
      transcript.tasks.length === 0 &&
      !working.current
    )
      void dispatch(queue[0], true);
  }, [
    queue,
    queuePaused,
    unknown,
    error,
    guard.confirming,
    connected,
    loading,
    busy,
    submitting,
    transcript.tasks.length,
    dispatch,
  ]);

  async function send(steer = false) {
    if (!client.current || !connected || loading || busy || !sessionId || !draft.trim()) return;
    if (unknown) {
      if (pending.current && !working.current) await dispatch(pending.current);
      return;
    }
    if (steer && (working.current || !transcript.tasks.length)) return;
    const submission: Submission = {
      id: crypto.randomUUID(),
      text: draft.trim(),
      workspace,
      session: sessionId,
      ...(steer ? { taskId: transcript.tasks[0] } : {}),
    };
    if (!steer && (submitting || transcript.tasks.length || queue.length)) {
      setQueue((items) => [...items, submission]);
      setDraft('');
    } else await dispatch(submission);
  }

  async function cancel() {
    setQueuePaused(true);
    if (submitting && pending.current) {
      if (!pending.current.taskId) {
        pending.current.cancelRequested = true;
        return;
      }
      // Steering already has a task ID; stopping need not wait for its reply.
      if (!client.current) return;
      setBusy(true);
      try {
        await client.current.request('session.cancel', {
          workspace_path: workspace,
          session_id: sessionId,
          task_id: pending.current.taskId,
        });
      } catch (error) {
        setError(error instanceof Error ? error.message : '取消失败');
      } finally {
        setBusy(false);
      }
      return;
    }
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
    loading,
    error,
    draft,
    setDraft,
    busy,
    submitting,
    unknown,
    queue,
    queuePaused,
    resumeQueue: () => setQueuePaused(false),
    removeQueued: (id: string) => setQueue((items) => items.filter((item) => item.id !== id)),
    editQueued: (id: string) => {
      const item = queue.find((item) => item.id === id);
      if (!item) return;
      setQueuePaused(true);
      setQueue((items) => items.filter((item) => item.id !== id));
      setDraft((current) => (current ? current + '\n' + item.text : item.text));
    },
    guard,
    go,
    createSession,
    deleteSession,
    send,
    cancel,
    reconnect: () => setVersion((value) => value + 1),
    clearDraft: () => {
      pending.current = undefined;
      setUnknown(false);
      guard.markSaved();
      setDraft('');
    },
    discardDraft: () => {
      pending.current = undefined;
      setUnknown(false);
      setDraft('');
      setQueue([]);
      setQueuePaused(false);
      guard.discardChanges();
    },
  };
}
