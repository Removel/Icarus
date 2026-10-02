"""Explicit addressing signals; these do not decide whether to respond."""
import unicodedata


def mentions_name(obs, names):
    text = unicodedata.normalize('NFKC', ''.join(p.value for p in obs.content if p.kind == 'text')).casefold()
    return any(unicodedata.normalize('NFKC', name).casefold() in text for name in names if name.strip())


def is_strong_signal(obs, bot_names):
    return not obs.is_self and (obs.subject.kind == 'private' or obs.mentions_bot or mentions_name(obs, bot_names))
