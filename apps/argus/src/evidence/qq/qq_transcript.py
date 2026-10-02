"""Render observations without fetching or deciding anything."""

def transcript(messages):
    return '\n'.join(
        ('[机器人] ' if getattr(m,'is_self',False) else '')
        + f'{m.actor.display_name or m.actor.external_id}: '
        + ''.join(p.value if p.kind=='text' else '[图片]' if p.kind=='image' else f'[{p.value}]'
                  for p in m.content)
        for m in messages)


