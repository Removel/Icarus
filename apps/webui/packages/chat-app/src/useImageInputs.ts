import { useCallback, useEffect, useRef, useState } from 'react';
import {
  imageTypes,
  imageUrl,
  maxImageBytes,
  parseImageResources,
  type ImageResource,
} from './imageResources';

export type DraftImage = {
  id: string;
  name: string;
  preview: string;
  file?: File;
  resource?: ImageResource;
  source?: ImageResource;
  progress: number;
  error?: string;
};

export default function useImageInputs() {
  const [images, setImages] = useState<DraftImage[]>([]);
  const [error, setError] = useState('');
  const entries = useRef<DraftImage[]>([]);
  const requests = useRef(new Map<string, XMLHttpRequest>());
  const urls = useRef(new Map<string, string>());
  const update = useCallback((change: (items: DraftImage[]) => DraftImage[]) => {
    entries.current = change(entries.current);
    setImages(entries.current);
  }, []);
  const clear = useCallback(() => {
    // Remove entries before aborting: stale callbacks cannot restore a removed file.
    update(() => []);
    for (const request of requests.current.values()) request.abort();
    requests.current.clear();
    for (const url of urls.current.values()) URL.revokeObjectURL(url);
    urls.current.clear();
    setError('');
  }, [update]);
  useEffect(() => clear, [clear]);
  const upload = useCallback(
    function uploadImage(id: string) {
      const item = entries.current.find((image) => image.id === id);
      if (!item || (!item.file && !item.source)) return;
      const request = new XMLHttpRequest();
      requests.current.set(id, request);
      update((items) =>
        items.map((image) =>
          image.id === id ? { ...image, error: undefined, progress: 0 } : image,
        ),
      );
      request.open(item.file ? 'POST' : 'GET', item.file ? '/resources' : item.preview);
      request.timeout = 60000;
      if (item.file) request.setRequestHeader('Content-Type', item.file.type);
      else request.responseType = 'blob';
      request.upload.onprogress = (event) => {
        if (event.lengthComputable)
          update((items) =>
            items.map((image) =>
              image.id === id
                ? {
                    ...image,
                    progress: Math.min(99, Math.round((event.loaded / event.total) * 100)),
                  }
                : image,
            ),
          );
      };
      const fail = (message: string) => {
        requests.current.delete(id);
        update((items) =>
          items.map((image) => (image.id === id ? { ...image, error: message } : image)),
        );
      };
      request.onerror = () => fail('上传失败，请重试');
      request.ontimeout = () => fail('上传超时，请重试');
      request.onload = () => {
        if (request.status < 200 || request.status >= 300) {
          fail(
            request.status === 413
              ? '图片超过 20 MiB'
              : request.status === 415
                ? '图片格式或内容不受支持'
                : request.status === 404
                  ? '图片接口不可用，请更新并重启 Gateway'
                  : '上传失败，请重试',
          );
          return;
        }
        try {
          if (!item.file) {
            const file = new File([request.response as Blob], 'image', {
              type: item.source!.media_type,
            });
            update((items) => items.map((image) => (image.id === id ? { ...image, file } : image)));
            uploadImage(id);
            return;
          }
          const resource = parseImageResources([JSON.parse(request.responseText)])[0];
          if (!resource) throw new Error('Invalid resource response');
          requests.current.delete(id);
          update((items) =>
            items.map((image) =>
              image.id === id ? { ...image, resource, progress: 100, error: undefined } : image,
            ),
          );
        } catch {
          fail('上传响应无效，请重试');
        }
      };
      request.send(item.file ?? null);
    },
    [update],
  );
  function add(files: File[]) {
    setError('');
    for (const file of files) {
      if (!imageTypes.includes(file.type)) {
        setError('仅支持 PNG、JPEG、GIF、WebP 图片');
        continue;
      }
      if (!file.size || file.size > maxImageBytes) {
        setError('单张图片须大于 0 且不超过 20 MiB');
        continue;
      }
      if (entries.current.length >= 8) {
        setError('每条消息最多添加 8 张图片');
        break;
      }
      const id = crypto.randomUUID();
      const preview = URL.createObjectURL(file);
      urls.current.set(id, preview);
      update((items) => [
        ...items,
        { id, name: file.name || '粘贴图片', file, preview, progress: 0 },
      ]);
      upload(id);
    }
  }
  const restore = useCallback(
    (resources: ImageResource[], workspace: string, session: string) => {
      const restored = resources
        .filter(
          (resource) =>
            !entries.current.some(
              (item) => (item.source ?? item.resource)?.resource_id === resource.resource_id,
            ),
        )
        .map((resource) => ({
          id: crypto.randomUUID(),
          name: '已上传图片',
          preview: imageUrl(resource, workspace, session),
          source: resource.resource_id.startsWith('assets/') ? resource : undefined,
          resource: resource.resource_id.startsWith('assets/') ? undefined : resource,
          progress: resource.resource_id.startsWith('assets/') ? 0 : 100,
        }));
      update((items) => [...restored, ...items]);
      for (const image of restored) if (image.source) upload(image.id);
    },
    [update, upload],
  );
  function remove(id: string) {
    update((items) => items.filter((image) => image.id !== id));
    requests.current.get(id)?.abort();
    requests.current.delete(id);
    const url = urls.current.get(id);
    if (url) URL.revokeObjectURL(url);
    urls.current.delete(id);
  }
  const consume = useCallback(
    (resources: ImageResource[]) => {
      const ids = new Set(resources.map((resource) => resource.resource_id));
      const consumed = entries.current.filter(
        (image) => image.resource && ids.has(image.resource.resource_id),
      );
      update((items) =>
        items.filter((image) => !image.resource || !ids.has(image.resource.resource_id)),
      );
      for (const image of consumed) {
        const url = urls.current.get(image.id);
        if (url) URL.revokeObjectURL(url);
        urls.current.delete(image.id);
      }
    },
    [update],
  );
  return { images, error, add, remove, retry: upload, clear, restore, consume };
}
