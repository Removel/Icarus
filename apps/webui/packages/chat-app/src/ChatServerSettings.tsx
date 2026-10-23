import { useEffect, useState } from 'react';
import { Gateway } from './gateway';
import { Select } from './dswui/Select';

type Model = {
  name: string;
  model_name: string;
  context_window: number;
  max_tokens: number;
  temperature: number;
  default_think_level: string;
};
type Settings = {
  agent: { max_steps: number; tool_execution: Record<string, number> };
  skill: { allow_produce: boolean; allow_evolve: boolean };
  model_settings: Model[];
  model_roles: Record<string, string>;
};
type Snapshot = { revision: string; settings: Settings };

export default function ChatServerSettings({ gateway, tab }: { gateway?: Gateway; tab: string }) {
  const [connection, setConnection] = useState<Gateway>();
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [draft, setDraft] = useState<Settings>();
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let stale = false;
    const client =
      gateway ??
      new Gateway(
        () => {},
        () => {
          if (!stale) setMessage('连接已断开，请重新载入配置。');
        },
      );
    setMessage('');
    setSnapshot(undefined);
    setDraft(undefined);
    void client.ready
      .then(() => client.request<Snapshot>('settings.get', {}))
      .then((value) => {
        if (!stale) {
          setConnection(client);
          setSnapshot(value);
          setDraft(value.settings);
        }
      })
      .catch((error: Error) => {
        if (!stale) setMessage(error.message);
      });
    return () => {
      stale = true;
      if (!gateway) client.close();
    };
  }, [gateway, reload]);
  const numberField = (
    label: string,
    value: number,
    change: (value: number) => void,
    options: { min?: number; max?: number; step?: number } = {},
  ) => (
    <label className="setting-row" key={label}>
      <span className="txt">{label}</span>
      <input
        className="setting-input"
        aria-label={label}
        type="number"
        required
        min={options.min ?? 1}
        max={options.max}
        step={options.step ?? 1}
        value={value}
        onChange={(event) => change(event.target.valueAsNumber)}
      />
    </label>
  );
  if (!draft || !snapshot)
    return (
      <div className="settings-server-state">
        <p role="status">{message || '正在读取服务端配置…'}</p>
        {message && (
          <button className="btn btn-ghost" onClick={() => setReload((value) => value + 1)}>
            重新载入
          </button>
        )}
      </div>
    );
  const patchTool = (key: string, value: number) =>
    setDraft({
      ...draft,
      agent: { ...draft.agent, tool_execution: { ...draft.agent.tool_execution, [key]: value } },
    });
  const patchModel = (index: number, fields: Partial<Model>) =>
    setDraft({
      ...draft,
      model_settings: draft.model_settings.map((model, i) =>
        i === index ? { ...model, ...fields } : model,
      ),
    });
  const dirty = JSON.stringify(draft) !== JSON.stringify(snapshot.settings);
  return (
    <form
      className="settings-server-form"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!connection) return;
        setSaving(true);
        setMessage('');
        try {
          const value = await connection.request<Snapshot>('settings.update', {
            revision: snapshot.revision,
            settings: draft,
          });
          setSnapshot(value);
          setDraft(value.settings);
          setMessage('已保存，新建或重新加载会话后生效。');
        } catch (error) {
          setMessage(error instanceof Error ? error.message : '保存失败');
        } finally {
          setSaving(false);
        }
      }}
    >
      <p className="settings-section-hint">
        与终端共用服务端配置。保存后用于新建或重新加载的会话，当前任务继续执行。
      </p>
      {tab === 'agent' ? (
        <>
          {numberField('最大执行步数', draft.agent.max_steps, (value) =>
            setDraft({ ...draft, agent: { ...draft.agent, max_steps: value } }),
          )}
          {numberField(
            '每批最多工具调用',
            draft.agent.tool_execution.max_calls_per_batch,
            (value) => patchTool('max_calls_per_batch', value),
          )}
          {numberField(
            '默认工具超时（秒）',
            draft.agent.tool_execution.default_timeout_seconds,
            (value) => patchTool('default_timeout_seconds', value),
          )}
          {numberField(
            '最长工具超时（秒）',
            draft.agent.tool_execution.max_timeout_seconds,
            (value) => patchTool('max_timeout_seconds', value),
          )}
          {numberField(
            '默认工具输出 Token',
            draft.agent.tool_execution.default_output_tokens,
            (value) => patchTool('default_output_tokens', value),
          )}
          {numberField(
            '每批工具输出 Token',
            draft.agent.tool_execution.batch_output_tokens,
            (value) => patchTool('batch_output_tokens', value),
          )}
          {(
            [
              { key: 'allow_produce', label: '允许生成 Skill' },
              { key: 'allow_evolve', label: '允许演化 Skill' },
            ] as const
          ).map((item) => (
            <label className="setting-row" key={item.key}>
              <span className="txt">{item.label}</span>
              <input
                type="checkbox"
                aria-label={item.label}
                checked={draft.skill[item.key]}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    skill: { ...draft.skill, [item.key]: event.target.checked },
                  })
                }
              />
            </label>
          ))}
        </>
      ) : (
        <>
          <p className="settings-section-hint">
            模型使用服务端已有的连接与凭据；API Key 由服务端环境管理。
          </p>
          <div className="settings-model-roles">
            {Object.entries(draft.model_roles).map(([role, name]) => (
              <label className="setting-row" key={role}>
                <span className="txt">
                  {{
                    agent: '对话模型',
                    compaction: '上下文压缩',
                    skill_generation: 'Skill 生成',
                    title: '标题生成',
                  }[role] ?? role}
                </span>
                <Select
                  label={`模型角色 ${role}`}
                  value={name}
                  onChange={(value) =>
                    setDraft({
                      ...draft,
                      model_roles: { ...draft.model_roles, [role]: String(value) },
                    })
                  }
                  options={draft.model_settings.map((model) => ({
                    value: model.name,
                    label: model.name,
                  }))}
                />
              </label>
            ))}
          </div>
          {draft.model_settings.map((model, index) => (
            <fieldset className="settings-model-card" key={index}>
              <legend>模型 {index + 1}</legend>
              <label className="field">
                名称
                <input
                  type="text"
                  aria-label={`模型 ${index + 1} 名称`}
                  required
                  value={model.name}
                  onChange={(event) => patchModel(index, { name: event.target.value })}
                />
              </label>
              <label className="field">
                模型 ID
                <input
                  type="text"
                  aria-label={`模型 ${index + 1} ID`}
                  required
                  value={model.model_name}
                  onChange={(event) => patchModel(index, { model_name: event.target.value })}
                />
              </label>
              {numberField(`模型 ${index + 1} 上下文窗口`, model.context_window, (value) =>
                patchModel(index, { context_window: value }),
              )}
              {numberField(`模型 ${index + 1} 最大输出 Token`, model.max_tokens, (value) =>
                patchModel(index, { max_tokens: value }),
              )}
              {numberField(
                `模型 ${index + 1} 温度`,
                model.temperature,
                (value) => patchModel(index, { temperature: value }),
                { min: 0, max: 2, step: 0.1 },
              )}
              <label className="setting-row">
                <span className="txt">思考强度</span>
                <Select
                  label={`模型 ${index + 1} 思考强度`}
                  value={model.default_think_level}
                  onChange={(value) => patchModel(index, { default_think_level: String(value) })}
                  options={['low', 'medium', 'high', 'max'].map((level) => ({
                    value: level,
                    label: level,
                  }))}
                />
              </label>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={draft.model_settings.length === 1}
                onClick={() =>
                  setDraft({
                    ...draft,
                    model_settings: draft.model_settings.filter((_, i) => i !== index),
                  })
                }
              >
                移除模型
              </button>
            </fieldset>
          ))}
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() =>
              setDraft({
                ...draft,
                model_settings: [
                  ...draft.model_settings,
                  {
                    name: '',
                    model_name: '',
                    context_window: 65536,
                    max_tokens: 8192,
                    temperature: 0.6,
                    default_think_level: 'medium',
                  },
                ],
              })
            }
          >
            添加模型
          </button>
        </>
      )}
      {message && (
        <p className="settings-section-hint" role="status">
          {message}
        </p>
      )}
      <div className="settings-save-actions">
        <button
          type="button"
          className="btn btn-ghost"
          disabled={saving}
          onClick={() => setReload((value) => value + 1)}
        >
          重新载入
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={!dirty || saving}
          onClick={() => {
            setDraft(snapshot.settings);
            setMessage('');
          }}
        >
          放弃修改
        </button>
        <button type="submit" className="btn btn-primary" disabled={!dirty || saving}>
          {saving ? '保存中…' : '保存配置'}
        </button>
      </div>
    </form>
  );
}
