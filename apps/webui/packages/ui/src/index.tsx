import '@douyinfe/semi-ui/react19-adapter';
import { useId, type ComponentProps, type ReactNode } from 'react';
import { ArrowDown, Search, X, SearchX } from 'lucide-react';
import { Button, Input, Tabs as SemiTabs, Modal as SemiModal, Select as SemiSelect } from '@douyinfe/semi-ui';

export { Button, Input, TextArea, Toast, Switch, Tooltip, Progress, Spin, Checkbox } from '@douyinfe/semi-ui';
export { navigate, useHashLocation } from './navigation';

export function Modal({ okText = '确认', cancelText = '取消', okButtonProps, cancelButtonProps, ...props }: ComponentProps<typeof SemiModal>) {
  return <SemiModal {...props} okText={okText} cancelText={cancelText}
    okButtonProps={{ 'aria-label': typeof okText === 'string' ? okText : '确认', ...okButtonProps }}
    cancelButtonProps={{ 'aria-label': typeof cancelText === 'string' ? cancelText : '取消', ...cancelButtonProps }} />;
}

export function Select({ 'aria-label': label, ...props }: ComponentProps<typeof SemiSelect> & { 'aria-label'?: string }) {
  const labelId = useId();
  return <>{label && <span id={labelId} className="visually-hidden">{label}</span>}<SemiSelect {...props} aria-labelledby={props['aria-labelledby'] ?? (label ? labelId : undefined)} /></>;
}

export function BrandMark({ size = 30 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 48 48" fill="currentColor" aria-hidden="true"><path d="M24 4C10 4 7 14 16 22l8 2zM44 24c0-14-10-17-18-8l-2 8zM24 44c14 0 17-10 8-18l-8-2zM4 24c0 14 10 17 18 8l2-8z" /></svg>;
}

export function PageHeading({ title, description, context, children }: { title: string; description?: string; context?: ReactNode; children?: ReactNode }) {
  return <header className="page-heading"><div className="page-heading-main"><div className="page-title-row"><h1>{title}</h1>{context}</div>{description && <p>{description}</p>}</div>{children && <div className="heading-actions">{children}</div>}</header>;
}

export function Status({ children, tone = 'green' }: { children: ReactNode; tone?: 'green' | 'blue' | 'gray' | 'amber' | 'pink' }) {
  return <span className={`status status-${tone === 'pink' ? 'blue' : tone}`}><span className="status-dot" aria-hidden="true" />{children}</span>;
}

export function Tabs({ items, value, onChange }: { items: { id: string; label: string; count?: number }[]; value: string; onChange: (id: string) => void }) {
  return <SemiTabs className="content-tabs" type="line" activeKey={value} onChange={onChange}>{items.map(item => <SemiTabs.TabPane itemKey={item.id} key={item.id} tab={<span>{item.label}{item.count !== undefined && <span className="tab-count">{item.count}</span>}</span>} />)}</SemiTabs>;
}

export function SearchField({ value, onChange, placeholder = '搜索当前列表…' }: { value: string; onChange: (value: string) => void; placeholder?: string }) {
  return <Input className="search-field" prefix={<Search size={16} />} aria-label={placeholder} value={value} onChange={onChange} placeholder={placeholder} showClear />;
}

export function EmptyState({ title = '没有找到匹配的内容', description, children }: { title?: string; description?: string; children?: ReactNode }) {
  return <div className="empty-state"><SearchX size={26} strokeWidth={1.5} aria-hidden="true" /><h3>{title}</h3>{description && <p>{description}</p>}{children}</div>;
}

export function DetailHeading({ label, onClose }: { label: string; onClose: () => void }) {
  return <div className="detail-heading"><span>{label}</span><Button className="icon-button" theme="borderless" type="tertiary" icon={<X size={17} />} aria-label="关闭详情" title="关闭详情" onClick={onClose} /></div>;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <div className="form-field"><div className="form-label">{label}</div>{children}{hint && <p className="field-hint">{hint}</p>}</div>;
}

export function SortButton({ descending, onClick }: { descending: boolean; onClick: () => void }) {
  return <Button type="tertiary" theme="borderless" className="sort-button" icon={<ArrowDown size={15} style={{ transform: descending ? undefined : 'rotate(180deg)' }} />} onClick={onClick} aria-label={descending ? '更新时间：从新到旧' : '更新时间：从旧到新'}>{descending ? '最近更新' : '最早更新'}</Button>;
}
