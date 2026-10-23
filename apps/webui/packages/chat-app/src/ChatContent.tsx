import Markdown from 'react-markdown';
import { memo } from 'react';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import ChatCodeBlock from './ChatCodeBlock';
import useChatText from './useChatText';

const components = {
  pre: ChatCodeBlock,
  table: ({ children }: { children?: React.ReactNode }) => (
    <div className="md-table-wrap">
      <table>{children}</table>
    </div>
  ),
};
const remarkPlugins = [remarkGfm, remarkBreaks, remarkMath];
const rehypePlugins = [rehypeKatex];

function ChatContent({
  text,
  live,
  onGrowth,
}: {
  text: string;
  live: boolean;
  onGrowth: () => void;
}) {
  const content = useChatText(text, live, onGrowth);
  return (
    <div className="md-body chat-markdown" data-streaming={content !== text || undefined}>
      <Markdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
        skipHtml
      >
        {content}
      </Markdown>
    </div>
  );
}

export default memo(ChatContent);
