import { useRef, useState } from 'react';
import { Field, Input, Select, TextArea } from '@icarus/ui';
import WriteDialog from './WriteDialog';
import { pathPart, personPath, request } from './rela';
import { jsonText, type Person, type ProfileField } from './types';

type Callbacks = { onClose: () => void; onSaved: () => void };

export function PersonForm({
  person,
  onCreated,
  ...callbacks
}: Callbacks & { person?: Person; onCreated?: (person: Person) => void }) {
  const [name, setName] = useState(person?.display_name || '');
  const [kind, setKind] = useState(person?.kind || 'human');
  const special = person?.kind === 'user' || person?.kind === 'agent';
  const created = useRef<Person | undefined>(undefined);
  return (
    <WriteDialog
      title={person ? '编辑人物' : '添加人物'}
      dirty={name !== (person?.display_name || '') || kind !== (person?.kind || 'human')}
      {...callbacks}
      onSaved={() => {
        callbacks.onSaved();
        if (created.current) onCreated?.(created.current);
      }}
      submit={async () => {
        if (!name.trim()) throw new Error('请填写姓名。');
        const result = await request<{ person: Person }>(
          person ? personPath(person.person_id) : '/v1/persons',
          {
            method: person ? 'PATCH' : 'POST',
            body: { display_name: name.trim(), ...(!special ? { kind } : {}) },
          },
        );
        if (!person) created.current = result.person;
      }}
    >
      <Field label="姓名">
        <Input aria-label="姓名" required maxLength={256} value={name} onChange={setName} />
      </Field>
      {!special && (
        <Field label="人物类型">
          <Select
            aria-label="创建人物类型"
            value={kind}
            optionList={[
              { value: 'human', label: '人物' },
              { value: 'virtual', label: '虚拟人物' },
            ]}
            onChange={(value) => setKind(value as Person['kind'])}
          />
        </Field>
      )}
    </WriteDialog>
  );
}

export function AliasForm({ person, ...callbacks }: Callbacks & { person: Person }) {
  const [namespace, setNamespace] = useState('name');
  const [value, setValue] = useState('');
  const [display, setDisplay] = useState('');
  return (
    <WriteDialog
      title="添加别名"
      dirty={namespace !== 'name' || Boolean(value || display)}
      {...callbacks}
      submit={async () => {
        if (!value.trim()) throw new Error('请填写别名值。');
        await request(personPath(person.person_id) + '/aliases', {
          method: 'POST',
          body: {
            namespace: namespace.trim(),
            value: value.trim(),
            ...(display.trim() ? { display_value: display.trim() } : {}),
          },
        });
      }}
    >
      <Field label="命名空间" hint="普通姓名或昵称使用 name；精确身份使用对应命名空间。">
        <Input
          aria-label="别名命名空间"
          value={namespace}
          onChange={setNamespace}
          required
          pattern="[a-z][a-z0-9_]{0,31}"
        />
      </Field>
      <Field label="别名值">
        <Input aria-label="别名值" value={value} onChange={setValue} required maxLength={256} />
      </Field>
      <Field label="显示名称（可选）">
        <Input aria-label="别名显示名称" value={display} onChange={setDisplay} maxLength={256} />
      </Field>
    </WriteDialog>
  );
}

export function ProfileFieldForm({
  person,
  field,
  ...callbacks
}: Callbacks & { person: Person; field?: ProfileField }) {
  const [key, setKey] = useState(field?.attr_key || '');
  const initialMode = field && typeof field.attr_value !== 'string' ? 'json' : 'text';
  const [mode, setMode] = useState(initialMode);
  const initialValue = field ? jsonText(field.attr_value) : '';
  const [value, setValue] = useState(initialValue);
  return (
    <WriteDialog
      title={field ? '修正档案字段' : '添加档案字段'}
      dirty={key !== (field?.attr_key || '') || value !== initialValue || mode !== initialMode}
      {...callbacks}
      submit={async () => {
        let parsed: unknown = value;
        if (mode === 'json') {
          try {
            parsed = JSON.parse(value);
          } catch {
            throw new Error('请输入合法 JSON，包括字符串、数字、布尔值、对象、数组或 null。');
          }
        }
        if (new TextEncoder().encode(JSON.stringify(parsed)).length > 65536)
          throw new Error('档案值不能超过 64 KiB。');
        await request(personPath(person.person_id) + '/profile/' + pathPart(key), {
          method: 'PUT',
          body: { value: parsed, source: 'user' },
        });
      }}
    >
      <Field label="档案 key">
        <Input
          aria-label="档案 key"
          required
          disabled={Boolean(field)}
          maxLength={128}
          pattern="[a-z][a-z0-9_]*(\.[a-z0-9_]+)*"
          value={key}
          onChange={setKey}
        />
      </Field>
      <Field label="值类型">
        <Select
          aria-label="值类型"
          value={mode}
          optionList={[
            { value: 'text', label: '文本' },
            { value: 'json', label: 'JSON' },
          ]}
          onChange={setMode}
        />
      </Field>
      <Field
        label="档案值"
        hint={
          mode === 'json'
            ? 'null 是有效空值，忘记字段请使用独立操作。'
            : '文本按原文保存，不自动识别 JSON。'
        }
      >
        <TextArea aria-label="档案值" rows={7} value={value} onChange={setValue} />
      </Field>
    </WriteDialog>
  );
}

export function ConfirmWrite({
  title,
  description,
  submit,
  saveText,
  ...callbacks
}: Callbacks & {
  title: string;
  description: string;
  submit: () => Promise<unknown>;
  saveText: string;
}) {
  return (
    <WriteDialog title={title} dirty={false} submit={submit} saveText={saveText} {...callbacks}>
      <p>{description}</p>
    </WriteDialog>
  );
}
