export default function ThinkingWave({ active }: { active: boolean }) {
  return (
    <span className="chat-thinking-wave" data-active={active || undefined} aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}
