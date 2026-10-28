import { Gateway } from '@icarus/ui/gateway';

export type MemoryContext = {
  user_id: string;
  agent_id: string;
  run_id: string;
  workspace_path?: string;
};

export async function memoryContext(workspace?: string): Promise<MemoryContext> {
  const gateway = new Gateway(
    () => {},
    () => {},
  );
  try {
    const context = await gateway.request<MemoryContext>(
      'memory.get_context',
      workspace ? { workspace_path: workspace } : {},
    );
    if (!context?.user_id || !context.agent_id || !context.run_id)
      throw new Error('无法读取记忆所属用户与作用范围，请检查 Agent 配置。');
    return context;
  } finally {
    gateway.close();
  }
}

export const scopeName = (scope: string) =>
  scope === 'global'
    ? '全局'
    : scope.startsWith('workspace:')
      ? `工作区 · ${scope.slice(10)}`
      : scope || '未指定';
export const validScope = (scope: string) =>
  scope === 'global' || /^workspace:[a-f0-9]{16}$/.test(scope);
