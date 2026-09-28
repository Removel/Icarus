export function excerpt(content: string) {
  const paragraph = content.split('\n').find(line => line.trim() && !line.trim().startsWith('#')) ?? '';
  return paragraph.length > 120 ? `${paragraph.slice(0, 120)}…` : paragraph;
}

export default function Reading({ content }: { content: string }) {
  return <div className="reading">{content.split('\n').map((line, index) => {
    if (line.startsWith('### ')) return <h4 key={index}>{line.slice(4)}</h4>;
    if (line.startsWith('## ')) return <h3 key={index}>{line.slice(3)}</h3>;
    if (line.startsWith('# ')) return <h2 key={index}>{line.slice(2)}</h2>;
    if (/^[-*] /.test(line)) return <p className="reading-bullet" key={index}><span aria-hidden="true">•</span>{line.slice(2)}</p>;
    return line.trim() ? <p key={index}>{line}</p> : null;
  })}</div>;
}
