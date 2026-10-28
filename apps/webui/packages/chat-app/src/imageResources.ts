export type ImageResource = { resource_id: string; media_type: string };
export const imageTypes = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
export const maxImageBytes = 20 * 1024 * 1024;

export function imageBoxStyle(aspect: number) {
  const ratio = Math.max(0.25, Math.min(4, aspect));
  return { width: 240 * Math.min(1, ratio), aspectRatio: ratio };
}

export function imagePosition(aspect: number) {
  return aspect < 0.25 ? 'center top' : aspect > 4 ? 'left center' : 'center';
}
const reference = /^(?:webui\/[a-f0-9]{32}|assets\/[a-f0-9]{64})\.(?:png|jpg|gif|webp)$/;

export function parseImageResources(value: unknown): ImageResource[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is ImageResource =>
      item &&
      typeof item.resource_id === 'string' &&
      reference.test(item.resource_id) &&
      imageTypes.includes(item.media_type),
  );
}

export function imageUrl(image: ImageResource, workspace = '', session = '') {
  const params = new URLSearchParams({ resource_id: image.resource_id });
  if (image.resource_id.startsWith('assets/')) {
    params.set('workspace_path', workspace);
    params.set('session_id', session);
  }
  return '/resources?' + params;
}
