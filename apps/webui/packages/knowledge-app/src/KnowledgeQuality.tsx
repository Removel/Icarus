import { useEffect, useRef, useState } from 'react';
import { ShieldCheck, FileCheck2 } from 'lucide-react';
import { Button, EmptyState, Modal } from '@icarus/ui';
import Reading from './Reading';
import * as api from './openkb';

function reportDate(file: string) {
  const date = /^lint_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/.exec(file);
  return date ? `${date[1]}-${date[2]}-${date[3]} ${date[4]}:${date[5]}:${date[6]}` : file;
}

export default function KnowledgeQuality({
  name,
  preview,
  active,
}: {
  name: string;
  preview: boolean;
  active: boolean;
}) {
  const [reports, setReports] = useState<string[]>([]);
  const [selected, setSelected] = useState<{ name: string; content: string }>();
  const [removing, setRemoving] = useState<string>();
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const working = useRef(false);
  const pages = Math.max(1, Math.ceil(reports.length / 10));
  const current = Math.min(page, pages);
  useEffect(() => {
    if (preview) return;
    const controller = new AbortController();
    setLoading(true);
    api
      .listReports(name, controller.signal)
      .then(setReports)
      .catch((error) => {
        if (!controller.signal.aborted) setError(api.errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [name, preview]);
  async function perform(action: () => Promise<void>) {
    if (preview || working.current) return;
    working.current = true;
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (error) {
      setError(api.errorMessage(error));
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  async function refresh() {
    setReports(await api.listReports(name));
  }
  function run() {
    return perform(async () => {
      const report = await api.lint(name);
      setNotice(
        report.skipped
          ? `本次检查已跳过：${report.reason ?? report.message}`
          : '检查完成，报告已保存到知识库。',
      );
      await refresh();
      setPage(1);
    });
  }
  async function download(file: string, content?: string) {
    const text = content ?? (await api.loadPage(name, 'reports/' + file)).content;
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = file;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="quality-workspace" aria-label="检查报告">
      <div className="quality-start" aria-label="质量检查操作">
        <ShieldCheck size={24} />
        <div>
          <h2>知识库质量检查</h2>
          <p>检查知识内容与引用关系，生成可回看的检查报告。</p>
        </div>
        <Button disabled={preview || busy || loading} onClick={() => perform(refresh)}>
          刷新报告
        </Button>
        <Button theme="solid" disabled={preview || busy || loading} loading={busy} onClick={run}>
          {busy ? '正在处理…' : reports.length ? '重新检查' : '开始检查'}
        </Button>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {loading && <p role="status">正在读取报告…</p>}
      <div className="quality-reports-heading">
        <h3>检查报告</h3>
        <span>{reports.length} 份</span>
      </div>
      {!loading && !error && !reports.length && (
        <div className="quality-empty">
          <EmptyState
            title="还没有检查报告"
            icon={<FileCheck2 size={36} strokeWidth={1.5} aria-hidden="true" />}
            description="点击上方「开始检查」，了解知识库中需要完善的内容。生成的报告会保存在这里，支持查看、导出和删除。"
          />
        </div>
      )}
      <div className="quality-report-list">
        {reports.slice((current - 1) * 10, current * 10).map((file) => (
          <article className="quality-report-row" key={file}>
            <div>
              <strong>检查报告</strong>
              <time>{reportDate(file)}</time>
            </div>
            <div className="quality-report-actions">
              <Button
                disabled={busy}
                onClick={() =>
                  perform(async () =>
                    setSelected({
                      name: file,
                      content: (await api.loadPage(name, 'reports/' + file)).content,
                    }),
                  )
                }
              >
                查看报告
              </Button>
              <Button disabled={busy} onClick={() => perform(() => download(file))}>
                导出
              </Button>
              <Button
                disabled={busy}
                type="danger"
                theme="borderless"
                onClick={() => setRemoving(file)}
              >
                删除报告
              </Button>
            </div>
          </article>
        ))}
      </div>
      {pages > 1 && (
        <nav className="quality-report-pages" aria-label="报告分页">
          <Button disabled={current === 1} onClick={() => setPage(current - 1)}>
            上一页
          </Button>
          <span>
            第 {current} / {pages} 页 · 共 {reports.length} 份
          </span>
          <Button disabled={current === pages} onClick={() => setPage(current + 1)}>
            下一页
          </Button>
        </nav>
      )}
      <Modal
        title="删除这份报告？"
        visible={active && Boolean(removing)}
        onCancel={() => {
          if (!busy) setRemoving(undefined);
        }}
        footer={
          <Button
            disabled={busy}
            type="danger"
            theme="solid"
            onClick={() =>
              perform(async () => {
                if (!removing) return;
                await api.deleteReport(name, removing);
                setRemoving(undefined);
                await refresh();
              })
            }
          >
            确认删除报告
          </Button>
        }
      >
        <p>{removing}</p>
        <p>报告将从知识库永久删除，无法恢复。原始资料和知识页面保持不变。</p>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
      </Modal>
      <Modal
        title="质量检查报告"
        visible={active && Boolean(selected)}
        width={800}
        onCancel={() => setSelected(undefined)}
        footer={
          <Button
            onClick={() => selected && perform(() => download(selected.name, selected.content))}
          >
            导出报告
          </Button>
        }
      >
        {selected && (
          <>
            <p className="field-hint">
              {name} · {selected.name}
            </p>
            <div className="quality-report">
              <Reading content={selected.content} title="质量检查报告" />
            </div>
          </>
        )}
      </Modal>
    </section>
  );
}
