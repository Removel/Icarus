import { useState } from 'react';
import { Plus, Check, Search, Pencil, ArrowUpRight } from 'lucide-react';
import { Button, Input, Switch, Select, Status, PageHeading, Toast, Field } from '@icarus/ui';

const colors = [
  { name: '主色', variable: '--semi-color-primary' },
  { name: '正文', variable: '--semi-color-text-0' },
  { name: '辅助文字', variable: '--semi-color-text-2' },
  { name: '页面', variable: '--canvas' },
  { name: '内容', variable: '--semi-color-bg-1' },
  { name: '边界', variable: '--semi-color-border' },
];

export default function DesignSystem() {
  const [enabled, setEnabled] = useState(true);
  const [input, setInput] = useState('');
  const [range, setRange] = useState('workspace');
  return <div className="page design-page">
    <PageHeading title="设计规范" context={<span className="heading-count">Semi Universe</span>} />
    <section className="design-section"><h2>颜色与层级</h2><div className="color-grid">{colors.map(color => <div className="color-swatch" key={color.variable}><div style={{ background: `var(${color.variable})` }} /><h3>{color.name}</h3><code>{color.variable}</code></div>)}</div></section>
    <section className="design-section"><h2>控件与反馈</h2><div className="component-grid">
      <div className="component-spec"><h3>操作层级</h3><div className="component-samples"><Button theme="solid" icon={<Plus size={16} />} onClick={() => Toast.success('已完成')}>主要操作</Button><Button onClick={() => Toast.info('已完成')}>次要操作</Button><Button theme="borderless" type="tertiary" onClick={() => Toast.info('已完成')}>文字操作</Button><Button className="icon-button" theme="borderless" type="tertiary" icon={<Pencil size={16} />} title="图标操作" aria-label="规范示例图标操作" onClick={() => Toast.info('已完成')} /></div><span className="spec-note">控件 36 px · 圆角 8 px</span></div>
      <div className="component-spec"><h3>状态</h3><div className="component-samples"><Status>生效中</Status><Status tone="blue">处理中</Status><Status tone="amber">需重试</Status><Status tone="gray">已停用</Status></div><span className="spec-note">状态同时使用文字与标记</span></div>
      <div className="component-spec"><h3>输入与选择</h3><div className="component-samples"><Input aria-label="规范示例输入" prefix={<Search size={16} />} value={input} onChange={setInput} placeholder="搜索内容…" /><Select aria-label="规范示例范围" value={range} onChange={value => setRange(String(value))} optionList={[{ value: 'workspace', label: '工作区' }, { value: 'global', label: '全局' }]} /></div><span className="spec-note">统一高度、焦点和边界</span></div>
      <div className="component-spec"><h3>设置与完成</h3><div className="component-samples"><Switch aria-label="规范示例开关" checked={enabled} onChange={setEnabled} /><span>{enabled ? '已启用' : '已停用'}</span><Check size={16} /><span>已保存</span></div></div>
    </div></section>
    <section className="design-section"><h2>内容优先</h2><div className="design-content-example"><div><span className="spec-note">记忆</span><h3>默认使用中文交流，技术名词保留英文。</h3><span className="spec-note">全局 · lin · 最近更新</span></div><Button className="icon-button" type="tertiary" theme="borderless" icon={<ArrowUpRight size={17} />} aria-label="查看记忆示例" onClick={() => Toast.info('业务页面使用非遮罩详情')} /></div><div className="design-measures"><Field label="正文"><span>14 px / 1.8–2 行高</span></Field><Field label="辅助信息"><span>11–13 px</span></Field><Field label="间距"><span>8 / 12 / 16 / 24 / 32 px</span></Field><Field label="布局"><span>居中内容 · 按需阅读 · 底部导航</span></Field></div></section>
  </div>;
}
