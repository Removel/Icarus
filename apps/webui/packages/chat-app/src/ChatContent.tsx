import Markdown from 'react-markdown';
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import type { PluggableList } from 'unified';
import ChatCodeBlock, { StreamingCode } from './ChatCodeBlock';
import useChatText from './useChatText';
import remarkCitations from './remarkCitations';
import SearchSources from './SearchSources';
import type { WebSource } from './webSearch';
import { advanceMarkdown, provisionalMarkdown, type MarkdownState } from './streamingMarkdown';

const components = {
  pre: ChatCodeBlock,
  table: ({ children }: { children?: React.ReactNode }) => (
    <div className="md-table-wrap">
      <table>{children}</table>
    </div>
  ),
};
const remarkPlugins = [remarkGfm, remarkBreaks, remarkMath];
const noPlugins: PluggableList = [];
const MarkdownBlock = memo(function MarkdownBlock({
  text,
  plugins,
  rehypePlugins,
  rendering,
}: {
  text: string;
  plugins: PluggableList;
  rehypePlugins: PluggableList;
  rendering: Components;
}) {
  return (
    <Markdown remarkPlugins={plugins} rehypePlugins={rehypePlugins} components={rendering} skipHtml>
      {text}
    </Markdown>
  );
});

function ChatContent({
  text,
  live,
  onGrowth,
  sources,
  streaming = live,
}: {
  text: string;
  live: boolean;
  onGrowth: () => void;
  sources?: WebSource[];
  streaming?: boolean;
}) {
  const content = useChatText(text, live, onGrowth);
  const [selected, setSelected] = useState<number>();
  const [rehypePlugins, setRehypePlugins] = useState<PluggableList>(noPlugins);
  const needsMath = content.includes('$');
  useEffect(() => {
    if (!needsMath) return;
    let stale = false;
    void import('./mathRenderer')
      .then(({ default: math }) => {
        if (!stale) setRehypePlugins([math]);
      })
      .catch(() => {
        /* Preserve readable Markdown if optional math assets fail. */
      });
    return () => {
      stale = true;
    };
  }, [needsMath]);
  const state = useRef<MarkdownState | undefined>(undefined);
  const incremental = streaming || content !== text;
  const segments = useMemo(() => {
    state.current = incremental ? advanceMarkdown(content, state.current) : undefined;
    return state.current;
  }, [content, incremental]);
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
  const rendering: Components = useMemo(
    () => ({
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
    }),
    [sources],
  );
  const tail = segments ? content.slice(segments.offset) : content;
  const plainTail = incremental && provisionalMarkdown(tail);
  return (
    <div className="md-body chat-markdown" data-streaming={content !== text || undefined}>
      {segments?.blocks.map((block) => (
        <MarkdownBlock
          key={block.offset}
          text={block.text}
          plugins={plugins}
          rehypePlugins={rehypePlugins}
          rendering={rendering}
        />
      ))}
      <StreamingCode.Provider value={incremental}>
        {plainTail ? (
          <p className="chat-markdown-tail">{tail}</p>
        ) : (
          <MarkdownBlock
            key={segments?.offset ?? 0}
            text={tail}
            plugins={plugins}
            rehypePlugins={rehypePlugins}
            rendering={rendering}
          />
        )}
      </StreamingCode.Provider>
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
