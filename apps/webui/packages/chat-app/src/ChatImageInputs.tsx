import { useState } from 'react';
import { RotateCcw, X } from 'lucide-react';
import type { DraftImage } from './useImageInputs';
import ChatImageViewer from './ChatImageViewer';

function ImageCard({
  image,
  index,
  disabled,
  onOpen,
  onRemove,
  onRetry,
}: {
  image: DraftImage;
  index: number;
  disabled: boolean;
  onOpen: () => void;
  onRemove: () => void;
  onRetry: () => void;
}) {
  return (
    <div className="chat-image-input" data-failed={Boolean(image.error) || undefined}>
      <button
        type="button"
        className="chat-image-tile"
        title={image.name}
        aria-label={`预览待发送图片 ${index + 1}`}
        onClick={onOpen}
      >
        <img src={image.preview} alt={`待发送图片 ${index + 1}`} />
      </button>
      <button
        type="button"
        className="chat-image-remove"
        disabled={disabled}
        aria-label={`移除图片 ${index + 1}`}
        onClick={onRemove}
      >
        <X size={12} strokeWidth={1.5} absoluteStrokeWidth />
      </button>
      {image.error ? (
        <div className="chat-image-state" title={image.error}>
          <button
            type="button"
            className="chat-image-retry"
            disabled={disabled}
            aria-label="重试上传"
            onClick={onRetry}
          >
            <RotateCcw size={20} />
            <span>重试</span>
          </button>
        </div>
      ) : (
        <span role="status" className={image.resource ? 'dsw-sr-only' : 'chat-image-state'}>
          <span className="dsw-sr-only">
            {image.resource ? '已上传' : `上传中 ${image.progress}%`}
          </span>
          {!image.resource && (
            <svg
              className="chat-image-progress"
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" opacity=".35" />
              <circle
                cx="12"
                cy="12"
                r="9"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeDasharray={Math.PI * 18}
                strokeDashoffset={Math.PI * 18 * (1 - image.progress / 100)}
                transform="rotate(-90 12 12)"
              />
            </svg>
          )}
        </span>
      )}
    </div>
  );
}

export default function ChatImageInputs({
  images,
  disabled,
  remove,
  retry,
}: {
  images: DraftImage[];
  disabled: boolean;
  remove: (id: string) => void;
  retry: (id: string) => void;
}) {
  const [selected, setSelected] = useState<number>();
  return (
    <div className="chat-image-inputs" aria-label="待发送图片">
      {images.map((image, index) => (
        <ImageCard
          key={image.id}
          image={image}
          index={index}
          disabled={disabled}
          onOpen={() => setSelected(index)}
          onRemove={() => remove(image.id)}
          onRetry={() => retry(image.id)}
        />
      ))}
      {images.some((image) => image.error) && (
        <div className="chat-image-errors">
          {images
            .filter((image) => image.error)
            .map((image) => (
              <p className="chat-image-upload-error" role="alert" key={image.id}>
                {image.name}：{image.error}
              </p>
            ))}
        </div>
      )}
      {selected !== undefined && images[selected] && (
        <ChatImageViewer
          images={images.map((image) => ({ src: image.preview, alt: image.name }))}
          initial={selected}
          onClose={() => setSelected(undefined)}
        />
      )}
    </div>
  );
}
