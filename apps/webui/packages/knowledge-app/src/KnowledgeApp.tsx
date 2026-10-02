import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Upload, ArrowRight, MoreHorizontal, X } from 'lucide-react';
import {
  Button,
  Dropdown,
  Select,
  Modal,
  SearchField,
  EmptyState,
  Toast,
  navigate,
} from '@icarus/ui';
import { kindLabel, type Source, type WikiPage } from './types';
import { type Target, derivedPages } from './evidence';
import KnowledgeReader from './KnowledgeReader';
import KnowledgeQuality from './KnowledgeQuality';
import KnowledgeActions, { type Action } from './KnowledgeActions';
import useKnowledge from './useKnowledge';
import * as api from './openkb';
import { excerpt } from './Reading';
import './knowledge.css';

const EvidenceMap = lazy(() => import('./EvidenceMap'));

function routeTarget(params: URLSearchParams): Target | undefined {
  return params.has('page')
    ? { kind: 'page', id: params.get('page')! }
    : params.has('source')
      ? { kind: 'source', id: params.get('source')! }
      : undefined;
}
export default function KnowledgeApp({ active, hash }: { active: boolean; hash: string }) {
  const params = new URLSearchParams(hash.split('?')[1]);
  const section = hash.split('?')[0].split('/')[2];
  const view = ['pages', 'sources', 'graph', 'quality'].includes(section) ? section : 'sources';
  const target = routeTarget(params);
  const preview = import.meta.env.DEV && params.get('preview') === '1';
  const [previewData, setPreviewData] = useState<typeof import('./preview')>();
  useEffect(() => {
    if (import.meta.env.DEV && preview) void import('./preview').then(setPreviewData);
  }, [preview]);
  const liveData = useKnowledge(active && !preview, params.get('base'), target);
  const data =
    preview && previewData
      ? {
          ...liveData,
          names: [previewData!.previewBase.name],
          name: previewData!.previewBase.name,
          base: previewData!.previewBase,
          graph: previewData!.previewGraph,
          loading: false,
          error: '',
          graphError: '',
          listError: '',
          refresh: () => {},
        }
      : liveData;
  const { base, graph, name } = data;
  const source =
    view === 'sources'
      ? base?.documents.find((item) => item.hash === params.get('source'))
      : undefined;
  const page =
    view === 'pages' ? base?.pages.find((item) => item.path === params.get('page')) : undefined;
  const readingFrom = params.get('from');
  const readerOpen = Boolean(target && (view === 'pages' || view === 'sources'));
  function validReturn(value: string | null): value is string {
    if (!value || !/^#\/knowledge\/(sources|pages|graph|quality)(\?|$)/.test(value)) return false;
    const query = new URLSearchParams(value.split('?')[1]);
    return (
      (!query.has('base') || query.get('base') === name) &&
      (query.get('preview') === '1') === preview
    );
  }
  const origin = validReturn(params.get('origin'))
    ? params.get('origin')!
    : validReturn(readingFrom) && readingFrom.startsWith('#/knowledge/graph')
      ? readingFrom
      : '#/knowledge/' +
        view +
        '?' +
        new URLSearchParams({ base: name, ...(preview ? { preview: '1' } : {}) });
  const surface = readerOpen ? origin.split('?')[0].split('/')[2] : view;
  const [filters, setFilters] = useState<Record<string, { search: string; type: string }>>({});
  const filterKey = JSON.stringify([name, surface]);
  const { search = '', type = 'all' } = filters[filterKey] ?? {};
  const setSearch = (search: string) =>
    setFilters((values) => ({ ...values, [filterKey]: { search, type } }));
  const setType = (type: string) =>
    setFilters((values) => ({ ...values, [filterKey]: { search, type } }));
  const clearFilters = () =>
    setFilters((values) => ({ ...values, [filterKey]: { search: '', type: 'all' } }));
  const [action, setAction] = useState<Action | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [compiling, setCompiling] = useState('');
  const [operationError, setOperationError] = useState('');
  const lastGraph = useRef('');
  const opener = useRef<HTMLElement | null>(null);
  const scrollPositions = useRef(new Map<string, number>());
  if (view === 'graph') lastGraph.current = hash;
  const graphParams = new URLSearchParams(
    (surface === 'graph' ? (readerOpen ? origin : hash) : lastGraph.current).split('?')[1],
  );
  const graphFocus =
    !graphParams.has('base') || graphParams.get('base') === name
      ? routeTarget(graphParams)
      : undefined;

  function go(nextView = view, fields: Record<string, string> = {}, replace = false) {
    scrollPositions.current.set(hash, document.querySelector('.shell-main')?.scrollTop ?? 0);
    const query = new URLSearchParams({
      ...(preview ? { preview: '1' } : {}),
      base: name,
      ...fields,
    });
    navigate('#/knowledge/' + nextView + '?' + query, replace);
  }
  function readFields() {
    if (!readerOpen)
      opener.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return { from: hash, origin: readerOpen ? origin : hash };
  }
  function readPage(item: WikiPage) {
    go('pages', { page: item.path, ...readFields() });
  }
  function readSource(item: Source) {
    go('sources', { source: item.hash, ...readFields() });
  }
  function closeReader() {
    navigate(origin);
  }
  function focus(next: Target, trail: Target[], details = false) {
    go('graph', {
      ...(details ? { details: '1' } : {}),
      [next.kind]: next.id,
      trail: JSON.stringify(trail.slice(-30)),
    });
  }
  useEffect(() => {
    setAction(null);
    setMoreOpen(false);
    setOperationError('');
  }, [name, view]);
  useEffect(() => {
    if (!active) {
      setAction(null);
      setMoreOpen(false);
    }
  }, [active]);
  useEffect(() => {
    if (active && base) {
      const scroll = document.querySelector('.shell-main');
      if (scroll && !readerOpen) scroll.scrollTop = scrollPositions.current.get(hash) ?? 0;
      if (!readerOpen && opener.current?.isConnected) opener.current.focus({ preventScroll: true });
    }
  }, [active, hash, Boolean(base)]);
  async function recompile(item: Source) {
    if (compiling) return;
    setCompiling(item.hash);
    setOperationError('');
    try {
      const result = await api.recompile(name, item);
      data.refresh();
      Toast.info('服务已处理：编译 ' + result.recompiled + ' 份，跳过 ' + result.skipped + ' 份');
    } catch (error) {
      setOperationError(api.errorMessage(error));
    } finally {
      setCompiling('');
    }
  }
  const normalized = search.trim().toLowerCase();
  const documents =
    base?.documents.filter(
      (item) =>
        (type === 'all' || item.display_type === type) &&
        item.name.toLowerCase().includes(normalized),
    ) ?? [];
  const pages =
    base?.pages.filter(
      (item) =>
        (type === 'all' || item.kind === type) &&
        (item.title + ' ' + item.summary).toLowerCase().includes(normalized),
    ) ?? [];
  const invalidBase = data.names !== null && Boolean(name) && !data.names.includes(name);
  const missingReader = Boolean(
    base && !data.loading && (view === 'pages' || view === 'sources') && target && !source && !page,
  );
  return (
    <div className={`page knowledge-page${surface === 'graph' ? ' is-graph' : ''}`}>
      <div
        className="knowledge-background"
        inert={active && readerOpen}
        aria-hidden={active && readerOpen ? true : undefined}
      >
        {surface !== 'graph' && (
          <div className="knowledge-context">
            {data.names?.length ? (
              <Select
                className="base-switch"
                aria-label="选择知识库"
                value={name}
                disabled={Boolean(compiling)}
                onChange={(value) => go(surface, { base: String(value) })}
                optionList={data.names.map((value) => ({ label: value, value }))}
              />
            ) : (
              <span>知识库</span>
            )}
            <Dropdown
              trigger="click"
              position="bottomRight"
              visible={moreOpen}
              onVisibleChange={setMoreOpen}
              render={
                <Dropdown.Menu>
                  <Dropdown.Item
                    disabled={preview || !data.names || Boolean(data.listError)}
                    onClick={() => {
                      setMoreOpen(false);
                      setAction({ kind: 'create' });
                    }}
                  >
                    新建知识库
                  </Dropdown.Item>
                  <Dropdown.Item
                    disabled={preview || data.loading}
                    onClick={() => {
                      setMoreOpen(false);
                      data.refresh();
                    }}
                  >
                    刷新知识库
                  </Dropdown.Item>
                  {import.meta.env.DEV && (
                    <Dropdown.Item
                      onClick={() => {
                        setMoreOpen(false);
                        navigate(preview ? '#/knowledge/sources' : '#/knowledge/sources?preview=1');
                      }}
                    >
                      {preview ? '返回真实知识库' : '浏览示例'}
                    </Dropdown.Item>
                  )}
                </Dropdown.Menu>
              }
            >
              <Button
                type="tertiary"
                theme="borderless"
                className="icon-button"
                icon={<MoreHorizontal size={18} />}
                aria-label="知识库设置"
              />
            </Dropdown>
            {base && graph && (surface === 'sources' || surface === 'pages') && (
              <div className="knowledge-filters">
                <SearchField
                  value={search}
                  onChange={setSearch}
                  placeholder={surface === 'sources' ? '搜索资料名称…' : '搜索标题与摘要…'}
                />
                <Select
                  aria-label="筛选内容类型"
                  value={type}
                  onChange={(value) => setType(String(value))}
                  optionList={
                    surface === 'sources'
                      ? [
                          { value: 'all', label: '全部格式' },
                          ...[...new Set(base.documents.map((item) => item.display_type))].map(
                            (value) => ({ value, label: value }),
                          ),
                        ]
                      : [
                          { value: 'all', label: '全部类型' },
                          ...Object.entries(kindLabel).map(([value, label]) => ({ value, label })),
                        ]
                  }
                />
                {(search || type !== 'all') && (
                  <Button type="tertiary" theme="borderless" onClick={clearFilters}>
                    清除筛选
                  </Button>
                )}
              </div>
            )}
            {surface === 'sources' && (
              <Button
                className="knowledge-import"
                theme="solid"
                icon={<Upload size={16} />}
                disabled={preview || !base || data.loading || Boolean(compiling)}
                onClick={() => setAction({ kind: 'import' })}
              >
                导入资料
              </Button>
            )}
          </div>
        )}
        {(data.listError || data.error || operationError) && (
          <div className="knowledge-error" role="alert">
            <p>{operationError || data.listError || data.error}</p>
            <Button
              onClick={() => {
                setOperationError('');
                if (data.listError) data.refreshNames();
                else data.refresh();
              }}
            >
              重试加载
            </Button>
          </div>
        )}
        {!data.names && !data.listError && <p role="status">正在连接知识服务…</p>}
        {invalidBase && (
          <EmptyState
            title="知识库不存在或不可访问"
            description="此链接的知识库不可用，请选择其他知识库。"
          />
        )}
        {data.names?.length === 0 && !data.listError && (
          <EmptyState title="还没有知识库">
            <Button onClick={() => setAction({ kind: 'create' })}>新建知识库</Button>
          </EmptyState>
        )}
        {!base && data.loading && !invalidBase && data.names?.length ? (
          <p role="status">正在加载知识库…</p>
        ) : null}
        {base && graph && (
          <>
            {data.loading && (
              <p className="field-hint" role="status">
                正在刷新；完成前保留上次读取的内容。
              </p>
            )}
            {(surface === 'pages' || surface === 'sources') && (
              <>
                {(search || type !== 'all') && (
                  <div className="filter-summary">
                    {search && (
                      <button
                        className="filter-chip"
                        aria-label="移除搜索条件"
                        onClick={() => setSearch('')}
                      >
                        {search}
                        <X size={12} />
                      </button>
                    )}
                    {type !== 'all' && (
                      <button
                        className="filter-chip"
                        aria-label="移除类型筛选"
                        onClick={() => setType('all')}
                      >
                        {surface === 'pages' ? kindLabel[type as keyof typeof kindLabel] : type}
                        <X size={12} />
                      </button>
                    )}
                  </div>
                )}
                {surface === 'pages' && (
                  <section aria-label="知识页面列表">
                    {pages.length ? (
                      <div className="knowledge-grid">
                        {pages.map((item) => (
                          <article className="knowledge-card" key={item.path}>
                            <button
                              className="knowledge-card-main"
                              aria-label={'阅读知识：' + item.title}
                              onClick={() => readPage(item)}
                            >
                              <h2>
                                {item.title}
                                <ArrowRight size={15} />
                              </h2>
                              <p>{item.summary || excerpt(item.content) || '打开页面读取正文。'}</p>
                            </button>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <EmptyState
                        title={base.pages.length ? '没有匹配的知识页面' : '还没有知识页面'}
                        description={
                          base.pages.length
                            ? undefined
                            : '导入并编译资料后，生成的知识页面会出现在这里。'
                        }
                      />
                    )}
                  </section>
                )}
                {surface === 'sources' && (
                  <section aria-label="资料列表">
                    {documents.length ? (
                      <div className="knowledge-grid">
                        {documents.map((item) => {
                          const count = derivedPages(item, base, graph).length;
                          return (
                            <article className="knowledge-card source-card" key={item.hash}>
                              <button
                                className="knowledge-card-main"
                                aria-label={'查看资料：' + item.name}
                                onClick={() => readSource(item)}
                              >
                                <h2>
                                  {item.name}
                                  <ArrowRight size={15} />
                                </h2>
                                <p>
                                  {item.pages != null ? item.pages + ' 页 · ' : ''}
                                  {data.graphError ? '派生知识暂不可用' : count + ' 篇派生知识'}
                                </p>
                              </button>
                            </article>
                          );
                        })}
                      </div>
                    ) : (
                      <EmptyState
                        title={base.documents.length ? '没有匹配的资料' : '还没有原始资料'}
                      />
                    )}
                  </section>
                )}
              </>
            )}
            {data.graphError && (
              <p className="field-hint" role="status">
                关联信息暂不可用，仍可阅读和导入资料。
                <Button theme="borderless" onClick={data.refresh}>
                  重新加载关联
                </Button>
              </p>
            )}
            {surface === 'graph' && (
              <>
                {!data.graphError && (
                  <Suspense fallback={<p role="status">正在加载关联画布…</p>}>
                    <EvidenceMap
                      inspect={graphParams.get('details') === '1'}
                      onInspectClose={() =>
                        go(
                          'graph',
                          Object.fromEntries([...graphParams].filter(([key]) => key !== 'details')),
                          true,
                        )
                      }
                      dialogsVisible={active && !readerOpen}
                      key={name}
                      base={base}
                      focus={graphFocus}
                      graphData={graph}
                      onFocus={(target, trail) => focus(target, trail, true)}
                      onReadPage={readPage}
                      onReadSource={readSource}
                    />
                  </Suspense>
                )}
              </>
            )}
            <div hidden={surface !== 'quality'}>
              <KnowledgeQuality
                key={(preview ? 'preview:' : 'live:') + name}
                name={name}
                preview={preview}
                active={active && surface === 'quality'}
              />
            </div>
          </>
        )}
      </div>
      {base && graph && (source || page) && (
        <KnowledgeReader
          key={name + ':' + (source?.hash ?? page?.path)}
          operationError={operationError}
          visible={active}
          onClose={closeReader}
          readOnly={preview}
          relationsUnavailable={Boolean(data.graphError)}
          base={base}
          graph={graph}
          source={source}
          page={page}
          onPage={readPage}
          onSource={readSource}
          onRetryRead={data.retryRead}
          onRetry={recompile}
          compiling={compiling === source?.hash}
          onDelete={(item) => setAction({ kind: 'remove', source: item })}
          onExplore={() => {
            const next: Target = page
              ? { kind: 'page', id: page.path }
              : { kind: 'source', id: source!.hash };
            focus(next, [next], true);
          }}
          onSave={async (path, content) => {
            const result = await api.savePage(name, path, content);
            data.refresh();
            Toast.success(
              result.ghosts_stripped.length
                ? '已保存；服务已移除 ' + result.ghosts_stripped.length + ' 条失效链接'
                : '已保存页面',
            );
          }}
        />
      )}
      {active && readerOpen && !source && !page && (
        <Modal
          className="knowledge-reader-modal"
          title="文档详情"
          visible
          width="min(1120px, calc(100vw - 64px))"
          footer={null}
          onCancel={closeReader}
        >
          {missingReader ? (
            <EmptyState title="内容不存在或已被移除" />
          ) : (
            <p role="status">正在读取文档…</p>
          )}
          {(data.error || data.listError) && <p role="alert">{data.error || data.listError}</p>}
        </Modal>
      )}
      {action && (
        <KnowledgeActions
          key={name + ':' + action.kind}
          action={action}
          base={base}
          names={data.names ?? []}
          onClose={() => setAction(null)}
          onRefresh={data.refresh}
          onCreated={(created) => {
            data.refreshNames();
            go('sources', { base: created });
          }}
        />
      )}
    </div>
  );
}
