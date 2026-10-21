import { useEffect, useRef, useState } from 'react';
import { Check, Copy, Pause, Pencil, Play, Trash2 } from 'lucide-react';
import {
  Button,
  Field,
  Input,
  LoadingIndicator,
  Modal,
  Status,
  TextArea,
  Toast,
  useUnsavedChanges,
} from '@icarus/ui';
import { isExpired, memoryDate, type MemoryEntry } from './types';
import { message } from './mem0';
import MemoryHistory from './MemoryHistory';
import { memoryContext, scopeName, validScope, type MemoryContext } from './context';

type Editor = { memory: string; category: string; expiration_date: string };
export type MemoryChanges = Pick<MemoryEntry, 'memory' | 'metadata' | 'expiration_date'>;

export default function MemoryDetail({
  item,
  active,
  loading,
  loadError,
  onClose,
  onAfterClose,
  onSave,
  onChangeStatus,
  onDelete,
  onRecreate,
}: {
  item?: MemoryEntry;
  active: boolean;
  onClose: () => void;
  onAfterClose: () => void;
  loading: boolean;
  loadError: string;
  onSave: (id: string, changes: MemoryChanges) => Promise<void>;
  onChangeStatus: (item: MemoryEntry) => Promise<void>;
  onDelete: (item: MemoryEntry) => Promise<void>;
  onRecreate: (item: MemoryEntry) => void;
}) {
  const [editor, setEditor] = useState<Editor | null>(null);
  const [error, setError] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [context, setContext] = useState<MemoryContext>();
  useEffect(() => {
    if (!active) return;
    let stale = false;
    memoryContext()
      .then((value) => {
        if (!stale) setContext(value);
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [active]);
  const working = useRef(false);
  const editingItem = useRef<MemoryEntry | undefined>(undefined);
  const actionsRef = useRef<HTMLFieldSetElement>(null);
  const shown = editor ? editingItem.current : item;
  const sourceFields = [
    ['source_session_id', '来源会话'],
    ['source_run_id', '来源执行'],
    ['source_workspace_key', '来源工作区标识'],
    ['source_operation_id', '来源操作标识'],
  ].flatMap(([key, label]) => {
    const value = shown?.metadata[key];
    return typeof value === 'string' && value.trim() ? [{ label, value }] : [];
  });
  const original = editingItem.current;
  const dirty = Boolean(
    editor &&
    original &&
    (editor.memory !== original.memory ||
      editor.category !== original.metadata.category ||
      editor.expiration_date !== (original.expiration_date ?? '')),
  );
  const guard = useUnsavedChanges(dirty);
  const discard = guard.confirming;

  useEffect(() => {
    setEditor(null);
    setDeleting(false);
    setError('');
  }, [item?.id, active]);
  useEffect(() => {
    if (!active || !shown || document.activeElement !== document.body) return;
    const dialog = actionsRef.current?.closest('.semi-modal-content');
    const target =
      editor && !discard
        ? dialog?.querySelector<HTMLTextAreaElement>('textarea')
        : actionsRef.current?.querySelector<HTMLButtonElement>('button:not(.memory-delete)');
    target?.focus({ preventScroll: true });
  }, [active, shown?.id, deleting, discard, Boolean(editor)]);
  useEffect(() => {
    if (!active || !shown) return;
    function keyboard(event: KeyboardEvent) {
      if (event.key !== 'Escape' || event.isComposing || event.defaultPrevented) return;
      event.preventDefault();
      cancel();
    }
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, [active, shown?.id, dirty, discard, deleting, onClose]);

  function leave(destination: 'read' | 'close') {
    if (working.current) return;
    guard.requestLeave(() => {
      setEditor(null);
      setError('');
      if (destination === 'close') onClose();
    });
  }
  function cancel() {
    if (working.current) return;
    if (discard) guard.keepEditing();
    else if (deleting) setDeleting(false);
    else leave('close');
  }
  function confirmDiscard() {
    setEditor(null);
    setError('');
    guard.discardChanges();
  }
  function startEdit() {
    if (!shown) return;
    editingItem.current = shown;
    setEditor({
      memory: shown.memory,
      category: shown.metadata.category,
      expiration_date: shown.expiration_date ?? '',
    });
    setError('');
  }
  async function perform(operation: () => Promise<void>) {
    if (working.current || loading || loadError) return;
    working.current = true;
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (error) {
      setError(message(error));
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  async function save() {
    if (!editor || !original) return;
    if (!editor.memory.trim()) {
      setError('请先填写记忆内容。');
      return;
    }
    await perform(async () => {
      if (dirty)
        await onSave(original.id, {
          memory: editor.memory.trim(),
          metadata: { ...original.metadata, category: editor.category.trim() || '手动记录' },
          expiration_date: editor.expiration_date || null,
        });
      guard.markSaved();
      setEditor(null);
      setError('');
    });
  }
  async function copySource() {
    try {
      await navigator.clipboard.writeText(
        sourceFields.map(({ label, value }) => `${label}：${value}`).join('\n'),
      );
      Toast.success('已复制来源信息');
    } catch {
      Toast.error('复制失败，请选中文字手动复制');
    }
  }
  const title = discard
    ? '放弃未保存的修改？'
    : deleting
      ? '删除这条记忆？'
      : editor
        ? '修正记忆'
        : '记忆详情';
  const footer = shown && (
    <fieldset
      disabled={busy || loading || Boolean(loadError)}
      ref={actionsRef}
      className="memory-detail-actions"
    >
      {discard ? (
        <>
          <Button onClick={guard.keepEditing}>继续编辑</Button>
          <Button type="danger" theme="solid" onClick={confirmDiscard}>
            放弃修改
          </Button>
        </>
      ) : deleting ? (
        <>
          <Button onClick={() => setDeleting(false)}>保留记忆</Button>
          <Button
            type="danger"
            theme="solid"
            onClick={() =>
              perform(async () => {
                await onDelete(shown);
                setDeleting(false);
              })
            }
          >
            确认删除
          </Button>
        </>
      ) : editor ? (
        <>
          <span className="memory-save-hint">Ctrl / ⌘ ↵ 保存</span>
          <Button onClick={() => leave('read')}>取消修改</Button>
          <Button theme="solid" icon={<Check size={14} />} onClick={save}>
            保存修改
          </Button>
        </>
      ) : (
        <>
          <Button
            className="memory-delete"
            type="danger"
            theme="borderless"
            icon={<Trash2 size={14} />}
            onClick={() => setDeleting(true)}
          >
            删除记忆
          </Button>
          <Button
            icon={isExpired(shown) ? <Play size={14} /> : <Pause size={14} />}
            onClick={() => perform(() => onChangeStatus(shown))}
          >
            {isExpired(shown) ? '恢复使用' : '暂停使用'}
          </Button>
          <Button theme="solid" icon={<Pencil size={14} />} onClick={startEdit}>
            修正内容
          </Button>
        </>
      )}
    </fieldset>
  );
  return (
    <Modal
      title={title}
      visible={active && Boolean(shown)}
      className="memory-detail-modal"
      width={discard || deleting ? 480 : 640}
      maskClosable
      closeOnEsc={false}
      onCancel={cancel}
      afterClose={onAfterClose}
      footer={footer ?? null}
    >
      {(error || loadError) && (
        <p role="alert" className="form-error">
          {error || loadError}
        </p>
      )}
      {(loading || busy) && <LoadingIndicator label={busy ? '正在保存' : '正在读取详情'} />}
      {shown &&
        (discard ? (
          <p className="danger-copy">修改尚未保存。放弃后将离开编辑，原内容不会改变。</p>
        ) : deleting ? (
          <>
            <p className="dialog-preview">{shown.memory}</p>
            <p className="field-hint">删除后无法恢复。若只是暂时不用，可以选择暂停。</p>
          </>
        ) : editor ? (
          <div
            className="memory-editor"
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return;
              if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                event.preventDefault();
                save();
              }
            }}
          >
            <Field label="记忆内容">
              <TextArea
                autoFocus
                aria-label="修正记忆内容"
                value={editor.memory}
                onChange={(memory) => setEditor({ ...editor, memory })}
                autosize={{ minRows: 5, maxRows: 12 }}
              />
            </Field>
            <div className="form-row">
              <Field label="分类">
                <Input
                  aria-label="修改记忆分类"
                  value={editor.category}
                  onChange={(category) => setEditor({ ...editor, category })}
                />
              </Field>
              <Field label="有效期" hint="留空为长期有效">
                <Input
                  aria-label="修改记忆有效期"
                  type="date"
                  value={editor.expiration_date}
                  onChange={(expiration_date) => setEditor({ ...editor, expiration_date })}
                />
              </Field>
            </div>
          </div>
        ) : (
          <>
            <p className="memory-detail-text">{shown.memory}</p>
            <dl className="memory-detail-properties">
              <div>
                <dt>状态</dt>
                <dd>
                  <Status
                    tone={isExpired(shown) ? 'gray' : validScope(shown.run_id) ? 'green' : 'amber'}
                  >
                    {shown.expiration_date === '1970-01-01'
                      ? '已停用'
                      : isExpired(shown)
                        ? '已过期'
                        : validScope(shown.run_id)
                          ? '生效中'
                          : '范围待修正'}
                  </Status>
                </dd>
              </div>
              <div>
                <dt>分类</dt>
                <dd>{shown.metadata.category}</dd>
              </div>
              <div>
                <dt>所属用户</dt>
                <dd>{shown.user_id}</dd>
              </div>
              <div>
                <dt>Agent</dt>
                <dd>{shown.agent_id}</dd>
              </div>
              <div>
                <dt>作用范围</dt>
                <dd>
                  {scopeName(shown.run_id)}
                  {typeof shown.metadata.workspace_path === 'string' && (
                    <small>{shown.metadata.workspace_path}</small>
                  )}
                </dd>
              </div>
              <div>
                <dt>有效期</dt>
                <dd>
                  {shown.expiration_date === '1970-01-01'
                    ? '已停用'
                    : shown.expiration_date || '长期有效'}
                </dd>
              </div>
              <div>
                <dt>创建时间</dt>
                <dd>{memoryDate(shown.created_at)}</dd>
              </div>
              <div>
                <dt>最近更新</dt>
                <dd>{memoryDate(shown.updated_at)}</dd>
              </div>
              <div>
                <dt>记录来源</dt>
                <dd>
                  {shown.metadata.source === 'webui'
                    ? '手动记录'
                    : shown.metadata.origin === 'explicit'
                      ? 'Agent 记录'
                      : '未提供'}
                </dd>
              </div>
            </dl>
            {sourceFields.length > 0 && (
              <details className="memory-source" key={`${shown.id}:${active}`}>
                <summary>来源信息</summary>
                <dl className="memory-detail-properties memory-source-properties">
                  {sourceFields.map(({ label, value }) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd className="mono">{value}</dd>
                    </div>
                  ))}
                </dl>
                <Button
                  className="memory-source-copy"
                  theme="borderless"
                  type="tertiary"
                  icon={<Copy size={14} />}
                  onClick={copySource}
                >
                  复制来源信息
                </Button>
              </details>
            )}
            {(!validScope(shown.run_id) ||
              (context &&
                (context.user_id !== shown.user_id || context.agent_id !== shown.agent_id))) && (
              <div className="memory-scope-notice" role="status">
                <strong>这条记忆的归属或作用范围无法匹配当前 Agent</strong>
                <p>
                  请按当前 Agent
                  配置重新添加，并选择全局或实际工作区。保存新记忆后，可删除这条旧记录。
                </p>
                <Button disabled={busy || loading} onClick={() => onRecreate(shown)}>
                  按当前配置重新添加
                </Button>
              </div>
            )}
            <MemoryHistory key={shown.id} entries={shown.history} />
          </>
        ))}
    </Modal>
  );
}
