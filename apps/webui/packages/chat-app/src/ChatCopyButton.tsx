import { useEffect, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { Button, Toast } from '@icarus/ui';

export default function ChatCopyButton({ text, label }: { text: string; label: string }) {
  const [copiedText, setCopiedText] = useState<string>();
  const [copying, setCopying] = useState(false);
  useEffect(() => {
    if (copiedText === undefined) return;
    const timer = setTimeout(() => setCopiedText(undefined), 2000);
    return () => clearTimeout(timer);
  }, [copiedText]);
  const copied = copiedText === text;
  const title = copied ? label.replace('复制', '已复制') : label;
  return (
    <Button
      className="chat-copy"
      theme="borderless"
      type="tertiary"
      size="small"
      icon={copied ? <Check size={14} /> : <Copy size={14} />}
      aria-label={title}
      title={title}
      disabled={copying || !text}
      onClick={async () => {
        setCopying(true);
        try {
          await navigator.clipboard.writeText(text);
          setCopiedText(text);
        } catch {
          Toast.error('复制失败，请选择文本后手动复制');
        } finally {
          setCopying(false);
        }
      }}
    />
  );
}
