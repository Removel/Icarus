import { useEffect, useRef, useState } from 'react';
import { Button, Field, Input, Modal, Select, TextArea, useUnsavedChanges } from '@icarus/ui';
import { memoryContext, type MemoryContext } from './context';
import * as api from './mem0';

export default function MemoryCreate({
  active,
  workspace,
  content = '',
  onClose,
  onCreated,
}: {
  active: boolean;
  workspace: string;
  content?: string;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [text, setText] = useState(content);
  const [scope, setScope] = useState('global');
  const [path, setPath] = useState(workspace);
  const [category, setCategory] = useState('手动记录');
  const [expiration, setExpiration] = useState('');
  const [context, setContext] = useState<MemoryContext>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const working = useRef(false);
  const guard = useUnsavedChanges(
    active &&
      (text !== content ||
        scope !== 'global' ||
        path !== workspace ||
        category !== '手动记录' ||
        Boolean(expiration)),
  );
  useEffect(() => {
    let stale = false;
    setLoading(true);
    setError('');
    memoryContext()
      .then((value) => {
        if (!stale) setContext(value);
      })
      .catch((error) => {
        if (!stale) setError(api.message(error));
      })
      .finally(() => {
        if (!stale) setLoading(false);
      });
    return () => {
      stale = true;
    };
  }, [version]);
  async function save() {
    if (working.current) return;
    if (!text.trim()) {
      setError('请先填写记忆内容。');
      return;
    }
    if (!context) {
      setError('请先连接 Agent，读取记忆配置后再保存。');
      return;
    }
    if (scope === 'workspace' && !path.trim()) {
      setError('请填写对话使用的工作区绝对路径。');
      return;
    }
    if (expiration && expiration < new Date().toLocaleDateString('en-CA')) {
      setError('有效期已过，请选择今天或之后的日期。');
      return;
    }
    working.current = true;
    setBusy(true);
    setError('');
    try {
      // Resolve with the same server-side identity logic used by Agent sessions.
      const target = await memoryContext(scope === 'workspace' ? path.trim() : undefined);
      const id = await api.create({
        memory: text.trim(),
        user_id: target.user_id,
        agent_id: target.agent_id,
        run_id: target.run_id,
        expiration_date: expiration || null,
        metadata: {
          category: category.trim() || '手动记录',
          source: 'webui',
          ...(target.workspace_path ? { workspace_path: target.workspace_path } : {}),
        },
      });
      guard.markSaved();
      onCreated(id);
    } catch (error) {
      setError(api.message(error));
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  return (
    <Modal
      title={guard.confirming ? '放弃未保存的修改？' : '添加记忆'}
      visible={active}
      width={guard.confirming ? 480 : 560}
      onCancel={() => {
        if (!busy) {
          if (guard.confirming) guard.keepEditing();
          else guard.requestLeave(onClose);
        }
      }}
      footer={
        guard.confirming ? (
          <>
            <Button onClick={guard.keepEditing}>继续编辑</Button>
            <Button
              type="danger"
              theme="solid"
              onClick={() => {
                onClose();
                guard.discardChanges();
              }}
            >
              放弃修改
            </Button>
          </>
        ) : (
          <>
            <Button disabled={busy} onClick={() => guard.requestLeave(onClose)}>
              取消
            </Button>
            <Button
              theme="solid"
              loading={busy}
              disabled={busy || loading || !context}
              onClick={save}
            >
              保存记忆
            </Button>
          </>
        )
      }
    >
      {guard.confirming ? (
        <p>记忆尚未保存，放弃后将丢弃本次填写的内容。</p>
      ) : (
        <fieldset
          className="memory-create-fields"
          disabled={busy}
          onKeyDown={(event) => {
            if (
              !event.nativeEvent.isComposing &&
              (event.ctrlKey || event.metaKey) &&
              event.key === 'Enter'
            ) {
              event.preventDefault();
              void save();
            }
          }}
        >
          <Field label="记忆内容">
            <TextArea
              autoFocus
              aria-label="记忆内容"
              value={text}
              onChange={setText}
              autosize={{ minRows: 4, maxRows: 8 }}
              placeholder="写下一条偏好、事实或约定…"
            />
          </Field>
          <Field
            label="作用范围"
            hint="全局记忆可供同一用户与 Agent 在所有工作区检索；工作区记忆仅在指定项目中使用。"
          >
            <Select
              aria-label="记忆作用范围"
              value={scope}
              onChange={(value) => setScope(String(value))}
              optionList={[
                { value: 'global', label: '全局' },
                { value: 'workspace', label: '指定工作区' },
              ]}
            />
          </Field>
          {scope === 'workspace' && (
            <Field label="工作区路径" hint="与对话页连接的服务端工作区路径一致。">
              <Input
                aria-label="记忆工作区路径"
                value={path}
                onChange={setPath}
                placeholder="工作区的绝对路径"
              />
            </Field>
          )}
          <div className="memory-create-identity" role="status">
            {loading ? (
              '正在读取 Agent 记忆配置…'
            ) : context ? (
              <>
                所属用户 <strong>{context.user_id}</strong>
                <span>
                  Agent <strong>{context.agent_id}</strong>
                </span>
              </>
            ) : (
              <Button onClick={() => setVersion((value) => value + 1)}>重新读取配置</Button>
            )}
          </div>
          <details className="form-extras">
            <summary>分类与有效期</summary>
            <div className="form-row">
              <Field label="分类">
                <Input aria-label="记忆分类" value={category} onChange={setCategory} />
              </Field>
              <Field label="有效期" hint="留空为长期有效">
                <Input
                  aria-label="记忆有效期"
                  type="date"
                  value={expiration}
                  onChange={setExpiration}
                />
              </Field>
            </div>
          </details>
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
        </fieldset>
      )}
    </Modal>
  );
}
