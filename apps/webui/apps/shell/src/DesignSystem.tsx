import { useState } from 'react';
import { Plus, Check, Search } from 'lucide-react';
import { Button, Input, Switch, Status, PageHeading, Toast } from '@icarus/ui';

const colors = [
  { name: '主色', variable: '--semi-color-primary', role: '主要操作与当前选中项' },
  { name: '正文', variable: '--semi-color-text-0', role: '标题与正文' },
  { name: '次级文字', variable: '--semi-color-text-2', role: '说明与辅助信息' },
  { name: '页面底色', variable: '--semi-color-fill-0', role: '工作区底色' },
  { name: '内容表面', variable: '--semi-color-bg-1', role: '侧栏、列表与详情' },
  { name: '边界', variable: '--semi-color-border', role: '分隔与控件边界' },
];

export default function DesignSystem() {
  const [enabled, setEnabled] = useState(true);
  const [input, setInput] = useState('');
  return <div className="page design-page">
    <PageHeading title="设计规范" description="工作台布局与 Semi Universe Design 主题的使用示例。" />
    <section className="design-section"><div className="section-title"><h2>颜色与层级</h2><p>页面使用主题变量，业务页面不定义独立配色。</p></div><div className="color-grid">{colors.map(c => <div className="color-swatch" key={c.variable}><div style={{ background: `var(${c.variable})` }} /><h3>{c.name}</h3><p>{c.role}</p><code>{c.variable}</code></div>)}</div></section>
    <section className="design-section"><div className="section-title"><h2>常用组件</h2><p>按钮、表单和状态由 Semi UI 提供。</p></div><div className="component-grid"><div className="component-spec"><h3>操作层级</h3><div className="component-samples"><Button theme="solid" icon={<Plus size={14} />} onClick={() => Toast.success('操作成功')}>主要操作</Button><Button onClick={() => Toast.info('已执行次要操作')}>次要操作</Button><Button theme="borderless" type="tertiary">文字操作</Button></div><p>同一区域只保留一个主要操作。</p></div><div className="component-spec"><h3>状态表达</h3><div className="component-samples"><Status>生效中</Status><Status tone="blue">处理中</Status><Status tone="amber">需重试</Status><Status tone="gray">已停用</Status></div><p>状态由颜色和文字共同表达。</p></div><div className="component-spec"><h3>输入与设置</h3><div className="component-samples"><Input aria-label="规范示例输入" prefix={<Search size={14} />} value={input} onChange={setInput} placeholder="输入关键词" style={{ maxWidth: 220 }} /><Switch aria-label="规范示例开关" checked={enabled} onChange={setEnabled} /></div><p>使用统一的控件尺寸与焦点反馈。</p></div><div className="component-spec"><h3>完成反馈</h3><div className="component-samples"><Check size={16} /><span>操作完成后给出明确反馈</span></div><p>危险操作通过确认弹窗单独处理。</p></div></div></section>
    <section className="design-section"><div className="section-title"><h2>页面布局</h2><p>内容居中呈现，导航悬浮在底部。</p></div><div className="layout-spec"><div className="layout-mini"><div className="layout-mini-main"><span>页面标题 · 主要操作</span><span>分类 · 搜索 · 筛选</span><div><span>连续内容列表</span></div><span>底部应用导航</span></div></div><div className="layout-measures"><p><strong>1140 px</strong>内容最大宽度</p><p><strong>430 px</strong>详情抽屉最大宽度</p><p><strong>16 / 20 / 24 / 32 px</strong>常用间距</p><p><strong>窄屏</strong>单列内容与底部导航</p></div></div></section>
    <footer className="bottom-caption">Icarus WebUI · 交互演示</footer>
  </div>;
}
