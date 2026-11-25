from apps.tui.src.event_pipeline import UpdateWebSources, create_default_projector_registry
from packages.gateway_protocol import RuntimeUpdateModel


def event(sources, task_id="task"):
    return RuntimeUpdateModel(workspace_key="w", session_id="s", task_id=task_id,
        type="web_search.updated", payload=dict(status="complete", sources=sources),
        occurred_at="2026-11-06T00:00:00Z")


def test_valid_sources_are_explicit_task_local_actions():
    registry = create_default_projector_registry()
    sources = [dict(citeIndex=index, url="https://example.com", title="来源") for index in [2, 1, 2, True, 0, -1, "3"]]
    sources += [None, {}, dict(citeIndex=5, url="file:///tmp", title="invalid")]
    assert registry.project(event(sources), active_task_id="task") == (UpdateWebSources("task", (1, 2)),)
    assert registry.project(event(sources), active_task_id="other") == ()
    assert registry.project(event(sources, None), active_task_id="task") == ()
    assert registry.project(event(None), active_task_id="task") == ()
    assert registry.project(event([]), active_task_id=None, include_unrelated=True, historical=True) == (
        UpdateWebSources("task", ()),
    )
