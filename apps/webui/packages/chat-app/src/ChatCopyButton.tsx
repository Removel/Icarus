import { useEffect, useState } from 'react';
import { IconCheck, IconCopy } from './dswui/Icons';

export default function ChatCopyButton({ text, label }: { text: string; label: string }) {
  const [copiedText, setCopiedText] = useState<string>();
  const [copying, setCopying] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (copiedText === undefined) return;
    const timer = setTimeout(() => setCopiedText(undefined), 2000);
    return () => clearTimeout(timer);
  }, [copiedText]);
  const copied = copiedText === text;
  const title = copied ? label.replace('复制', '已复制') : label;
  return (
    <>
      <button
        type="button"
        className="icon-btn chat-copy"
        aria-label={title}
        title={title}
        disabled={copying || !text}
        onClick={async () => {
          setCopying(true);
          setFailed(false);
          try {
            await navigator.clipboard.writeText(text);
            setCopiedText(text);
          } catch {
            setFailed(true);
          } finally {
            setCopying(false);
          }
        }}
      >
        {copied ? <IconCheck /> : <IconCopy />}
      </button>
      {failed && <span role="status">复制失败，请选择文本后手动复制</span>}
    </>
  );
}
