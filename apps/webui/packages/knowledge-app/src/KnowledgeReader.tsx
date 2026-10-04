import { useState } from 'react';
import { ArrowRight, BookOpen, FileText, Pencil, Check, Link2, MoreHorizontal } from 'lucide-react';
import {
  Button,
  Dropdown,
  TextArea,
  Status,
  EmptyState,
  Modal,
  useUnsavedChanges,
} from '@icarus/ui';
import { kindLabel, type KnowledgeBase, type Source, type WikiPage } from './types';
import Reading from './Reading';
import { derivedPages, resolveDeclaredSource, type GraphData } from './evidence';
import { errorMessage } from './openkb';

type Props = {
  operationError?: string;
  visible: boolean;
  onClose: () => void;
  readOnly?: boolean;
  relationsUnavailable?: boolean;
  base: KnowledgeBase;
  graph: GraphData;
  page?: WikiPage;
  source?: Source;
  onPage: (page: WikiPage) => void;
  onSource: (source: Source) => void;
  onSave: (path: string, content: string) => Promise<void>;
  onRetry: (source: Source) => void;
  onRetryRead: () => void;
  onDelete: (source: Source) => void;
  onExplore: () => void;
  compiling: boolean;
};
export default function KnowledgeReader({
  operationError,
  visible,
  onClose,
  readOnly = false,
  relationsUnavailable = false,
  base,
  graph,
  page,
  source,
  onPage,
  onSource,
  onSave,
  onRetry,
  onRetryRead,
  onDelete,
  onExplore,
  compiling,
}: Props) {
  const [confirmRecompile, setConfirmRecompile] = useState(false);
  const [operationsOpen, setOperationsOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [original, setOriginal] = useState('');
  const guard = useUnsavedChanges(editing && draft !== original);
  const item = page ?? source!;
  const derived = source ? derivedPages(source, base, graph) : [];
  const related = derived.map((item) => item.page);
  async function save() {
    if (!page || saving) return;
    if (!draft.trim()) {
      setError('页面内容不能为空。');
      return;
    }
    if (draft === original) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError('');
    try {
      await onSave(page.path, draft);
      setEditing(false);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }
  function cancel() {
    if (!saving) guard.requestLeave(() => setEditing(false));
  }
  function openPage(entry: WikiPage) {
    onPage(entry);
  }
  function openSource(entry: Source) {
    onSource(entry);
  }
  function resolveLink(target: string) {
    const normalized = target.replace(/^wiki\//, '').replace(/\.md$/, '');
    const entry = base.pages.find((entry) => entry.path === normalized);
    if (entry) return () => openPage(entry);
    const match = resolveDeclaredSource(target, graph, base.documents);
    const document =
      match.target.kind === 'source' && !match.status
        ? base.documents.find((entry) => entry.hash === match.target.id)
        : undefined;
    return document ? () => openSource(document) : undefined;
  }
  return (
    <Modal
      className="knowledge-reader-modal"
      title={page ? '知识页面详情' : '资料详情'}
      visible={visible}
      width="min(1120px, calc(100vw - 64px))"
      footer={null}
      closeOnEsc={!confirmRecompile && !guard.confirming && !operationsOpen}
      onCancel={() => {
        if (!saving) guard.requestLeave(onClose);
      }}
    >
      <section className="reader-workspace" aria-label={page ? '知识页面详情' : '资料详情'}>
        {operationError && (
          <p className="form-error" role="alert">
            {operationError}
          </p>
        )}
        <div className="reader-layout">
          <article className="reading-paper">
            <header className="reading-header">
              {page && <div className="reading-eyebrow">{kindLabel[page.kind]}</div>}
              <h1>{page?.title ?? source?.name}</h1>
              <div className="reading-actions">
                <Button
                  disabled={saving || relationsUnavailable}
                  type="tertiary"
                  theme="borderless"
                  icon={<Link2 size={14} />}
                  onClick={onExplore}
                >
                  查看关联
                </Button>
                <span className="toolbar-spacer" />
                {page
                  ? !editing && (
                      <Button
                        type="tertiary"
                        theme="borderless"
                        disabled={readOnly || page.readState !== 'ready'}
                        icon={<Pencil size={14} />}
                        onClick={() => {
                          setDraft(page.content);
                          setOriginal(page.content);
                          setError('');
                          setEditing(true);
                        }}
                      >
                        编辑页面
                      </Button>
                    )
                  : source && (
                      <>
                        <Dropdown
                          trigger="click"
                          position="bottomRight"
                          visible={operationsOpen}
                          onVisibleChange={setOperationsOpen}
                          render={
                            <Dropdown.Menu>
                              <Dropdown.Item
                                disabled={readOnly || compiling}
                                onClick={() => {
                                  setOperationsOpen(false);
                                  setConfirmRecompile(true);
                                }}
                              >
                                重新生成知识
                              </Dropdown.Item>
                              <Dropdown.Item
                                disabled={readOnly || compiling}
                                onClick={() => {
                                  setOperationsOpen(false);
                                  onDelete(source);
                                }}
                              >
                                移除资料
                              </Dropdown.Item>
                            </Dropdown.Menu>
                          }
                        >
                          <Button
                            type="tertiary"
                            theme="borderless"
                            icon={<MoreHorizontal size={16} />}
                            aria-label="资料操作"
                            loading={compiling}
                          />
                        </Dropdown>
                      </>
                    )}
              </div>
            </header>
            {editing ? (
              <div
                className="page-editor"
                onKeyDown={(event) => {
                  if (event.nativeEvent.isComposing) return;
                  if (event.key === 'Escape') {
                    event.stopPropagation();
                    cancel();
                  }
                  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                    event.preventDefault();
                    void save();
                  }
                }}
              >
                <p className="field-hint">
                  此处修改正文；列表名称与摘要由资料生成。重新生成知识可能覆盖本次修改。
                </p>
                <TextArea
                  aria-label="知识页面正文"
                  value={draft}
                  onChange={setDraft}
                  disabled={saving}
                  autosize={{ minRows: 14, maxRows: 32 }}
                  autoFocus
                />
                {error && (
                  <p className="form-error" role="alert">
                    {error}
                  </p>
                )}
                <div className="page-editor-footer">
                  <span>Markdown · Ctrl ↵ 保存</span>
                  <Button disabled={saving} onClick={cancel}>
                    取消编辑
                  </Button>
                  <Button theme="solid" loading={saving} icon={<Check size={14} />} onClick={save}>
                    保存页面
                  </Button>
                </div>
              </div>
            ) : item.readState === 'loading' || item.readState === 'unloaded' ? (
              <p role="status">正在读取正文…</p>
            ) : item.readState === 'failed' ? (
              <div role="alert">
                <p>{item.error}</p>
                <Button onClick={onRetryRead}>重新读取</Button>
              </div>
            ) : item.content.trim() ? (
              <Reading
                content={item.content}
                title={page?.title ?? source?.name}
                resolveLink={resolveLink}
                showOutline
              />
            ) : (
              <EmptyState title="正文当前不可用" description="资料存在，但服务尚未提供可读正文。" />
            )}
            {!editing && (
              <section className="reading-references" aria-label={page ? '声明来源' : '派生知识'}>
                <h3>
                  {page ? '声明来源' : '派生知识'}
                  <span>
                    {relationsUnavailable ? '—' : page ? page.sources.length : related.length}
                  </span>
                </h3>
                {relationsUnavailable ? (
                  <p className="field-hint">来源信息暂不可用，请刷新后重试。</p>
                ) : page ? (
                  page.sources.length ? (
                    [...new Set(page.sources)].map((name) => {
                      const match = resolveDeclaredSource(name, graph, base.documents);
                      const sourcePage =
                        match.target.kind === 'page'
                          ? base.pages.find((item) => item.path === match.target.id)
                          : undefined;
                      const document =
                        match.target.kind === 'source'
                          ? base.documents.find((item) => item.hash === match.target.id)
                          : undefined;
                      return sourcePage ? (
                        <button
                          className="reference-link"
                          key={name}
                          onClick={() => onPage(sourcePage)}
                        >
                          <BookOpen size={16} />
                          <span>
                            {sourcePage.title}
                            <small>来源摘要</small>
                          </span>
                          <ArrowRight size={15} />
                        </button>
                      ) : document ? (
                        <button
                          className="reference-link"
                          key={name}
                          onClick={() => onSource(document)}
                        >
                          <FileText size={16} />
                          <span>
                            {document.name}
                            <small>原始资料</small>
                          </span>
                          <ArrowRight size={15} />
                        </button>
                      ) : (
                        <div className="reference-missing" key={name}>
                          {name}
                          <Status tone="amber">
                            {match.status === 'ambiguous' ? '来源待确认' : '来源不可用'}
                          </Status>
                        </div>
                      );
                    })
                  ) : (
                    <p className="field-hint">此页面未声明来源。</p>
                  )
                ) : related.length ? (
                  related.map((entry) => (
                    <button
                      className="reference-link"
                      key={entry.path}
                      onClick={() => onPage(entry)}
                    >
                      <BookOpen size={16} />
                      <span>
                        {entry.title}
                        <small>
                          {kindLabel[entry.kind]} ·{' '}
                          {derived.find((item) => item.page.path === entry.path)?.direct
                            ? '直接引用资料'
                            : '经来源链派生'}
                        </small>
                      </span>
                      <ArrowRight size={15} />
                    </button>
                  ))
                ) : (
                  <p className="field-hint">暂无已确认的派生知识</p>
                )}
              </section>
            )}
          </article>
        </div>
        <Modal
          title="重新生成知识？"
          visible={confirmRecompile}
          width={520}
          onCancel={() => setConfirmRecompile(false)}
          cancelText="保留现有知识"
          okText="确认重新生成"
          okButtonProps={{ type: 'warning' }}
          onOk={() => {
            setConfirmRecompile(false);
            if (source) onRetry(source);
          }}
        >
          <p>
            将重新处理「{source?.name}
            」并调用模型生成知识。摘要和相关页面可能被改写，已保存的人工修改也可能被覆盖，目前不提供历史版本恢复。
          </p>
          <p className="field-hint">
            {relationsUnavailable
              ? '当前无法获取关联范围。'
              : '当前可追溯的知识有 ' + related.length + ' 篇。'}
            实际生成范围由资料内容决定。
          </p>
          {related.length > 0 && (
            <ul className="regenerate-pages">
              {related.map((item) => (
                <li key={item.path}>{item.title}</li>
              ))}
            </ul>
          )}
        </Modal>
        <Modal
          title="放弃未保存的修改？"
          visible={guard.confirming}
          width={480}
          onCancel={guard.keepEditing}
          cancelText="继续编辑"
          okText="放弃修改"
          okButtonProps={{ disabled: saving, type: 'danger' }}
          onOk={() => {
            setEditing(false);
            guard.discardChanges();
          }}
        >
          <p>修改尚未保存，放弃后将离开编辑，已保存的内容不会改变。</p>
        </Modal>
      </section>
    </Modal>
  );
}
