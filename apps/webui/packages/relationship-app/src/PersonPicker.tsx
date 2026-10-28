import { useState } from 'react';
import { Button, Input, LoadingIndicator } from '@icarus/ui';
import useRead from './useRead';
import { queryPath } from './rela';
import { Pagination, ReadError } from './ReadState';
import { aliasText, kindNames, type Page, type Person } from './types';

export default function PersonPicker({
  label,
  value,
  onChange,
  exclude,
}: {
  label: string;
  value?: Person;
  onChange: (person: Person) => void;
  exclude?: string;
}) {
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(!value);
  const [cursors, setCursors] = useState<string[]>(['']);
  const data = useRead<Page<Person>>(
    searching ? queryPath('/v1/persons', { query, limit: 20, cursor: cursors.at(-1) }) : null,
  );
  return (
    <div className="rela-person-picker">
      <span className="form-label">{label}</span>
      {value && (
        <div className="rela-picked-person">
          <span>
            {value.display_name} · {kindNames[value.kind]}
          </span>
          <code>{value.person_id}</code>
          <Button onClick={() => setSearching(!searching)}>重新选择</Button>
        </div>
      )}
      {searching && (
        <>
          <Input
            aria-label={label + '搜索'}
            placeholder="搜索姓名或别名"
            value={query}
            onChange={(text) => {
              setQuery(text);
              setCursors(['']);
            }}
          />
          <ReadError error={data.error} retry={data.refresh} />
          {data.loading && <LoadingIndicator label="正在搜索候选人物" />}
          <div className="rela-person-options">
            {data.data?.items
              .filter((person) => person.person_id !== exclude)
              .map((person) => (
                <button
                  type="button"
                  key={person.person_id}
                  aria-label={`选择${label}：${person.display_name}，${person.person_id}`}
                  onClick={() => {
                    onChange(person);
                    setSearching(false);
                  }}
                >
                  <strong>{person.display_name}</strong>
                  <small>
                    {kindNames[person.kind]} ·{' '}
                    {person.aliases?.map(aliasText).join('、') || '暂无别名'}
                  </small>
                  <code>{person.person_id}</code>
                </button>
              ))}
          </div>
          {data.data && !data.data.items.length && <p>没有匹配的候选人物</p>}
          <Pagination
            page={data.data}
            disabled={data.loading}
            previous={cursors.length > 1 ? () => setCursors(cursors.slice(0, -1)) : undefined}
            next={(cursor) => setCursors([...cursors, cursor])}
          />
        </>
      )}
    </div>
  );
}
