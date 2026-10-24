// Respect explicit exclusions (br;q=0) even when a wildcard is present.
export function acceptedEncodings(header = '') {
  const qualities = new Map(
    String(header)
      .toLowerCase()
      .split(',')
      .map((part) => {
        const [name, ...parameters] = part.trim().split(';');
        const q = parameters.find((parameter) => parameter.trim().startsWith('q='));
        const quality = q ? Number(q.trim().slice(2)) : 1;
        return [name, Number.isFinite(quality) && quality >= 0 && quality <= 1 ? quality : 0];
      }),
  );
  const quality = (name) => qualities.get(name) ?? qualities.get('*') ?? 0;
  const identity = qualities.get('identity') ?? (qualities.get('*') === 0 ? 0 : 1);
  return ['br', 'gzip', 'identity']
    .filter((name) => (name === 'identity' ? identity : quality(name)) > 0)
    .sort(
      (a, b) =>
        (b === 'identity' ? identity : quality(b)) - (a === 'identity' ? identity : quality(a)),
    );
}
