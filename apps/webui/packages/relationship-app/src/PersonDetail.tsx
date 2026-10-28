import { useState, type MouseEvent } from 'react';
import { Button, EmptyState, Input, SearchField, LoadingIndicator, Modal } from '@icarus/ui';
import { Bot, UserRound, Pencil, Plus, NotebookText, Users, X } from 'lucide-react';
import { pathPart, personPath, queryPath, request } from './rela';
import { AliasForm, ConfirmWrite, PersonForm, ProfileFieldForm } from './PersonForms';
import RelationForm from './RelationForm';
import useRead from './useRead';
import { CopyId, Pagination, ReadError } from './ReadState';
import {
  aliasText,
  dateText,
  jsonText,
  kindNames,
  type Membership,
  type Page,
  type Person,
  type ProfileField,
  type Relation,
} from './types';

const sections = [
  ['profile', '稳定档案'],
  ['relations', '关系'],
  ['circles', '圈子'],
  ['identity', '别名与身份核对'],
];

export default function PersonDetail({
  id,
  initialPerson,
  section,
  archived,
  revision,
  onChange,
  onClose,
  onAfterClose,
  onOpenPerson,
  onSection,
}: {
  id: string;
  initialPerson?: Person;
  section: string;
  archived: boolean;
  revision: number;
  onChange: () => void;
  onClose: () => void;
  onAfterClose: () => void;
  onOpenPerson: (id: string, event?: MouseEvent<HTMLAnchorElement>) => void;
  onSection: (section: string) => void;
}) {
  const person = useRead<{ person: Person }>(
    queryPath(personPath(id), { include_archived: archived }),
    revision,
    initialPerson ? { person: initialPerson } : undefined,
  );
  const selected = sections.some(([key]) => key === section) ? section : 'profile';
  const trail =
    window.history.state?.relaDetail === id
      ? (window.history.state?.relaTrail as string[] | undefined)
      : undefined;
  const shown = person.data?.person;
  const [editing, setEditing] = useState(false);
  return (
    <Modal
      visible
      title="人物档案"
      className="rela-detail-modal"
      width={860}
      footer={null}
      closable={false}
      onCancel={onClose}
      afterClose={onAfterClose}
    >
      <div className="rela-detail-top">
        <span>人物档案</span>
        <div>
          {Boolean(trail?.length) && (
            <Button theme="borderless" onClick={() => window.history.back()}>
              返回上一人物
            </Button>
          )}
          <Button
            aria-label="关闭人物档案"
            theme="borderless"
            icon={<X size={18} />}
            onClick={onClose}
          />
        </div>
      </div>
      <ReadError error={person.error} retry={person.refresh} />
      {person.loading && !shown && <LoadingIndicator label="正在加载人物档案" />}
      {shown && (
        <>
          <header className="rela-person-heading" data-kind={shown.kind}>
            <div className="rela-identity-heading">
              <span className="rela-avatar" aria-hidden="true">
                {shown.kind === 'agent' ? (
                  <Bot size={24} strokeWidth={1.5} />
                ) : shown.kind === 'user' ? (
                  <UserRound size={24} strokeWidth={1.5} />
                ) : (
                  Array.from(shown.display_name)[0]
                )}
              </span>
              <div className="rela-identity-copy">
                <div className="rela-name-row">
                  <h2>{shown.display_name}</h2>
                  <div className="rela-badges">
                    <span>{kindNames[shown.kind]}</span>
                    {shown.status === 'archived' && <span className="rela-archived">已归档</span>}
                  </div>
                </div>
                {Boolean(shown.aliases?.length) && (
                  <p className="rela-detail-aliases">
                    别名：{shown.aliases!.map(aliasText).join('、')}
                  </p>
                )}
                <CopyId id={shown.person_id} label="Person ID" />
              </div>
              <Button
                className="rela-edit-person"
                icon={<Pencil size={14} />}
                disabled={person.loading || Boolean(person.error)}
                onClick={() => setEditing(true)}
              >
                编辑基础信息
              </Button>
            </div>
          </header>
          <div className="rela-detail-tabs" role="tablist" aria-label="人物档案分类">
            {sections.map(([key, label]) => (
              <button
                key={key}
                role="tab"
                id={`rela-tab-${key}`}
                aria-controls="rela-person-panel"
                tabIndex={selected === key ? 0 : -1}
                aria-selected={selected === key}
                onClick={() => onSection(key)}
                onKeyDown={(event) => {
                  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                  event.preventDefault();
                  const index = sections.findIndex(([name]) => name === selected);
                  const next =
                    event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? sections.length - 1
                        : (index + (event.key === 'ArrowRight' ? 1 : -1) + sections.length) %
                          sections.length;
                  onSection(sections[next][0]);
                  document.getElementById(`rela-tab-${sections[next][0]}`)?.focus();
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <fieldset
            className="rela-detail-content"
            disabled={person.loading || Boolean(person.error)}
            role="tabpanel"
            id="rela-person-panel"
            aria-labelledby={`rela-tab-${selected}`}
          >
            {selected === 'profile' && (
              <ProfilePanel
                key={id}
                person={shown}
                archived={archived}
                revision={revision}
                onChange={onChange}
              />
            )}
            {selected === 'relations' && (
              <RelationsPanel
                key={id}
                person={shown}
                archived={archived}
                revision={revision}
                onOpenPerson={onOpenPerson}
                onChange={onChange}
              />
            )}
            {selected === 'circles' && (
              <MembershipPanel key={id} person={shown} archived={archived} revision={revision} />
            )}
            {selected === 'identity' && (
              <IdentityPanel
                key={id}
                person={shown}
                archived={archived}
                onOpenPerson={onOpenPerson}
                onChange={onChange}
              />
            )}
          </fieldset>
          {editing && (
            <PersonForm
              person={shown}
              onClose={() => setEditing(false)}
              onSaved={() => {
                setEditing(false);
                onChange();
              }}
            />
          )}
        </>
      )}
    </Modal>
  );
}

function ProfilePanel({
  person,
  archived,
  revision,
  onChange,
}: {
  person: Person;
  archived: boolean;
  revision: number;
  onChange: () => void;
}) {
  const [editor, setEditor] = useState<ProfileField | null | undefined>(undefined);
  const [forgetting, setForgetting] = useState<ProfileField | undefined>(undefined);
  const [query, setQuery] = useState('');
  const [cursors, setCursors] = useState<string[]>(['']);
  const [historyKey, setHistoryKey] = useState('');
  const [historyInput, setHistoryInput] = useState('');
  const [historyCursors, setHistoryCursors] = useState<string[]>(['']);
  const data = useRead<Page<ProfileField>>(
    queryPath(personPath(person.person_id) + '/profile', {
      q: query,
      include_archived: archived,
      limit: 20,
      cursor: cursors.at(-1),
    }),
    revision,
  );
  const history = useRead<Page<ProfileField>>(
    historyKey
      ? queryPath(
          personPath(person.person_id) + '/profile/' + encodeURIComponent(historyKey) + '/history',
          { include_archived: archived, limit: 20, cursor: historyCursors.at(-1) },
        )
      : null,
    revision,
  );
  return (
    <>
      <div className="rela-profile-toolbar">
        <SearchField
          placeholder="搜索档案 key 或内容"
          value={query}
          onChange={(value) => {
            setQuery(value);
            setCursors(['']);
          }}
        />
        {person.status === 'active' && (
          <Button theme="solid" icon={<Plus size={15} />} onClick={() => setEditor(null)}>
            添加档案
          </Button>
        )}
      </div>
      <ReadError
        error={data.error}
        retry={data.refresh}
        reset={cursors.length > 1 ? () => setCursors(['']) : undefined}
      />
      {data.loading && <LoadingIndicator overlay label="正在加载稳定档案" />}
      {!data.loading && !data.error && !data.data?.items.length && (
        <EmptyState
          icon={<NotebookText size={26} strokeWidth={1.5} aria-hidden="true" />}
          title={query ? '没有匹配的档案' : '还没有稳定档案'}
          description={
            query
              ? '试试其他关键词，或清除搜索条件。'
              : person.status === 'active'
                ? '点击“添加档案”，记录人物的偏好、背景或其他稳定信息。'
                : '当前没有可显示的档案记录。'
          }
        />
      )}
      {data.data?.items.map((field) => (
        <article className="rela-profile-field" key={field.attr_key}>
          <div className="rela-field-heading">
            <code>{field.attr_key}</code>
            <span>{field.source}</span>
          </div>
          <div
            className={
              typeof field.attr_value === 'object' && field.attr_value !== null
                ? 'rela-json'
                : 'rela-value'
            }
          >
            {field.status === 'deleted' ? (
              '已忘记'
            ) : (
              <>
                {field.attr_value === null ? <code>null</code> : jsonText(field.attr_value)}
                {field.attr_value === null && <small>（有效空值）</small>}
              </>
            )}
          </div>
          <p className="rela-source">
            记录于 {dateText(field.recorded_at)}
            {field.source_ref && <span>source_ref：{field.source_ref}</span>}
          </p>
          <Button
            theme="borderless"
            size="small"
            aria-label={`查看 ${field.attr_key} 的历史`}
            onClick={() => {
              setHistoryKey(historyKey === field.attr_key ? '' : field.attr_key);
              setHistoryCursors(['']);
            }}
          >
            查看此字段历史
          </Button>
          {person.status === 'active' && (
            <div className="rela-inline-actions">
              <Button theme="borderless" size="small" onClick={() => setEditor(field)}>
                修正此字段
              </Button>
              <Button theme="borderless" size="small" onClick={() => setForgetting(field)}>
                忘记此字段
              </Button>
            </div>
          )}
          {historyKey === field.attr_key && (
            <span className="visually-hidden">下方显示此字段历史</span>
          )}
        </article>
      ))}
      <Pagination
        page={data.data}
        disabled={data.loading}
        previous={cursors.length > 1 ? () => setCursors(cursors.slice(0, -1)) : undefined}
        next={(cursor) => setCursors([...cursors, cursor])}
      />
      <details className="rela-history-lookup">
        <summary>按 key 查看字段历史（含已忘记字段）</summary>
        <form
          className="rela-filters"
          onSubmit={(event) => {
            event.preventDefault();
            setHistoryKey(historyInput.trim());
            setHistoryCursors(['']);
          }}
        >
          <Input
            aria-label="历史字段 key"
            required
            maxLength={128}
            pattern="[a-z][a-z0-9_]*(\.[a-z0-9_]+)*"
            value={historyInput}
            onChange={setHistoryInput}
          />
          <Button htmlType="submit">读取字段历史</Button>
        </form>
      </details>
      {historyKey && (
        <div className="rela-field-history" aria-live="polite">
          <h4>字段历史：{historyKey}</h4>
          <ReadError error={history.error} retry={history.refresh} />
          {history.loading && <LoadingIndicator overlay label="正在加载字段历史" />}
          {!history.loading && !history.error && !history.data?.items.length && (
            <p>此字段暂无历史记录</p>
          )}
          {history.data?.items.map((item) => (
            <div key={item.id}>
              <small>
                {dateText(item.recorded_at)} · {item.source} ·{' '}
                {item.status === 'deleted' ? '已忘记' : '有效记录'}
              </small>
              <pre>{item.status === 'deleted' ? 'deleted' : jsonText(item.attr_value)}</pre>
              {item.source_ref && <p>source_ref：{item.source_ref}</p>}
            </div>
          ))}
          <Pagination
            page={history.data}
            disabled={history.loading}
            previous={
              historyCursors.length > 1
                ? () => setHistoryCursors(historyCursors.slice(0, -1))
                : undefined
            }
            next={(cursor) => setHistoryCursors([...historyCursors, cursor])}
          />
        </div>
      )}
      {editor !== undefined && (
        <ProfileFieldForm
          person={person}
          field={editor || undefined}
          onClose={() => setEditor(undefined)}
          onSaved={() => {
            setEditor(undefined);
            onChange();
          }}
        />
      )}
      {forgetting && (
        <ConfirmWrite
          title="忘记档案字段"
          description={`确认忘记 ${person.display_name} 的 ${forgetting.attr_key}？将追加删除标记，历史记录仍保留。`}
          saveText="确认忘记"
          submit={() =>
            request(
              personPath(person.person_id) +
                '/profile/' +
                pathPart(forgetting.attr_key) +
                '?source=user',
              { method: 'DELETE' },
            )
          }
          onClose={() => setForgetting(undefined)}
          onSaved={() => {
            setHistoryKey(forgetting.attr_key);
            setHistoryInput(forgetting.attr_key);
            setHistoryCursors(['']);
            setForgetting(undefined);
            onChange();
          }}
        />
      )}
    </>
  );
}

function RelationsPanel({
  person,
  archived,
  revision,
  onOpenPerson,
  onChange,
}: {
  person: Person;
  archived: boolean;
  revision: number;
  onOpenPerson: (id: string, event?: MouseEvent<HTMLAnchorElement>) => void;
  onChange: () => void;
}) {
  const [editor, setEditor] = useState<Relation | null | undefined>(undefined);
  const [invalidating, setInvalidating] = useState<Relation | undefined>(undefined);
  const [cursors, setCursors] = useState<string[]>(['']);
  const data = useRead<Page<Relation>>(
    queryPath('/v1/relations', {
      person_id: person.person_id,
      include_incoming: true,
      include_archived: archived,
      status: 'active',
      limit: 20,
      cursor: cursors.at(-1),
    }),
    revision,
  );
  return (
    <>
      <div className="rela-section-heading">
        <h3 className="visually-hidden">生效关系</h3>
        {person.status === 'active' && (
          <Button theme="solid" icon={<Plus size={15} />} onClick={() => setEditor(null)}>
            添加关系
          </Button>
        )}
      </div>
      <ReadError
        error={data.error}
        retry={data.refresh}
        reset={cursors.length > 1 ? () => setCursors(['']) : undefined}
      />
      {data.loading && <LoadingIndicator overlay label="正在加载关系" />}
      {!data.loading && !data.error && !data.data?.items.length && (
        <EmptyState
          title="暂无生效关系"
          description="记录人物之间的称谓和关系，方便理解彼此的联系。"
        />
      )}
      {data.data?.items.map((relation) => {
        const other = relation.other_person;
        const outgoing = relation.from_person_id === person.person_id;
        const valid =
          other &&
          other.person_id === (outgoing ? relation.to_person_id : relation.from_person_id) &&
          relation.relative_direction === (outgoing ? 'outgoing' : 'incoming');
        const link = valid ? (
          <a
            href={`#/people?person=${encodeURIComponent(other.person_id)}`}
            data-archived={other.status === 'archived'}
            aria-label={`查看关联人物：${other.display_name}，${other.person_id}`}
            onClick={(event) => onOpenPerson(other.person_id, event)}
          >
            {other.display_name}
          </a>
        ) : (
          <span>{outgoing ? relation.to_person_id : relation.from_person_id}</span>
        );
        const missing = relation.detail_missing || !relation.detail;
        return (
          <article className="rela-relation" key={relation.relation_id}>
            <div className="rela-relation-sentence">
              {valid && !missing ? (
                <>
                  {link}是{person.display_name}的{relation.detail}
                </>
              ) : outgoing ? (
                <>
                  {person.display_name}是{link}的{relation.forward_detail}
                </>
              ) : (
                <>
                  {link}是{person.display_name}的{relation.forward_detail}
                </>
              )}
            </div>
            <p className="rela-source">
              {relation.direction === 'two_way' ? '双向称谓' : '单向称谓'} · {relation.kind} ·{' '}
              {relation.source}
              {missing && ' · 未提供反向称谓'}
            </p>
            {relation.source_ref && (
              <p className="rela-source">source_ref：{relation.source_ref}</p>
            )}
            {person.status === 'active' && relation.other_person?.status === 'active' && (
              <div className="rela-inline-actions">
                <Button theme="borderless" onClick={() => setEditor(relation)}>
                  修改关系
                </Button>
                <Button theme="borderless" onClick={() => setInvalidating(relation)}>
                  失效关系
                </Button>
              </div>
            )}
          </article>
        );
      })}
      <Pagination
        page={data.data}
        disabled={data.loading}
        previous={cursors.length > 1 ? () => setCursors(cursors.slice(0, -1)) : undefined}
        next={(cursor) => setCursors([...cursors, cursor])}
      />
      {editor !== undefined && (
        <RelationForm
          person={person}
          relation={editor || undefined}
          onClose={() => setEditor(undefined)}
          onSaved={() => {
            setEditor(undefined);
            onChange();
          }}
        />
      )}
      {invalidating && (
        <ConfirmWrite
          title="失效关系"
          description={`确认使这条 ${invalidating.kind} 关系失效？不会删除人物。`}
          saveText="确认失效"
          submit={() =>
            request('/v1/relations/' + pathPart(invalidating.relation_id), { method: 'DELETE' })
          }
          onClose={() => setInvalidating(undefined)}
          onSaved={() => {
            setInvalidating(undefined);
            onChange();
          }}
        />
      )}
    </>
  );
}

function MembershipPanel({
  person,
  archived,
  revision,
}: {
  person: Person;
  archived: boolean;
  revision: number;
}) {
  const [cursors, setCursors] = useState<string[]>(['']);
  const data = useRead<Page<Membership>>(
    queryPath(personPath(person.person_id) + '/circles', {
      include_archived: archived,
      limit: 20,
      cursor: cursors.at(-1),
    }),
    revision,
  );
  return (
    <>
      <h3 className="visually-hidden">所在圈子</h3>
      <ReadError
        error={data.error}
        retry={data.refresh}
        reset={cursors.length > 1 ? () => setCursors(['']) : undefined}
      />
      {data.loading && <LoadingIndicator overlay label="正在加载所属圈子" />}
      {!data.loading && !data.error && !data.data?.items.length && (
        <EmptyState
          icon={<Users size={26} strokeWidth={1.5} aria-hidden="true" />}
          title="未加入圈子"
          description="可在圈子页面添加成员，按家庭、工作或兴趣组织人物。"
        />
      )}
      {data.data?.items.map((item) => (
        <article className="rela-membership" key={item.circle_id}>
          <h4>{item.circle?.name || item.circle_id}</h4>
          <p>
            {item.circle?.kind} · {item.role}
            {item.identity_label && ' · ' + item.identity_label}
            {item.alias && ' · ' + item.alias}
          </p>
          <code>{item.circle_id}</code>
        </article>
      ))}
      <Pagination
        page={data.data}
        disabled={data.loading}
        previous={cursors.length > 1 ? () => setCursors(cursors.slice(0, -1)) : undefined}
        next={(cursor) => setCursors([...cursors, cursor])}
      />
    </>
  );
}

function IdentityPanel({
  person,
  archived,
  onOpenPerson,
  onChange,
}: {
  person: Person;
  archived: boolean;
  onOpenPerson: (id: string, event?: MouseEvent<HTMLAnchorElement>) => void;
  onChange: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<string | undefined>(undefined);
  const [namespace, setNamespace] = useState('');
  const [value, setValue] = useState('');
  const [lookup, setLookup] = useState<string | null>(null);
  const result = useRead<{ person: Person | null }>(lookup);
  return (
    <>
      <div className="rela-section-heading">
        <h3>别名</h3>
        {person.status === 'active' && (
          <Button theme="solid" icon={<Plus size={15} />} onClick={() => setAdding(true)}>
            添加别名
          </Button>
        )}
      </div>
      {person.aliases?.length ? (
        <div className="rela-alias-list">
          {person.aliases.map((alias) => (
            <div key={alias.alias_id}>
              <strong>{aliasText(alias)}</strong>
              <small>
                {alias.namespace} · {alias.value}
              </small>
              <Button
                theme="borderless"
                aria-label={`移除别名：${aliasText(alias)}`}
                onClick={() => setRemoving(alias.alias_id)}
              >
                移除
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState title="暂无别名" />
      )}
      <form
        className="rela-resolver"
        onSubmit={(event) => {
          event.preventDefault();
          setLookup(
            queryPath('/v1/identity/resolve', {
              namespace: namespace.trim(),
              value: value.trim(),
              include_archived: archived,
            }),
          );
          result.refresh();
        }}
      >
        <h3>按精确身份核对</h3>
        <p>输入已知命名空间与身份值，如 qq、wechat、email。姓名请使用人物搜索。</p>
        <label>
          命名空间
          <Input
            aria-label="身份命名空间"
            required
            pattern="[a-z][a-z0-9_]{0,31}"
            value={namespace}
            onChange={setNamespace}
          />
        </label>
        <label>
          身份值
          <Input
            aria-label="精确身份值"
            required
            maxLength={256}
            value={value}
            onChange={setValue}
          />
        </label>
        <Button
          htmlType="submit"
          disabled={
            result.loading || namespace.trim() === 'name' || !namespace.trim() || !value.trim()
          }
        >
          核对身份
        </Button>
        {namespace.trim() === 'name' && <p>姓名不能作为精确身份解析，请使用人物搜索。</p>}
        <ReadError error={result.error} retry={result.refresh} />
        {result.loading && <LoadingIndicator overlay label="正在核对身份" />}
        {result.data &&
          (result.data.person ? (
            <div className="rela-resolve-result">
              <a
                href={`#/people?person=${encodeURIComponent(result.data.person.person_id)}`}
                onClick={(event) => onOpenPerson(result.data!.person!.person_id, event)}
              >
                {result.data.person.display_name} · {result.data.person.person_id}
              </a>
            </div>
          ) : (
            <p role="status">未找到对应人物</p>
          ))}
      </form>
      {adding && (
        <AliasForm
          person={person}
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            onChange();
          }}
        />
      )}
      {removing && (
        <ConfirmWrite
          title="移除别名"
          description="仅移除所选别名，不会删除人物或档案。"
          saveText="确认移除"
          submit={() =>
            request(personPath(person.person_id) + '/aliases/' + pathPart(removing), {
              method: 'DELETE',
            })
          }
          onClose={() => setRemoving(undefined)}
          onSaved={() => {
            setRemoving(undefined);
            onChange();
          }}
        />
      )}
    </>
  );
}
