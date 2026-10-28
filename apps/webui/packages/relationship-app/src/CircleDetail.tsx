import { useEffect, useState, type MouseEvent } from 'react';
import { Button, Checkbox, EmptyState, LoadingIndicator, Modal } from '@icarus/ui';
import { X, Users, Pencil, Plus } from 'lucide-react';
import { CircleForm, MemberForm } from './CircleForms';
import { ConfirmWrite } from './PersonForms';
import { CopyId, Pagination, ReadError } from './ReadState';
import { pathPart, queryPath, request } from './rela';
import useRead from './useRead';
import { kindNames, type Circle, type Membership, type Page } from './types';

export default function CircleDetail({
  id,
  initialCircle,
  revision,
  onClose,
  onChange,
  onOpenPerson,
}: {
  id: string;
  initialCircle?: Circle;
  revision: number;
  onClose: () => void;
  onChange: () => void;
  onOpenPerson: (id: string, event?: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ circle?: Circle; loading: boolean; error?: Error }>({
    circle: initialCircle,
    loading: true,
  });
  const [editing, setEditing] = useState(false);
  // REST has no single-circle GET. Resolve against the entire cursor sequence, not the first page.
  useEffect(() => {
    const controller = new AbortController();
    setState((old) => ({ circle: old.circle, loading: true }));
    async function resolve() {
      let cursor: string | undefined;
      const seen = new Set<string>();
      do {
        const page = await request<Page<Circle>>(
          queryPath('/v1/circles', { include_archived: true, limit: 200, cursor }),
          { signal: controller.signal },
        );
        const circle = page.items.find((item) => item.circle_id === id);
        if (circle) {
          if (!controller.signal.aborted) setState({ circle, loading: false });
          return;
        }
        if (!page.has_more) break;
        if (!page.next_cursor || seen.has(page.next_cursor))
          throw new Error('圈子游标无效，请重试。');
        seen.add(page.next_cursor);
        cursor = page.next_cursor;
      } while (!controller.signal.aborted);
      if (!controller.signal.aborted)
        setState({ loading: false, error: new Error('未找到这个圈子，请返回圈子首页重新选择。') });
    }
    void resolve().catch((error: Error) => {
      if (!controller.signal.aborted) setState({ loading: false, error });
    });
    return () => controller.abort();
  }, [id, revision, attempt]);
  return (
    <Modal
      visible
      title="圈子详情"
      className="rela-detail-modal"
      width={860}
      footer={null}
      closable={false}
      onCancel={onClose}
    >
      <div className="rela-detail-top">
        <span>圈子详情</span>
        <Button
          aria-label="关闭圈子详情"
          theme="borderless"
          icon={<X size={18} />}
          onClick={onClose}
        />
      </div>
      <ReadError error={state.error} retry={() => setAttempt(attempt + 1)} />
      {state.loading && !state.circle && <LoadingIndicator label="正在定位圈子" />}
      {state.circle && (
        <>
          <header className="rela-person-heading">
            <div className="rela-identity-heading">
              <span className="rela-avatar" aria-hidden="true">
                <Users size={24} strokeWidth={1.5} />
              </span>
              <div className="rela-identity-copy">
                <div className="rela-name-row">
                  <h2>{state.circle.name}</h2>
                  <div className="rela-badges">
                    <span>{state.circle.kind}</span>
                    {state.circle.status === 'archived' && (
                      <span className="rela-archived">已归档</span>
                    )}
                  </div>
                </div>
                <p className="rela-detail-aliases">
                  负责人：{state.circle.owner_person_id || '未指定'}
                </p>
                <CopyId id={state.circle.circle_id} label="Circle ID" />
              </div>
              {state.circle.status === 'active' && (
                <Button
                  className="rela-edit-person"
                  icon={<Pencil size={14} />}
                  disabled={state.loading || Boolean(state.error)}
                  onClick={() => setEditing(true)}
                >
                  编辑圈子
                </Button>
              )}
            </div>
          </header>
          <fieldset
            className="rela-detail-content"
            disabled={state.loading || Boolean(state.error)}
          >
            <Members
              key={id}
              circle={state.circle}
              revision={revision}
              onChange={onChange}
              onOpenPerson={onOpenPerson}
            />
          </fieldset>
          {editing && (
            <CircleForm
              circle={state.circle}
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

function Members({
  circle,
  revision,
  onChange,
  onOpenPerson,
}: {
  circle: Circle;
  revision: number;
  onChange: () => void;
  onOpenPerson: (id: string, event?: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const [left, setLeft] = useState(false);
  const [archived, setArchived] = useState(circle.status === 'archived');
  const [cursors, setCursors] = useState<string[]>(['']);
  const [editor, setEditor] = useState<Membership | null | undefined>(undefined);
  const [leaving, setLeaving] = useState<Membership | undefined>(undefined);
  const data = useRead<Page<Membership>>(
    queryPath('/v1/circles/' + pathPart(circle.circle_id) + '/members', {
      include_left: left,
      include_archived: archived,
      cursor: cursors.at(-1),
      limit: 20,
    }),
    revision,
  );
  return (
    <>
      <div className="rela-section-heading">
        <h3>圈子成员</h3>
        {circle.status === 'active' && (
          <Button theme="solid" icon={<Plus size={15} />} onClick={() => setEditor(null)}>
            添加成员
          </Button>
        )}
      </div>
      <div className="rela-filters">
        <Checkbox
          checked={left}
          onChange={(event) => {
            setLeft(Boolean(event.target.checked));
            setCursors(['']);
          }}
        >
          包含已退出
        </Checkbox>
        <Checkbox
          checked={archived}
          disabled={circle.status === 'archived'}
          onChange={(event) => {
            setArchived(Boolean(event.target.checked));
            setCursors(['']);
          }}
        >
          包含归档人物
        </Checkbox>
      </div>
      <ReadError
        error={data.error}
        retry={data.refresh}
        reset={cursors.length > 1 ? () => setCursors(['']) : undefined}
      />
      {data.loading && <LoadingIndicator overlay label="正在加载圈子成员" />}
      {!data.loading && !data.error && !data.data?.items.length && (
        <EmptyState
          icon={<Users size={26} strokeWidth={1.5} aria-hidden="true" />}
          title="暂无成员"
          description={
            left || archived
              ? '当前筛选条件下没有成员。'
              : circle.status === 'active'
                ? '点击“添加成员”，选择已认识的人物加入这个圈子。'
                : '当前没有可显示的成员。'
          }
        />
      )}
      {data.data?.items.map((member) => (
        <article key={member.person_id} className="rela-member-row">
          <div>
            <a
              data-archived={member.person?.status === 'archived'}
              data-rela-member-id={member.person_id}
              href={`#/people?person=${encodeURIComponent(member.person_id!)}`}
              aria-label={`查看成员人物：${member.person?.display_name}，${member.person_id}`}
              onClick={(event) => onOpenPerson(member.person_id!, event)}
            >
              {member.person?.display_name || member.person_id}
            </a>
            <div className="rela-badges">
              {member.person && <span>{kindNames[member.person.kind]}</span>}
              <span>{member.status === 'left' ? '已退出' : member.role}</span>
              {member.person?.status === 'archived' && <span>已归档</span>}
            </div>
            <p>
              {member.alias && `圈内别名：${member.alias}`}
              {member.identity_label && ` · ${member.identity_label}`}
            </p>
            <code>{member.person_id}</code>
          </div>
          {circle.status === 'active' && member.person?.status === 'active' && (
            <div className="rela-inline-actions">
              <Button
                theme="borderless"
                aria-label={`编辑成员：${member.person.display_name}，${member.person_id}`}
                onClick={() => setEditor(member)}
              >
                编辑
              </Button>
              {member.status === 'active' && (
                <Button
                  theme="borderless"
                  aria-label={`退出圈子：${member.person.display_name}，${member.person_id}`}
                  onClick={() => setLeaving(member)}
                >
                  退出
                </Button>
              )}
            </div>
          )}
        </article>
      ))}
      <Pagination
        page={data.data}
        disabled={data.loading}
        previous={cursors.length > 1 ? () => setCursors(cursors.slice(0, -1)) : undefined}
        next={(cursor) => setCursors([...cursors, cursor])}
      />
      {editor !== undefined && (
        <MemberForm
          circle={circle}
          member={editor || undefined}
          onClose={() => setEditor(undefined)}
          onSaved={() => {
            setEditor(undefined);
            onChange();
          }}
        />
      )}
      {leaving && (
        <ConfirmWrite
          title="退出圈子"
          description={`确认让 ${leaving.person?.display_name || leaving.person_id} 退出 ${circle.name}？人物本身不会被删除。`}
          saveText="确认退出"
          submit={() =>
            request(
              '/v1/circles/' +
                pathPart(circle.circle_id) +
                '/members/' +
                pathPart(leaving.person_id!),
              { method: 'DELETE' },
            )
          }
          onClose={() => setLeaving(undefined)}
          onSaved={() => {
            setLeaving(undefined);
            onChange();
          }}
        />
      )}
    </>
  );
}
