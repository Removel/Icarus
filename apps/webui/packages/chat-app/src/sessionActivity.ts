import type { Session } from './gateway';

export function sessionTime(session: Session): number {
  const updated = Date.parse(session.updated_at ?? '');
  return Number.isFinite(updated) ? updated : Date.parse(session.created_at ?? '');
}

export function sortSessions(sessions: Session[]): Session[] {
  return sessions
    .map((session, index) => ({ session, index }))
    .sort((a, b) => {
      const left = sessionTime(a.session);
      const right = sessionTime(b.session);
      if (!Number.isFinite(left)) return Number.isFinite(right) ? 1 : a.index - b.index;
      if (!Number.isFinite(right)) return -1;
      return right - left || a.session.session_id.localeCompare(b.session.session_id);
    })
    .map(({ session }) => session);
}

export function touchSession(sessions: Session[], id: string, occurredAt: string): Session[] {
  const time = Date.parse(occurredAt);
  if (!Number.isFinite(time)) return sessions;
  const session = sessions.find((row) => row.session_id === id);
  if (!session || sessionTime(session) >= time) return sessions;
  return sessions.map((row) => (row === session ? { ...row, updated_at: occurredAt } : row));
}
