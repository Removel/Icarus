import { CodeHighlight } from '@icarus/ui';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-typescript';

export default function ChatCodeHighlight({ code, language }: { code: string; language: string }) {
  return <CodeHighlight key={language} code={code} language={language} lineNumber={false} />;
}
