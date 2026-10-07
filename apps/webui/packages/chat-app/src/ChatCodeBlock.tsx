import { isValidElement, type ComponentProps, type ReactNode } from 'react';
import { CodeHighlight } from '@icarus/ui';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-typescript';
import ChatCopyButton from './ChatCopyButton';

export default function ChatCodeBlock({ children }: ComponentProps<'pre'>) {
  if (!isValidElement<{ className?: string; children?: ReactNode }>(children))
    return <pre>{children}</pre>;
  const language = /\blanguage-([^\s]+)/.exec(children.props.className ?? '')?.[1] ?? 'text';
  const code = String(children.props.children ?? '').replace(/\n$/, '');
  return (
    <div className="chat-code-block">
      <div className="chat-code-heading">
        <span>{language === 'text' ? '纯文本' : language}</span>
        <ChatCopyButton text={code} label="复制代码" />
      </div>
      <CodeHighlight key={language} code={code} language={language} lineNumber={false} />
    </div>
  );
}
