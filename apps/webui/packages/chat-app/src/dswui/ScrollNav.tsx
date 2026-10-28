interface Props {
  items: { id: string; title: string }[];
  active: string | null;
  onJump: (id: string) => void;
}

export function ScrollNav({ items, active, onJump }: Props) {
  if (items.length <= 1) return null;

  return (
    <nav className="scroll-nav" aria-label="对话目录">
      <div className="scroll-nav-bg" />
      <div className="scroll-nav-list">
        {items.map((item) => (
          <button
            key={item.id}
            className={`scroll-nav-item ${active === item.id ? 'active' : ''}`}
            onClick={() => onJump(item.id)}
            title={item.title}
          >
            <span className="scroll-nav-title">{item.title}</span>
            <span className="scroll-nav-indicator">
              <i />
            </span>
          </button>
        ))}
      </div>
    </nav>
  );
}
