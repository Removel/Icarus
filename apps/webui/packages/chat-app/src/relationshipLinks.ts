const toolNames = new Set([
  'relationship_find',
  'relationship_context',
  'relationship_remember',
  'relationship_forget',
  'relationship_relate',
]);
type PersonLink = { id: string; name: string };

/** Output previews can be cropped. Only complete, known JSON shapes produce links. */
export function relationshipLinks(
  tool: string | undefined,
  detail: string | undefined,
): PersonLink[] {
  if (!tool || !toolNames.has(tool) || !detail) return [];
  let value: Record<string, unknown>;
  try {
    value = JSON.parse(detail);
  } catch {
    return [];
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  const result = new Map<string, PersonLink>();
  function add(person: unknown) {
    if (!person || typeof person !== 'object' || Array.isArray(person)) return;
    const item = person as Record<string, unknown>;
    if (typeof item.person_id !== 'string' || !/^[A-Za-z0-9_.-]{1,128}$/.test(item.person_id))
      return;
    result.set(item.person_id, {
      id: item.person_id,
      name: typeof item.display_name === 'string' ? item.display_name : item.person_id,
    });
  }
  add(value.person);
  if (tool === 'relationship_find' && Array.isArray(value.items)) value.items.forEach(add);
  if (Array.isArray(value.candidates)) value.candidates.forEach(add);
  if (tool === 'relationship_relate' && value.relation && typeof value.relation === 'object') {
    const relation = value.relation as Record<string, unknown>;
    add({ person_id: relation.from_person_id });
    add({ person_id: relation.to_person_id });
  }
  return [...result.values()];
}
