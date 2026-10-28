import type { MouseEvent } from 'react';
import { Bot, UserRound } from 'lucide-react';
import { aliasText, kindNames, shortPersonId, type Person } from './types';

export default function PersonCard({
  person,
  ids,
  href,
  onOpen,
}: {
  person: Person;
  ids: string[];
  href: string;
  onOpen: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  return (
    <a
      className="person-card"
      href={href}
      onClick={onOpen}
      data-person-id={person.person_id}
      data-kind={person.kind}
      aria-label={`查看人物：${person.display_name}，${person.person_id}`}
    >
      <span className="person-card-avatar" aria-hidden="true">
        {person.kind === 'agent' ? (
          <Bot size={20} strokeWidth={1.5} />
        ) : person.kind === 'user' ? (
          <UserRound size={19} strokeWidth={1.5} />
        ) : (
          Array.from(person.display_name)[0]
        )}
      </span>
      <span className="person-card-copy">
        <span className="person-card-heading">
          <span className="person-card-name" title={person.display_name}>
            {person.display_name}
          </span>
          <span className="person-card-kind">
            <span>{kindNames[person.kind]}</span>
            {person.status === 'archived' && <span className="rela-archived">已归档</span>}
          </span>
        </span>
        {Boolean(person.aliases?.length) && (
          <span className="person-card-aliases">
            <span>别名</span> {person.aliases!.map(aliasText).join('、')}
          </span>
        )}
        <span className="person-card-id" aria-label="Person ID">
          <small>ID</small>
          <code>{shortPersonId(person.person_id, ids)}</code>
        </span>
      </span>
    </a>
  );
}
