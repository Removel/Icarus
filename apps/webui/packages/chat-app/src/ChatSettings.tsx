// Adapted from dswui SettingsDialog, SettingsNav and GeneralSection @ 3eaf631.
import { useState, type ReactNode } from 'react';
import { IconClose, IconFile, IconMonitor, IconMoon, IconSettings, IconSun } from './dswui/Icons';
import ChatDialog from './ChatDialog';
import ChatServerSettings from './ChatServerSettings';
import type { Gateway } from './gateway';
import { Bot, SlidersHorizontal } from 'lucide-react';

export type ChatTheme = 'light' | 'dark' | 'system';
export type ManagementPage = 'memory' | 'knowledge';
export type ChatSettingsProps = {
  theme: ChatTheme;
  onThemeChange: (theme: ChatTheme) => void;
  managementPaths: Record<ManagementPage, string>;
  onOpenManagement: (page: ManagementPage) => void;
};

export default function ChatSettings({
  open,
  onClose,
  theme,
  onThemeChange,
  connectionForm,
  enterToSend,
  setEnterToSend,
  fontSize,
  setFontSize,
  wide,
  setWide,
  gateway,
}: ChatSettingsProps & {
  open: boolean;
  onClose: () => void;
  connectionForm: ReactNode;
  enterToSend: boolean;
  setEnterToSend: (value: boolean) => void;
  fontSize: number;
  setFontSize: (value: number) => void;
  wide: boolean;
  setWide: (value: boolean) => void;
  gateway?: Gateway;
}) {
  const [tab, setTab] = useState('general');
  const items = [
    { id: 'general', label: '通用设置', icon: IconSettings },
    { id: 'workspace', label: '工作区', icon: IconFile },
    { id: 'agent', label: 'Agent 与工具', icon: Bot },
    { id: 'models', label: '模型配置', icon: SlidersHorizontal },
  ];
  return (
    <ChatDialog open={open} label="设置" onClose={onClose}>
      <div className="dialog settings-dialog">
        <div className="dialog-head">
          <h2 id="settings-title">系统设置</h2>
          <button className="icon-btn" onClick={onClose} title="关闭设置" aria-label="关闭设置">
            <IconClose />
          </button>
        </div>
        <div className="dialog-body">
          <div className="settings-layout">
            <div className="settings-nav">
              {items.map((item) => (
                <button
                  key={item.id}
                  className={tab === item.id ? 'on' : ''}
                  onClick={() => setTab(item.id)}
                >
                  <item.icon />
                  {item.label}
                </button>
              ))}
            </div>
            <div className="settings-content">
              {tab === 'general' && (
                <>
                  <div className="theme-setting">
                    <div className="txt">主题</div>
                    <div className="theme-choices" role="group" aria-label="主题">
                      {(
                        [
                          { value: 'light', label: '浅色', icon: IconSun },
                          { value: 'dark', label: '深色', icon: IconMoon },
                          { value: 'system', label: '跟随系统', icon: IconMonitor },
                        ] as const
                      ).map((item) => (
                        <button
                          key={item.value}
                          className={`theme-choice ${theme === item.value ? 'on' : ''}`}
                          aria-pressed={theme === item.value}
                          onClick={() => onThemeChange(item.value)}
                        >
                          <item.icon />
                          {item.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="setting-row">
                    <div className="txt">发送方式</div>
                    <div className="seg" role="group" aria-label="发送方式">
                      <button
                        className={enterToSend ? 'on' : ''}
                        onClick={() => setEnterToSend(true)}
                      >
                        Enter 发送
                      </button>
                      <button
                        className={!enterToSend ? 'on' : ''}
                        onClick={() => setEnterToSend(false)}
                      >
                        Ctrl + Enter 发送
                      </button>
                    </div>
                  </div>
                  <label className="setting-row">
                    <span className="txt">对话字号</span>
                    <select
                      className="setting-select"
                      aria-label="对话字号"
                      value={fontSize}
                      onChange={(event) => setFontSize(Number(event.target.value))}
                    >
                      {[14, 16, 18, 20].map((size) => (
                        <option key={size} value={size}>
                          {size} px
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="setting-row">
                    <span className="txt">宽版对话</span>
                    <input
                      type="checkbox"
                      aria-label="宽版对话"
                      checked={wide}
                      onChange={(event) => setWide(event.target.checked)}
                    />
                  </label>
                </>
              )}
              {tab === 'workspace' && connectionForm}
              {(tab === 'agent' || tab === 'models') && (
                <ChatServerSettings gateway={gateway} tab={tab} />
              )}
            </div>
          </div>
        </div>
      </div>
    </ChatDialog>
  );
}
