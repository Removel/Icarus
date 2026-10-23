import { useEffect, useState } from 'react';
import {
  IconEdit,
  IconLike,
  IconDislike,
  IconRefresh,
  IconSpeaker,
  IconShare,
} from './dswui/Icons';
import ChatCopyButton from './ChatCopyButton';

export default function ChatMessageActions({
  text,
  user,
  onCompose,
  prompt,
  disabled,
}: {
  text: string;
  user: boolean;
  onCompose: (text: string) => void;
  prompt?: string;
  disabled: boolean;
}) {
  const [feedback, setFeedback] = useState<'like' | 'dislike' | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [notice, setNotice] = useState('');
  useEffect(
    () => () => {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    },
    [],
  );
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 2500);
    return () => clearTimeout(timer);
  }, [notice]);
  return (
    <div className={`msg-actions ${user ? '' : 'assistant-actions'} chat-message-actions`}>
      <ChatCopyButton text={text} label={user ? '复制消息' : '复制回答'} />
      {user ? (
        <button
          className="icon-btn"
          title="编辑为新消息"
          aria-label="编辑为新消息"
          disabled={disabled}
          onClick={() => onCompose(text)}
        >
          <IconEdit />
        </button>
      ) : (
        <>
          <button
            className="icon-btn"
            title="再次提问（填入输入框）"
            aria-label="再次提问"
            disabled={disabled || !prompt}
            onClick={() => {
              if (prompt) onCompose(prompt);
            }}
          >
            <IconRefresh />
          </button>
          {(
            [
              { value: 'like', label: '有帮助', icon: IconLike },
              { value: 'dislike', label: '没帮助', icon: IconDislike },
            ] as const
          ).map((item) => (
            <button
              key={item.value}
              className={`icon-btn ${feedback === item.value ? 'active' : ''}`}
              title={item.label}
              aria-label={item.label}
              aria-pressed={feedback === item.value}
              onClick={() => {
                setFeedback(feedback === item.value ? null : item.value);
                setNotice('评价已在本页标记');
              }}
            >
              <item.icon />
            </button>
          ))}
          <button
            className={`icon-btn ${speaking ? 'active' : ''}`}
            title={speaking ? '停止朗读' : '朗读'}
            aria-label={speaking ? '停止朗读' : '朗读'}
            onClick={() => {
              if (!('speechSynthesis' in window)) {
                setNotice('当前浏览器不支持朗读');
                return;
              }
              window.speechSynthesis.cancel();
              if (speaking) {
                setSpeaking(false);
                return;
              }
              const speech = new SpeechSynthesisUtterance(text);
              speech.lang = 'zh-CN';
              speech.onend = () => setSpeaking(false);
              speech.onerror = () => setSpeaking(false);
              setSpeaking(true);
              window.speechSynthesis.speak(speech);
            }}
          >
            <IconSpeaker />
          </button>
          <button
            className="icon-btn"
            title="分享回答"
            aria-label="分享回答"
            onClick={async () => {
              try {
                if (navigator.share) await navigator.share({ text });
                else {
                  await navigator.clipboard.writeText(text);
                  setNotice('回答已复制，可粘贴分享');
                }
              } catch (error) {
                if (!(error instanceof DOMException && error.name === 'AbortError'))
                  setNotice('分享失败，请复制回答');
              }
            }}
          >
            <IconShare />
          </button>
        </>
      )}
      {notice && (
        <span className="msg-meta" role="status">
          {notice}
        </span>
      )}
    </div>
  );
}
