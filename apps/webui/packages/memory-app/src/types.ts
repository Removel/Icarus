export type MemoryEntry = {
  id: string;
  memory: string;
  user_id: string;
  agent_id: string;
  run_id: string;
  expiration_date: string | null;
  updated_at: string;
  created_at: string;
  metadata: Record<string, unknown> & { category: string };
  history: {
    date: string;
    label: string;
    content?: string;
    details?: string[];
    previous?: string;
  }[];
};

export function isExpired(item: MemoryEntry) {
  return Boolean(
    item.expiration_date && item.expiration_date < new Date().toLocaleDateString('en-CA'),
  );
}

export function memoryDate(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.valueOf())
    ? '时间未知'
    : new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(date);
}
