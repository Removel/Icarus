import { useRef, useState } from 'react';
import { Button, Field, Input } from '@icarus/ui';
import PersonPicker from './PersonPicker';
import WriteDialog from './WriteDialog';
import useRead from './useRead';
import { pathPart, personPath, request } from './rela';
import { ReadError } from './ReadState';
import type { Circle, Membership, Person } from './types';

export function CircleForm({
  circle,
  onClose,
  onSaved,
  onCreated,
}: {
  circle?: Circle;
  onClose: () => void;
  onSaved: () => void;
  onCreated?: (circle: Circle) => void;
}) {
  const [name, setName] = useState(circle?.name || '');
  const [kind, setKind] = useState(circle?.kind || '');
  const [owner, setOwner] = useState<Person | null | undefined>(undefined);
  const existingOwner = useRead<{ person: Person }>(
    circle?.owner_person_id ? personPath(circle.owner_person_id) + '?include_archived=true' : null,
  );
  const ownerId = owner === undefined ? circle?.owner_person_id || null : owner?.person_id || null;
  const created = useRef<Circle | undefined>(undefined);
  return (
    <WriteDialog
      title={circle ? '编辑圈子' : '新建圈子'}
      dirty={
        name !== (circle?.name || '') ||
        kind !== (circle?.kind || '') ||
        ownerId !== (circle?.owner_person_id || null)
      }
      onClose={onClose}
      onSaved={() => {
        onSaved();
        if (created.current) onCreated?.(created.current);
      }}
      submit={async () => {
        if (!name.trim() || !kind.trim()) throw new Error('请填写圈子名称和类型。');
        const result = await request<{ circle: Circle }>(
          '/v1/circles' + (circle ? '/' + pathPart(circle.circle_id) : ''),
          {
            method: circle ? 'PATCH' : 'POST',
            body: { name: name.trim(), kind: kind.trim(), owner_person_id: ownerId },
          },
        );
        if (!circle) created.current = result.circle;
      }}
    >
      <Field label="圈子名称">
        <Input aria-label="圈子名称" required maxLength={256} value={name} onChange={setName} />
      </Field>
      <Field label="圈子类型">
        <Input aria-label="圈子类型" required maxLength={64} value={kind} onChange={setKind} />
      </Field>
      <PersonPicker
        label="负责人（可选）"
        value={owner === undefined ? existingOwner.data?.person : owner || undefined}
        onChange={setOwner}
      />
      {ownerId && (
        <>
          <code>{ownerId}</code>
          <Button onClick={() => setOwner(null)}>不指定负责人</Button>
        </>
      )}
      <ReadError error={existingOwner.error} retry={existingOwner.refresh} />
    </WriteDialog>
  );
}

export function MemberForm({
  circle,
  member,
  onClose,
  onSaved,
}: {
  circle: Circle;
  member?: Membership;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [person, setPerson] = useState(member?.person);
  const [alias, setAlias] = useState(member?.alias || '');
  const [role, setRole] = useState(member?.role || 'member');
  const [identity, setIdentity] = useState(member?.identity_label || '');
  const dirty =
    person?.person_id !== member?.person_id ||
    alias !== (member?.alias || '') ||
    role !== (member?.role || 'member') ||
    identity !== (member?.identity_label || '');
  return (
    <WriteDialog
      title={member ? '编辑成员' : '添加成员'}
      dirty={dirty}
      onClose={onClose}
      onSaved={onSaved}
      submit={async () => {
        if (!person || !role.trim()) throw new Error('请选择成员人物并填写角色。');
        await request(
          '/v1/circles/' + pathPart(circle.circle_id) + '/members/' + pathPart(person.person_id),
          {
            method: 'PUT',
            body: {
              alias: alias.trim() || null,
              role: role.trim(),
              identity_label: identity.trim() || null,
              ...(member?.joined_at ? { joined_at: member.joined_at } : {}),
            },
          },
        );
      }}
    >
      {member ? (
        <p>
          {person?.display_name} · {member.person_id}
        </p>
      ) : (
        <PersonPicker label="成员人物" value={person} onChange={setPerson} />
      )}
      <Field label="圈内别名（可选）">
        <Input aria-label="圈内别名" maxLength={256} value={alias} onChange={setAlias} />
      </Field>
      <Field label="成员角色">
        <Input aria-label="成员角色" required maxLength={256} value={role} onChange={setRole} />
      </Field>
      <Field label="身份说明（可选）">
        <Input aria-label="成员身份说明" maxLength={256} value={identity} onChange={setIdentity} />
      </Field>
    </WriteDialog>
  );
}
