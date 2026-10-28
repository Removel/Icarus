import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import {
  Button,
  Checkbox,
  EmptyState,
  LoadingIndicator,
  Input,
  InfoTip,
  SearchField,
  Select,
  navigate,
} from '@icarus/ui';
import { RefreshCw, Users } from 'lucide-react';
import { queryPath } from './rela';
import useRead from './useRead';
import PersonCard from './PersonCard';
import PersonDetail from './PersonDetail';
import { PersonForm } from './PersonForms';
import { CircleForm } from './CircleForms';
import CircleDetail from './CircleDetail';
import { Pagination, ReadError } from './ReadState';
import { kindNames, type Page, type Person, type Circle } from './types';
import './relationship.css';

export default function RelationshipApp({
  active,
  hash,
  headerContainer,
}: {
  active: boolean;
  hash: string;
  headerContainer: HTMLElement | null;
}) {
  const params = new URLSearchParams(hash.split('?')[1]);
  const circles = hash.split('?')[0] === '#/people/circles';
  const id = params.get('person');
  const circleId = params.get('circle');
  const query = params.get('q') || '';
  const kind = params.get('kind') || '';
  const archived = params.get('archived') === 'true';
  const cursor = params.get('cursor') || '';
  const listParams = new URLSearchParams(params);
  listParams.delete('person');
  listParams.delete('section');
  listParams.delete('person_archived');
  const listHash =
    (circles ? '#/people/circles' : '#/people') + (listParams.size ? '?' + listParams : '');
  const filterKey = JSON.stringify([circles, query, kind, archived]);
  const [pages, setPages] = useState<Record<string, string[]>>(
    () => window.history.state?.relaPages || {},
  );
  function savePages(cursors: string[], key = filterKey) {
    const next = { ...pages, [key]: cursors };
    setPages(next);
    window.history.replaceState({ ...window.history.state, relaPages: next }, '');
  }
  const [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false);
  const [creatingCircle, setCreatingCircle] = useState(false);
  const savedLists = useRef<Record<string, string>>({
    '#/people': '#/people',
    '#/people/circles': '#/people/circles',
  });
  const baseListParams = new URLSearchParams(listParams);
  baseListParams.delete('circle');
  const baseListHash =
    (circles ? '#/people/circles' : '#/people') + (baseListParams.size ? '?' + baseListParams : '');
  savedLists.current[circles ? '#/people/circles' : '#/people'] = baseListHash;
  const board = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const scroll = useRef<number>(window.history.state?.relaScroll || 0);
  const data = useRead<Page<Person | Circle>>(
    active
      ? queryPath(circles ? '/v1/circles' : '/v1/persons', {
          query,
          kind,
          include_archived: archived,
          cursor,
          limit: 20,
        })
      : null,
    revision,
  );
  const rows = data.data?.items || [];

  function change(changes: Record<string, string>, replace = true) {
    const next = new URLSearchParams(listParams);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!('cursor' in changes)) {
      next.delete('cursor');
      savePages(
        [],
        JSON.stringify([
          circles,
          next.get('q') || '',
          next.get('kind') || '',
          next.get('archived') === 'true',
        ]),
      );
    }
    navigate((circles ? '#/people/circles' : '#/people') + (next.size ? '?' + next : ''), replace);
  }
  function personHref(personId: string) {
    const next = new URLSearchParams(listParams);
    next.set('person', personId);
    next.set('section', 'profile');
    return (circles ? '#/people/circles' : '#/people') + '?' + next;
  }
  function openPerson(personId: string, event?: MouseEvent<HTMLAnchorElement>) {
    if (
      event &&
      (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0)
    )
      return;
    event?.preventDefault();
    if (!id) {
      trigger.current = event?.currentTarget || null;
      scroll.current = board.current?.closest('.management-body')?.scrollTop || 0;
      window.history.replaceState(
        {
          ...window.history.state,
          relaScroll: scroll.current,
          relaTrigger: personId,
          relaTriggerKind: 'person',
        },
        '',
      );
    }
    const state = window.history.state || {};
    const trail = id && state.relaDetail === id ? [...(state.relaTrail || []), hash] : [];
    const origin = id && state.relaDetail === id ? state.relaOrigin : listHash;
    let target = personHref(personId);
    if (
      event?.currentTarget.dataset.archived === 'true' ||
      params.get('person_archived') === 'true'
    )
      target += '&person_archived=true';
    navigate(target);
    window.history.replaceState(
      { ...window.history.state, relaOrigin: origin, relaTrail: trail, relaDetail: personId },
      '',
    );
  }
  function closePerson() {
    const state = window.history.state;
    if (state?.relaDetail === id && state.relaOrigin?.startsWith('#/people'))
      window.history.go(-((state.relaTrail?.length || 0) + 1));
    else navigate(listHash, true);
  }
  function restore() {
    const container = board.current?.closest('.management-body');
    if (container) container.scrollTop = scroll.current;
    if (trigger.current?.isConnected) trigger.current.focus({ preventScroll: true });
    else {
      const restored = document.querySelector<HTMLElement>(
        `[data-${window.history.state?.relaTriggerKind === 'circle' ? 'circle' : 'person'}-id="${CSS.escape(window.history.state?.relaTrigger || '')}"]`,
      );
      restored?.focus({ preventScroll: true });
    }
  }
  useEffect(() => {
    if (active && !id && !circleId) {
      const frame = requestAnimationFrame(restore);
      return () => cancelAnimationFrame(frame);
    }
  }, [active, id, circleId]);
  const stack = pages[filterKey] || [];
  const section = params.get('section') || 'profile';
  return (
    <>
      {headerContainer &&
        createPortal(
          <nav className="rela-header-tabs" aria-label="人物分类">
            {[
              ['#/people', '人物'],
              ['#/people/circles', '圈子'],
            ].map(([target, label]) => (
              <a
                key={target}
                href={target}
                aria-current={(circles ? label === '圈子' : label === '人物') ? 'page' : undefined}
                onClick={(event) => {
                  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                  event.preventDefault();
                  navigate(savedLists.current[target]);
                }}
              >
                {label}
              </a>
            ))}
          </nav>,
          headerContainer,
        )}
      {headerContainer &&
        createPortal(
          <div className="rela-header-actions">
            <Button onClick={() => (circles ? setCreatingCircle(true) : setCreating(true))}>
              {circles ? '新建圈子' : '添加人物'}
            </Button>
          </div>,
          headerContainer,
        )}
      <section className="page relationship-page" ref={board} tabIndex={-1} aria-label="人物管理">
        <h1 className="visually-hidden">人物</h1>
        <div className="rela-filters">
          <SearchField
            placeholder={circles ? '搜索圈子名称' : '搜索姓名或别名'}
            value={query}
            onChange={(value) => change({ q: value })}
          />
          {!circles && (
            <Select
              aria-label="人物类型"
              value={kind}
              optionList={[
                { value: '', label: '全部类型' },
                ...Object.entries(kindNames).map(([value, label]) => ({ value, label })),
              ]}
              onChange={(value) => change({ kind: value })}
            />
          )}
          {circles && (
            <Input
              aria-label="筛选圈子类型"
              placeholder="圈子类型"
              value={kind}
              onChange={(value) => change({ kind: value })}
            />
          )}
          <Checkbox
            checked={archived}
            onChange={(event) => change({ archived: event.target.checked ? 'true' : '' })}
          >
            包含已归档
          </Checkbox>
          <Button
            theme="borderless"
            icon={<RefreshCw size={16} />}
            aria-label={circles ? '刷新圈子' : '刷新人物'}
            onClick={data.refresh}
            disabled={data.loading}
          />
        </div>
        <ReadError
          error={data.error}
          retry={data.refresh}
          reset={cursor ? () => change({ cursor: '' }) : undefined}
        />
        <div className="rela-caption">
          <h2>{circles ? '圈子' : '认识的人'}</h2>
          <InfoTip
            label={circles ? '圈子说明' : '人物辨认说明'}
            content={circles ? '按圈子组织人物' : '通过姓名、别名与 Person ID 辨认人物'}
          />
          <small>{data.data ? `本页 ${rows.length} ${circles ? '个圈子' : '人'}` : ''}</small>
        </div>
        {data.loading && (
          <LoadingIndicator
            label={cursor ? '正在加载下一页' : circles ? '正在加载圈子' : '正在加载人物'}
            overlay={Boolean(data.data)}
          />
        )}
        {!data.loading && !data.error && !rows.length && (
          <EmptyState
            title={
              query || kind
                ? circles
                  ? '没有匹配的圈子'
                  : '没有匹配的人物'
                : circles
                  ? '还没有圈子'
                  : '还没有人物'
            }
            description={
              query || kind || cursor
                ? '试试调整搜索或筛选条件。'
                : circles
                  ? '点击右上角“新建圈子”，按家庭、同事或兴趣组织人物。'
                  : '点击右上角“添加人物”，记录你认识的人。'
            }
          />
        )}
        {!circles && (
          <div className="people-grid">
            {(rows as Person[]).map((person) => (
              <PersonCard
                key={person.person_id}
                person={person}
                ids={(rows as Person[]).map((p) => p.person_id)}
                href={personHref(person.person_id)}
                onOpen={(event) => openPerson(person.person_id, event)}
              />
            ))}
          </div>
        )}
        {circles && (
          <div className="rela-circle-list">
            {(rows as Circle[]).map((circle) => (
              <a
                className="rela-circle-row"
                data-circle-id={circle.circle_id}
                key={circle.circle_id}
                href={
                  baseListHash +
                  (baseListHash.includes('?') ? '&' : '?') +
                  'circle=' +
                  encodeURIComponent(circle.circle_id)
                }
                onClick={(event) => {
                  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                  event.preventDefault();
                  trigger.current = event.currentTarget;
                  scroll.current = board.current?.closest('.management-body')?.scrollTop || 0;
                  window.history.replaceState(
                    {
                      ...window.history.state,
                      relaScroll: scroll.current,
                      relaTrigger: circle.circle_id,
                      relaTriggerKind: 'circle',
                    },
                    '',
                  );
                  navigate(event.currentTarget.getAttribute('href')!);
                  window.history.replaceState(
                    { ...window.history.state, relaCircleId: circle.circle_id },
                    '',
                  );
                }}
              >
                <span className="person-card-avatar" aria-hidden="true">
                  <Users size={20} strokeWidth={1.5} />
                </span>
                <div className="person-card-copy">
                  <div className="person-card-heading">
                    <h3 className="person-card-name">{circle.name}</h3>
                    <span className="person-card-kind">
                      <span>{circle.kind}</span>
                      {circle.status === 'archived' && (
                        <span className="rela-archived">已归档</span>
                      )}
                    </span>
                  </div>
                  {circle.owner_person_id && (
                    <p className="rela-circle-owner">负责人：{circle.owner_person_id}</p>
                  )}
                  <span className="person-card-id" aria-label="Circle ID">
                    <small>ID</small>
                    <code>{circle.circle_id}</code>
                  </span>
                </div>
              </a>
            ))}
          </div>
        )}
        <Pagination
          page={data.data}
          disabled={data.loading}
          previous={
            stack.length
              ? () => {
                  const previous = stack.at(-1)!;
                  savePages(stack.slice(0, -1));
                  change({ cursor: previous });
                }
              : undefined
          }
          next={(next) => {
            savePages([...stack, cursor]);
            change({ cursor: next });
            board.current?.closest('.management-body')?.scrollTo({ top: 0 });
          }}
        />
      </section>
      {active && id && (
        <PersonDetail
          key={id}
          id={id}
          initialPerson={
            circles ? undefined : (rows as Person[]).find((row) => row.person_id === id)
          }
          section={section}
          archived={archived || params.get('person_archived') === 'true'}
          revision={revision}
          onChange={() => setRevision((value) => value + 1)}
          onClose={closePerson}
          onAfterClose={restore}
          onOpenPerson={openPerson}
          onSection={(nextSection) => {
            const next = new URLSearchParams(params);
            next.set('section', nextSection);
            navigate(hash.split('?')[0] + '?' + next, true);
          }}
        />
      )}
      {active && circleId && !id && (
        <CircleDetail
          key={circleId}
          id={circleId}
          initialCircle={(rows as Circle[]).find((row) => row.circle_id === circleId)}
          revision={revision}
          onChange={() => setRevision((value) => value + 1)}
          onOpenPerson={openPerson}
          onClose={() => {
            if (window.history.state?.relaCircleId === circleId) window.history.back();
            else navigate(baseListHash, true);
          }}
        />
      )}
      {active && creatingCircle && (
        <CircleForm
          onClose={() => setCreatingCircle(false)}
          onSaved={() => {
            setCreatingCircle(false);
            setRevision((value) => value + 1);
          }}
          onCreated={(circle) => {
            navigate(
              baseListHash +
                (baseListHash.includes('?') ? '&' : '?') +
                'circle=' +
                encodeURIComponent(circle.circle_id),
            );
            window.history.replaceState(
              { ...window.history.state, relaCircleId: circle.circle_id },
              '',
            );
          }}
        />
      )}
      {active && creating && (
        <PersonForm
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            setRevision((value) => value + 1);
          }}
          onCreated={(person) => openPerson(person.person_id)}
        />
      )}
    </>
  );
}
