import {
  createContext,
  isValidElement,
  lazy,
  Suspense,
  useContext,
  type ComponentProps,
  type ReactNode,
} from 'react';
import ChatCopyButton from './ChatCopyButton';

const Highlight = lazy(() => import('./ChatCodeHighlight'));
export const StreamingCode = createContext(false);

export default function ChatCodeBlock({ children }: ComponentProps<'pre'>) {
  if (!isValidElement<{ className?: string; children?: ReactNode }>(children))
    return <pre>{children}</pre>;
  const language = /\blanguage-([^\s]+)/.exec(children.props.className ?? '')?.[1] ?? 'text';
  const code = String(children.props.children ?? '').replace(/\n$/, '');
  return <CodeBlock code={code} language={language} />;
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const streaming = useContext(StreamingCode);
  const plain = (
    <pre>
      <code>{code}</code>
    </pre>
  );
  return (
    <div className="code-block chat-code-block">
      <div className="code-head chat-code-heading">
        <span className="code-lang">{language === 'text' ? '纯文本' : language}</span>
        <ChatCopyButton text={code} label="复制代码" />
      </div>
      {streaming ? (
        plain
      ) : (
        <Suspense fallback={plain}>
          <Highlight code={code} language={language} />
        </Suspense>
      )}
    </div>
  );
}
