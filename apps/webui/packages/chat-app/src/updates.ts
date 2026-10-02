import type { RuntimeUpdate } from './gateway';

export type ChatItem = {
  id: string;
  kind: 'user' | 'assistant' | 'thinking' | 'tool' | 'error';
  text: string;
  label?: string;
  detail?: string;
  final: boolean;
};
export type Transcript = {
  items: ChatItem[];
  seen: Set<number>;
  tasks: string[];
  finished: Set<string>;
};
export const emptyTranscript = (): Transcript => ({
  items: [],
  seen: new Set(),
  tasks: [],
  finished: new Set(),
});

export function applyUpdate(previous: Transcript, update: RuntimeUpdate): Transcript {
  if (update.sequence && previous.seen.has(update.sequence)) return previous;
  const seen = new Set(previous.seen);
  if (update.sequence) seen.add(update.sequence);
  const state = { ...previous, seen };
  const task = update.task_id;
  if (!task) return state;
  const p = update.payload;
  const text = typeof p.text === 'string' ? p.text : '';
  if (update.type === 'task.accepted' || update.type === 'task.started')
    return state.finished.has(task)
      ? state
      : { ...state, tasks: [...new Set([...state.tasks, task])] };
  if (update.type === 'task.finished')
    return {
      ...state,
      tasks: state.tasks.filter((id) => id !== task),
      finished: new Set([...state.finished, task]),
    };
  let item: ChatItem;
  const step = typeof p.step === 'number' && p.step >= 1 ? p.step : 1;
  const key = `${task}:${step}`;
  if (update.type === 'user.message' || update.type === 'user.correction') {
    item = {
      id: `user:${task}:${update.type}:${update.sequence ?? ''}`,
      kind: 'user',
      text,
      final: true,
    };
  } else if (
    [
      'assistant.message',
      'assistant.text_delta',
      'assistant.thinking',
      'assistant.thinking_delta',
    ].includes(update.type)
  ) {
    const kind = update.type.includes('thinking') ? 'thinking' : 'assistant';
    const id = `${kind}:${key}`;
    const existing = state.items.find((item) => item.id === id);
    const delta = update.type.endsWith('_delta');
    if (delta && existing?.final) return state;
    item = { id, kind, text: delta ? (existing?.text ?? '') + text : text, final: !delta };
  } else if (update.type === 'tool.started' || update.type === 'tool.completed') {
    const completed = update.type === 'tool.completed';
    const existing = state.items.find((item) => item.id === `tool:${task}:${p.call_id}`);
    item = {
      id: `tool:${task}:${p.call_id}`,
      kind: 'tool',
      label: String(p.tool_name ?? '工具'),
      text: completed ? (p.success ? '已完成' : '执行失败') : '执行中',
      final: completed,
      detail: completed
        ? typeof (p.error ?? p.output_preview) === 'string'
          ? String(p.error ?? p.output_preview)
          : JSON.stringify(p.output_preview ?? '', null, 2)
        : JSON.stringify(p.arguments ?? {}, null, 2),
    };
    if (!completed && existing?.final) return state;
  } else if (update.type === 'task.error') {
    item = {
      id: `error:${task}`,
      kind: 'error',
      text: String(p.message ?? '执行失败'),
      final: true,
    };
  } else return state;
  return {
    ...state,
    items: state.items.some((row) => row.id === item.id)
      ? state.items.map((row) => (row.id === item.id ? item : row))
      : [...state.items, item],
  };
}
