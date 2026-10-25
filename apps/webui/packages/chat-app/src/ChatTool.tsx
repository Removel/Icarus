import { Check, ChevronRight, CircleAlert, LoaderCircle, Square, Terminal } from 'lucide-react';
import type { ChatItem } from './updates';

export default function ChatTool({ entry, onGrowth }: { entry: ChatItem; onGrowth: () => void }) {
  const status = entry.toolStatus ?? (entry.final ? 'success' : 'running');
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
          </section>
        )}
      </div>
    </details>
  );
}
