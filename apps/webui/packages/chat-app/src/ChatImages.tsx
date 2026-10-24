import { useState } from 'react';
import { imageUrl, type ImageResource } from './imageResources';

function Image({
  resource,
  workspace,
  session,
  onGrowth,
  index,
}: {
  resource: ImageResource;
  workspace: string;
  session: string;
  onGrowth: () => void;
  index: number;
}) {
  const [failed, setFailed] = useState(false);
  const url = imageUrl(resource, workspace, session);
  return failed ? (
    <span className="chat-image-error">图片 {index + 1} 无法加载</span>
  ) : (
    <a href={url} target="_blank" rel="noreferrer" aria-label={`查看图片 ${index + 1}`}>
      <img
        src={url}
        alt={`图片 ${index + 1}`}
        loading="lazy"
        onLoad={onGrowth}
        onError={() => {
          setFailed(true);
          onGrowth();
        }}
      />
    </a>
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
  return (
    <div className="chat-images">
      {resources.map((resource, index) => (
        <Image
          key={resource.resource_id}
          resource={resource}
          workspace={workspace}
          session={session}
          onGrowth={onGrowth}
          index={index}
        />
      ))}
    </div>
  );
}
