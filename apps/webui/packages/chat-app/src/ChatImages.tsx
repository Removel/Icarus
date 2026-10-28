import { useState } from 'react';
import { imageBoxStyle, imagePosition, imageUrl, type ImageResource } from './imageResources';
import ChatImageViewer from './ChatImageViewer';

function Image({
  resource,
  workspace,
  session,
  onGrowth,
  index,
  onOpen,
}: {
  resource: ImageResource;
  workspace: string;
  session: string;
  onGrowth: () => void;
  index: number;
  onOpen: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const [ratio, setRatio] = useState(1);
  const url = imageUrl(resource, workspace, session);
  return failed ? (
    <span className="chat-image-error">图片 {index + 1} 无法加载</span>
  ) : (
    <button
      type="button"
      className="chat-image-tile"
      onClick={onOpen}
      aria-label={`查看图片 ${index + 1}`}
      style={imageBoxStyle(ratio)}
    >
      <img
        src={url}
        alt={`图片 ${index + 1}`}
        loading="lazy"
        style={{ objectPosition: imagePosition(ratio) }}
        onLoad={(event) => {
          setRatio(event.currentTarget.naturalWidth / event.currentTarget.naturalHeight);
          onGrowth();
        }}
        onError={() => {
          setFailed(true);
          onGrowth();
        }}
      />
    </button>
  );
}

export default function ChatImages({
  resources,
  workspace,
  session,
  onGrowth,
}: {
  resources: ImageResource[];
  workspace: string;
  session: string;
  onGrowth: () => void;
}) {
  const [selected, setSelected] = useState<number>();
  const images = resources.map((resource, index) => ({
    src: imageUrl(resource, workspace, session),
    alt: `图片 ${index + 1}`,
  }));
  return (
    <div className="chat-images" data-multiple={resources.length > 1 || undefined}>
      {resources.map((resource, index) => (
        <Image
          key={resource.resource_id}
          resource={resource}
          workspace={workspace}
          session={session}
          onGrowth={onGrowth}
          index={index}
          onOpen={() => setSelected(index)}
        />
      ))}
      {selected !== undefined && (
        <ChatImageViewer
          images={images}
          initial={selected}
          onClose={() => setSelected(undefined)}
        />
      )}
    </div>
  );
}
