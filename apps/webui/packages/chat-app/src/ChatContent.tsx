import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ChatCodeBlock from './ChatCodeBlock';
import useChatText from './useChatText';

const components = { pre: ChatCodeBlock };

export default function ChatContent({
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
    <div className="chat-markdown" data-streaming={content !== text || undefined}>
      <Markdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {content}
      </Markdown>
    </div>
  );
}
