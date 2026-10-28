export type Page<T> = { items: T[]; has_more: boolean; next_cursor: string | null };
export type Alias = { alias_id: string; namespace: string; value: string; display_value?: string };
export type Person = {
  person_id: string;
  display_name: string;
  kind: 'user' | 'agent' | 'human' | 'virtual';
  status: 'active' | 'archived';
  aliases?: Alias[];
};
export type ProfileField = {
  id: number;
  attr_key: string;
  attr_value: unknown;
  status: 'active' | 'deleted';
  source: string;
  source_ref?: string | null;
  recorded_at: string;
};
export type Circle = {
  circle_id: string;
  name: string;
  kind: string;
  status: 'active' | 'archived';
  owner_person_id?: string | null;
};
export type Membership = {
  joined_at?: string | null;
  person_id?: string;
  circle_id?: string;
  person?: Person;
  circle?: Circle;
  alias?: string | null;
  role: string;
  identity_label?: string | null;
  status: 'active' | 'left';
};
export type Relation = {
  relation_id: string;
  from_person_id: string;
  to_person_id: string;
  kind: string;
  direction: 'one_way' | 'two_way';
  forward_detail: string;
  reverse_detail?: string | null;
  scope_circle_id?: string | null;
  status: 'active' | 'invalid';
  source: string;
  source_ref?: string | null;
  other_person?: Person;
  relative_direction?: 'outgoing' | 'incoming';
  detail?: string | null;
  detail_missing?: boolean;
};
export const kindNames = { user: '用户', agent: '助手', human: '人物', virtual: '虚拟人物' };
export const aliasText = (alias: Alias) => alias.display_value || alias.value;
export const jsonText = (value: unknown) =>
  typeof value === 'string' ? value : JSON.stringify(value, null, 2);
export const dateText = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN');
};

export function shortPersonId(id: string, ids: string[]) {
  for (let size = 4; size * 2 + 1 < id.length; size++) {
    const shortened = id.slice(0, size) + '…' + id.slice(-size);
    if (
      !ids.some(
        (other) => other !== id && other.slice(0, size) + '…' + other.slice(-size) === shortened,
      )
    )
      return shortened;
  }
  return id;
}
