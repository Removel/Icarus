import Markdown from 'react-markdown';
import { memo, useMemo, useState } from 'react';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import ChatCodeBlock from './ChatCodeBlock';
import useChatText from './useChatText';
import remarkCitations from './remarkCitations';
import SearchSources from './SearchSources';
import type { WebSource } from './webSearch';

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
  sources,
}: {
  text: string;
  live: boolean;
  onGrowth: () => void;
  sources?: WebSource[];
}) {
  const content = useChatText(text, live, onGrowth);
  const [selected, setSelected] = useState<number>();
  const plugins = useMemo(
    () =>
      sources?.length
        ? [
            ...remarkPlugins,
            [remarkCitations, { indices: sources.map((s) => s.citeIndex) }] as [
              typeof remarkCitations,
              { indices: number[] },
            ],
          ]
        : remarkPlugins,
    [sources],
  );
  const rendering: Components = {
    ...components,
    a: ({ href, children }) => {
      const match = href?.match(/^#web-source-(\d+)$/);
      const index = match ? Number(match[1]) : undefined;
      if (index && sources?.some((s) => s.citeIndex === index))
        return (
          <button
            className="web-citation"
            onClick={() => setSelected(index)}
            aria-label={`查看来源 ${index}`}
          >
            {children}
          </button>
        );
      return (
        <a href={href} target="_blank" rel="noreferrer">
          {children}
        </a>
      );
    },
  };
  return (
    <div className="md-body chat-markdown" data-streaming={content !== text || undefined}>
      <Markdown
        remarkPlugins={plugins}
        rehypePlugins={rehypePlugins}
        components={rendering}
        skipHtml
      >
        {content}
      </Markdown>
      {sources?.length ? (
        <SearchSources
          sources={sources}
          open={selected !== undefined}
          selected={selected}
          onClose={() => setSelected(undefined)}
        />
      ) : null}
    </div>
  );
}

export default memo(ChatContent);
