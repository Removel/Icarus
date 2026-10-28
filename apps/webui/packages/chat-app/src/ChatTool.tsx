import { Check, ChevronRight, CircleAlert, LoaderCircle, Square, Terminal } from 'lucide-react';
import type { ChatItem } from './updates';
import { navigate } from '@icarus/ui';
import { relationshipLinks } from './relationshipLinks';

export default function ChatTool({ entry, onGrowth }: { entry: ChatItem; onGrowth: () => void }) {
  const status = entry.toolStatus ?? (entry.final ? 'success' : 'running');
  const people =
    entry.final && status === 'success' ? relationshipLinks(entry.label, entry.detail) : [];
  const StatusIcon =
    status === 'running'
      ? LoaderCircle
      : status === 'error'
        ? CircleAlert
        : status === 'stopped'
          ? Square
          : Check;
  return (
    <details className="chat-message chat-tool" data-status={status} onToggle={onGrowth}>
      <summary>
        <Terminal size={15} className="chat-tool-icon" aria-hidden="true" />
        <span className="chat-tool-name" title={entry.label}>
          {entry.label}
        </span>
        <span className="chat-tool-status">
          <StatusIcon size={13} aria-hidden="true" />
          {entry.text}
        </span>
        <ChevronRight className="chat-tool-caret" size={14} aria-hidden="true" />
      </summary>
      <div className="chat-tool-detail">
        {entry.arguments && (
          <section>
            <h4>输入参数</h4>
            <pre>{entry.arguments}</pre>
          </section>
        )}
        {entry.final && entry.detail && (
          <section>
            <h4>{status === 'error' ? '错误信息' : '执行结果'}</h4>
            <pre>{entry.detail}</pre>
            {people.length > 0 && (
              <div className="chat-tool-people">
                {people.map((person) => (
                  <a
                    className="chat-tool-person-link"
                    key={person.id}
                    href={`#/people?person=${encodeURIComponent(person.id)}&section=profile&person_archived=true`}
                    aria-label={`查看人物档案：${person.name}，${person.id}`}
                    onClick={(event) => {
                      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                      event.preventDefault();
                      navigate(event.currentTarget.getAttribute('href')!);
                    }}
                  >
                    {person.name}
                    <code>{person.id}</code>
                  </a>
                ))}
              </div>
            )}
          </section>
        )}
      </div>
    </details>
  );
}
