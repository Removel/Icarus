import '@douyinfe/semi-ui/react19-adapter';
import { useId, type ComponentProps, type ReactNode } from 'react';
import { ArrowDown, Search, X, SearchX } from 'lucide-react';
import { Button, Input, Tag, Tabs as SemiTabs, Modal as SemiModal, Select as SemiSelect } from '@douyinfe/semi-ui';

export { Button, Input, TextArea, Toast, Switch, Tooltip, Progress, Spin } from '@douyinfe/semi-ui';

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

export function PageHeading({ title, description, children }: { eyebrow?: string; title: string; description: string; children?: ReactNode }) {
  return <header className="page-heading"><div><h1>{title}</h1><p>{description}</p></div><div className="heading-actions">{children}</div></header>;
}

export function Status({ children, tone = 'green' }: { children: ReactNode; tone?: 'green' | 'blue' | 'gray' | 'amber' | 'pink' }) {
  const color = { green: 'green', blue: 'blue', gray: 'grey', amber: 'orange', pink: 'blue' } as const;
  return <Tag color={color[tone]} type="light" size="small">{children}</Tag>;
}

export function Tabs({ items, value, onChange }: { items: { id: string; label: string; count?: number }[]; value: string; onChange: (id: string) => void }) {
  return <SemiTabs className="content-tabs" type="line" activeKey={value} onChange={onChange}>{items.map(item => <SemiTabs.TabPane itemKey={item.id} key={item.id} tab={<span>{item.label}{item.count !== undefined && <span className="tab-count">{item.count}</span>}</span>} />)}</SemiTabs>;
}

export function SearchField({ value, onChange, placeholder = '搜索当前列表…' }: { value: string; onChange: (value: string) => void; placeholder?: string }) {
  return <Input className="search-field" prefix={<Search size={16} />} aria-label={placeholder} value={value} onChange={onChange} placeholder={placeholder} showClear />;
}

export function EmptyState({ title = '没有找到匹配的内容', description = '试试其他关键词，或清除筛选条件。', children }: { title?: string; description?: string; children?: ReactNode }) {
  return <div className="empty-state"><SearchX size={28} strokeWidth={1.5} /><h3>{title}</h3><p>{description}</p>{children}</div>;
}

export function DetailHeading({ label, onClose }: { label: string; onClose: () => void }) {
  return <div className="detail-heading"><span>{label}</span><Button theme="borderless" type="tertiary" icon={<X size={17} />} aria-label="关闭详情" onClick={onClose} /></div>;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <div className="form-field"><div className="form-label">{label}</div>{children}{hint && <p className="field-hint">{hint}</p>}</div>;
}

export function TableFooter({ count, noun = '条记录' }: { count: number; noun?: string }) {
  return <div className="table-footer"><span>共 {count} {noun}</span><span className="footer-hint">已显示全部</span></div>;
}

export function SortButton({ descending, onClick }: { descending: boolean; onClick: () => void }) {
  return <button className="text-button sort-button" onClick={onClick}>更新时间<ArrowDown size={13} style={{ transform: descending ? undefined : 'rotate(180deg)' }} /></button>;
}
