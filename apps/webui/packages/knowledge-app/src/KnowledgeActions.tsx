import { useEffect, useRef, useState } from 'react';
import { Upload, X, FileText } from 'lucide-react';
import { Button, Modal, Input, Field, Toast } from '@icarus/ui';
import type { KnowledgeBase, Source } from './types';
import * as api from './openkb';

export type Action = { kind: 'create' | 'import' } | { kind: 'remove'; source: Source };
export default function KnowledgeActions({
  action,
  base,
  names,
  onClose,
  onRefresh,
  onCreated,
}: {
  action: Action;
  base?: KnowledgeBase;
  names: string[];
  onClose: () => void;
  onRefresh: () => void;
  onCreated: (name: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [name, setName] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [importResult, setImportResult] = useState<api.ImportResult | null>(null);
  const [preview, setPreview] = useState<api.RemoveResult | null>(null);
  const [version, setVersion] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const working = useRef(false);
  useEffect(() => {
    if (!base || action.kind !== 'remove') return;
    const controller = new AbortController();
    setBusy(true);
    setError('');
    const request = api
      .removeSource(base.name, action.source, true, controller.signal)
      .then((result) => {
        if (result.status !== 'dry_run') throw new Error('未取得有效移除预览，不能执行移除。');
        if (!controller.signal.aborted) setPreview(result);
      });
    request
      .catch((error) => {
        if (!controller.signal.aborted) setError(api.errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [action, base?.name, version]);

  function chooseFiles(incoming: FileList | File[]) {
    if (busy) return;
    const list = Array.from(incoming);
    if (list.some((file) => !/\.(pdf|md|txt|docx)$/i.test(file.name))) {
      setError('支持 PDF、Markdown、TXT 和 DOCX 文件。');
      return;
    }
    const next = [...files, ...list].filter(
      (file, index, all) =>
        all.findIndex((item) => item.name === file.name && item.size === file.size) === index,
    );
    if (
      next.some((file) => file.size > 100 * 1024 * 1024) ||
      next.reduce((sum, file) => sum + file.size, 0) > 500 * 1024 * 1024
    ) {
      setError('单个文件不超过 100 MB，单次合计不超过 500 MB。');
      return;
    }
    setFiles(next);
    setError('');
    setImportResult(null);
  }
  async function submit() {
    if (working.current || busy) return;
    setError('');
    if (
      action.kind === 'create' &&
      (!name.trim() || /[\\/]/.test(name) || name.trim().startsWith('.'))
    ) {
      setError('名称不能为空，不能以点开头或包含路径分隔符。');
      return;
    }
    if (action.kind === 'create' && names.includes(name.trim())) {
      setError('这个知识库名称已存在。');
      return;
    }
    if (action.kind === 'import' && !files.length) {
      setError('请先选择文件。');
      return;
    }
    if (action.kind === 'remove' && !preview) return;
    working.current = true;
    setBusy(true);
    try {
      if (action.kind === 'create') {
        const result = await api.createBase(name.trim());
        onCreated(result.kb);
        onClose();
        Toast.success(result.created ? '已创建知识库' : '知识库已存在');
      } else if (action.kind === 'import' && base) {
        const result = await api.importFiles(base.name, files);
        setImportResult(result);
        onRefresh();
        const failed = new Set(
          result.files.filter((item) => item.status === 'failed').map((item) => item.original_name),
        );
        setFiles((items) => items.filter((file) => failed.has(file.name)));
        if (result.failed_count) setError('部分文件未导入，请查看逐项结果。');
      } else if (action.kind === 'remove' && base) {
        const result = await api.removeSource(base.name, action.source, false);
        onRefresh();
        if (result.status !== 'removed' || result.pageindex_error) {
          setPreview(null);
          setError('移除未全部完成，已刷新列表。请重新获取影响预览后再处理。');
        } else {
          onClose();
          Toast.success('已按服务结果移除资料');
        }
      }
    } catch (error) {
      setError(api.errorMessage(error));
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  const title =
    action.kind === 'create'
      ? '新建知识库'
      : action.kind === 'import'
        ? '导入资料'
        : '移除资料影响预览';
  const confirm =
    action.kind === 'create' ? '创建知识库' : action.kind === 'import' ? '开始导入' : '确认移除';
  const pageActions =
    preview?.actions.flatMap((item) => {
      const path = item.target
        .replace(/\\/g, '/')
        .replace(/^wiki\//, '')
        .split(/\s{2,}/)[0]
        .replace(/\.md$/, '');
      const page = base?.pages.find((page) => page.path === path);
      return /^(summaries|concepts|entities)\//.test(path) &&
        ['DELETE', 'MODIFY'].includes(item.tag.toUpperCase())
        ? [{ ...item, title: page?.title ?? path.split('/').at(-1) }]
        : [];
    }) ?? [];
  return (
    <Modal
      title={title}
      visible
      onCancel={() => {
        if (!working.current) onClose();
      }}
      maskClosable={!working.current}
      width={action.kind === 'import' ? 600 : action.kind === 'create' ? 440 : 560}
      footer={
        <>
          <Button onClick={onClose} disabled={working.current}>
            {action.kind === 'remove' ? '保留资料' : '关闭'}
          </Button>
          <Button
            theme="solid"
            type={action.kind === 'remove' ? 'danger' : 'primary'}
            loading={busy}
            disabled={
              busy ||
              (action.kind === 'remove' && !preview) ||
              (action.kind === 'import' && !files.length)
            }
            onClick={submit}
          >
            {confirm}
          </Button>
        </>
      }
    >
      {action.kind === 'create' && (
        <Field label="知识库名称">
          <Input
            autoFocus
            aria-label="知识库名称"
            placeholder="例如：项目资料"
            value={name}
            disabled={busy}
            onChange={setName}
          />
        </Field>
      )}
      {action.kind === 'import' && (
        <>
          <p className="import-destination">
            导入到 <strong>{base?.name}</strong>
          </p>
          <input
            ref={input}
            type="file"
            multiple
            accept=".pdf,.md,.txt,.docx"
            aria-label="选择资料文件"
            hidden
            disabled={busy}
            onChange={(event) => {
              if (event.target.files) chooseFiles(event.target.files);
              event.target.value = '';
            }}
          />
          <button
            className="upload-zone"
            disabled={busy}
            onClick={() => input.current?.click()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              chooseFiles(event.dataTransfer.files);
            }}
          >
            <Upload size={24} />
            <strong>选择文件，或拖到这里</strong>
            <span>PDF、Markdown、TXT、DOCX</span>
            <small>单个不超过 100 MB · 合计不超过 500 MB</small>
          </button>
          {files.length > 0 && (
            <div className="upload-file-list">
              <p>待导入 · {files.length} 份资料</p>
              {files.map((file, index) => (
                <div className="upload-file" key={file.name + ':' + file.size}>
                  <FileText size={16} />
                  <span>{file.name}</span>
                  <small>
                    {file.size >= 1024 * 1024
                      ? (file.size / (1024 * 1024)).toFixed(1) + ' MB'
                      : Math.max(1, Math.ceil(file.size / 1024)) + ' KB'}
                  </small>
                  <Button
                    className="icon-button"
                    disabled={busy}
                    theme="borderless"
                    aria-label={'移除待导入文件 ' + file.name}
                    icon={<X size={14} />}
                    onClick={() => setFiles((items) => items.filter((_, i) => i !== index))}
                  />
                </div>
              ))}
            </div>
          )}
          {busy && <p role="status">正在解析资料并生成知识，请等待处理结果。</p>}
          {importResult && (
            <div className="operation-report" role="status">
              <p>
                已完成 {importResult.added_count} · 已跳过 {importResult.skipped_count} · 失败{' '}
                {importResult.failed_count}
              </p>
              {importResult.files.map((file, index) => (
                <div key={index}>
                  <p>
                    {file.original_name} ·{' '}
                    {(
                      {
                        added: '解析与知识生成完成',
                        skipped: '已跳过',
                        failed: '导入失败',
                      } as Record<string, string>
                    )[file.status] ?? '请查看处理详情'}
                  </p>
                  {file.message && (
                    <details>
                      <summary>处理详情</summary>
                      <p>{file.message}</p>
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
      {action.kind === 'remove' && (
        <>
          <p className="dialog-preview">{action.source.name}</p>
          {busy && !preview && <p role="status">正在读取服务影响预览…</p>}
          {preview && (
            <>
              <p className="field-hint">
                移除这份资料将删除{' '}
                {pageActions.filter((item) => item.tag.toUpperCase() === 'DELETE').length}{' '}
                个知识页面，更新{' '}
                {pageActions.filter((item) => item.tag.toUpperCase() === 'MODIFY').length}{' '}
                个知识页面的来源。确认后执行，服务会重新计算影响。
              </p>
              {pageActions.length > 0 && (
                <ul className="operation-report">
                  {pageActions.map((item, index) => (
                    <li key={index}>
                      {item.tag.toUpperCase() === 'DELETE' ? '删除页面' : '更新来源'} · {item.title}
                    </li>
                  ))}
                </ul>
              )}
              <details className="operation-report">
                <summary>服务操作详情 · {preview.actions.length} 项</summary>
                <ul>
                  {preview.actions.map((item, index) => (
                    <li key={index}>
                      <strong>{item.tag}</strong> · {item.target}
                    </li>
                  ))}
                </ul>
              </details>
            </>
          )}
        </>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {error && action.kind === 'remove' && !preview && (
        <Button disabled={busy} onClick={() => setVersion((value) => value + 1)}>
          重新获取
        </Button>
      )}
    </Modal>
  );
}
