import { useState } from 'react';
import { Field, Input, Select } from '@icarus/ui';
import WriteDialog from './WriteDialog';
import PersonPicker from './PersonPicker';
import { pathPart, request } from './rela';
import type { Person, Relation } from './types';

export default function RelationForm({
  person,
  relation,
  onClose,
  onSaved,
}: {
  person: Person;
  relation?: Relation;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [other, setOther] = useState<Person | undefined>(relation?.other_person);
  const [kind, setKind] = useState(relation?.kind || '');
  const [direction, setDirection] = useState(relation?.direction || 'one_way');
  const [forward, setForward] = useState(relation?.forward_detail || '');
  const [reverse, setReverse] = useState(relation?.reverse_detail || '');
  const from = relation?.from_person_id || person.person_id;
  const to = relation?.to_person_id || other?.person_id;
  const fromName = from === person.person_id ? person.display_name : other?.display_name || from;
  const toName = to === person.person_id ? person.display_name : other?.display_name || '所选人物';
  const dirty =
    other?.person_id !== relation?.other_person?.person_id ||
    kind !== (relation?.kind || '') ||
    direction !== (relation?.direction || 'one_way') ||
    forward !== (relation?.forward_detail || '') ||
    reverse !== (relation?.reverse_detail || '');
  return (
    <WriteDialog
      title={relation ? '修改关系' : '添加关系'}
      dirty={dirty}
      onClose={onClose}
      onSaved={onSaved}
      submit={async () => {
        if (!to || to === from) throw new Error('请选择不同的关联人物。');
        if (!kind.trim() || !forward.trim() || (direction === 'two_way' && !reverse.trim()))
          throw new Error('请填写关系类型与对应称谓。');
        await request('/v1/relations' + (relation ? '/' + pathPart(relation.relation_id) : ''), {
          method: relation ? 'PATCH' : 'POST',
          body: {
            ...(!relation ? { from_person_id: from, to_person_id: to } : {}),
            kind: kind.trim(),
            direction,
            forward_detail: forward.trim(),
            ...(direction === 'two_way' ? { reverse_detail: reverse.trim() } : {}),
            source: 'user',
          },
        });
      }}
    >
      {relation ? (
        <p>
          {fromName}（{from}） → {toName}（{to}）
        </p>
      ) : (
        <PersonPicker
          label="关联人物"
          value={other}
          onChange={setOther}
          exclude={person.person_id}
        />
      )}
      <Field label="关系类型">
        <Input aria-label="关系类型" required maxLength={64} value={kind} onChange={setKind} />
      </Field>
      <Field label="方向">
        <Select
          aria-label="关系方向"
          value={direction}
          optionList={[
            { value: 'one_way', label: '单向称谓' },
            { value: 'two_way', label: '双向称谓' },
          ]}
          onChange={(value) => setDirection(value as Relation['direction'])}
        />
      </Field>
      <Field label={`${fromName} 是 ${toName} 的…`}>
        <Input
          aria-label="正向称谓"
          required
          maxLength={256}
          value={forward}
          onChange={setForward}
        />
      </Field>
      {direction === 'two_way' && (
        <Field label={`${toName} 是 ${fromName} 的…`}>
          <Input
            aria-label="反向称谓"
            required
            maxLength={256}
            value={reverse}
            onChange={setReverse}
          />
        </Field>
      )}
      <p className="rela-source">
        {relation?.scope_circle_id ? '所属圈子范围：' + relation.scope_circle_id : '范围：全局'}
      </p>
    </WriteDialog>
  );
}
