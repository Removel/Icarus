import asyncio

import pytest

from .helpers import Backend


def setup():
    from agent_app.plugins.relationship.plugin import RelationshipPlugin
    backend = Backend()
    plugin = RelationshipPlugin("relationship", backend)
    return backend, {tool.definition.name: tool for tool in plugin.build_tools()}


def remember(tools, **arguments):
    return tools["relationship_remember"].invoke(arguments, task_id="task", run_id="run")


def test_remember_exact_identity_creates_missing_person():
    backend, tools = setup()
    result = remember(tools, display_name="张三", identities=["qq:123"], profile={"likes": "猫"})
    assert result.success and result.output["created"]
    assert backend.writes == [("create", "p_1", ("qq:123",)), ("profile", "p_1", "likes", "猫", "agent:task:run")]


def test_name_missing_needs_confirmation_then_sequential_repeat_reuses():
    backend, tools = setup()
    result = remember(tools, display_name="张三", profile={"likes": "猫"})
    assert result.success and result.output["status"] == "confirmation_required"
    assert result.output["would_create"] is True
    assert backend.writes == []
    assert remember(tools, display_name="张三", confirm_create=True).output["created"]
    assert remember(tools, display_name="张三", confirm_create=True).output["created"] is False
    assert len([entry for entry in backend.writes if entry[0] == "create"]) == 1


@pytest.mark.parametrize("names", [["张三丰"], ["张三", "张三"]])
def test_fuzzy_or_ambiguous_name_never_writes(names):
    backend, tools = setup()
    for i, name in enumerate(names):
        backend.add(f"p_{i}", name)
    result = remember(tools, display_name="张三", profile={"likes": "猫"}, confirm_create=True)
    assert result.success and result.output["status"] == "confirmation_required"
    assert backend.writes == []


def test_unique_exact_name_can_write_even_with_a_fuzzy_candidate():
    backend, tools = setup()
    backend.add("p_a", "张三")
    backend.add("p_b", "张三丰")
    assert remember(tools, display_name=" 张三 ", profile={"likes": None}).success
    assert backend.writes == [("profile", "p_a", "likes", None, "agent:task:run")]


def test_full_name_alias_evidence_allows_exact_match():
    backend, tools = setup()
    backend.add("p_a", "另一个显示名", names=tuple(f"其他{i}" for i in range(10)) + ("张三",))
    assert remember(tools, display_name="张三", profile={"likes": "猫"}).success
    assert backend.writes[0][1] == "p_a"


def test_unfinished_search_does_not_prove_unique_or_absent():
    backend, tools = setup()
    backend.add()
    backend.search_more = True
    result = remember(tools, display_name="张三", profile={"likes": "猫"})
    assert result.output["status"] == "confirmation_required"
    assert backend.writes == []


def test_new_identity_with_name_candidate_requires_confirmation():
    backend, tools = setup()
    backend.add()
    result = remember(tools, display_name="张三", identities=["qq:123"], profile={"likes": "猫"})
    assert result.output["status"] == "confirmation_required"
    assert backend.writes == []
    result = remember(tools, person_id="p_a", identities=["qq:123"], profile={"likes": "猫"})
    assert result.success
    assert backend.identities["qq:123"] == "p_a"
    assert len(backend.people) == 1


def test_explicit_new_person_confirmation_can_distinguish_namesakes_with_identity():
    backend, tools = setup()
    backend.add()
    result = remember(tools, display_name="张三", identities=["qq:123"], confirm_create=True)
    assert result.success and result.output["created"] is True
    assert len(backend.people) == 2


@pytest.mark.parametrize("status", ["active", "archived"])
def test_person_id_and_other_person_identity_conflict_never_writes(status):
    backend, tools = setup()
    backend.add("p_a")
    backend.add("p_b", "李四", status=status)
    backend.identities["qq:123"] = "p_b"
    result = remember(tools, person_id="p_a", identities=["qq:123"], profile={"likes": "猫"})
    assert result.success is False or result.output["status"] == "confirmation_required"
    assert backend.writes == []


def test_multiple_identities_must_agree_before_any_write():
    backend, tools = setup()
    backend.add("p_a")
    backend.add("p_b", "李四")
    backend.identities.update({"qq:123": "p_a", "wechat:wx": "p_b"})
    result = remember(tools, identities=["qq:123", "wechat:wx"], profile={"likes": "猫"})
    assert result.output["status"] == "confirmation_required"
    assert backend.writes == []


@pytest.mark.parametrize("owner, success", [("p_a", True), ("p_b", False)])
def test_alias_409_only_ignored_after_same_owner_resolution(owner, success):
    backend, tools = setup()
    backend.add("p_a")
    backend.add("p_b", "李四")
    backend.alias_conflict_owner = owner
    result = remember(tools, person_id="p_a", identities=["qq:123"], profile={"likes": "猫"})
    assert result.success is success
    assert any(entry[0] == "profile" for entry in backend.writes) is success


@pytest.mark.parametrize("arguments", [
    {"person_id": "p_a", "display_name": "张三"},
    {"person_id": "p_a", "profile": {"valid": 1, "Bad": 2}},
    {"person_id": "p_a", "profile": {"value": float("nan")}},
    {"person_id": "p_a", "profile": {"value": "x" * 65537}},
    {"display_name": "张三", "confirm_create": 1},
    {"identities": ["name:张三"]},
    {"identities": ["qq:letters"], "display_name": "张三"},
    {"person_id": "p_a", "source": "user"},
    {"person_id": "p_a", "identities": ["qq:1"] * 11},
    {"person_id": "p_a", "profile": {f"key_{i}": i for i in range(51)}},
])
def test_all_arguments_validate_before_write(arguments):
    backend, tools = setup()
    backend.add()
    result = remember(tools, **arguments)
    assert result.success is False
    assert backend.writes == []


def test_identity_normalization_deduplicates_before_atomic_create():
    backend, tools = setup()
    result = remember(tools, display_name="张三", identities=["qq:12-34", "qq:1234", "wechat:WX"])
    assert result.success
    assert backend.writes == [("create", "p_1", ("qq:1234", "wechat:WX"))]


def test_forget_uses_tombstone_and_source_ref():
    backend, tools = setup()
    backend.add()
    result = tools["relationship_forget"].invoke({"person_id": "p_a", "keys": ["likes"]}, task_id="task", run_id="run")
    assert result.success and result.output == {"status": "ok", "deleted": ["likes"]}
    assert backend.writes == [("forget", "p_a", "likes", "agent:task:run")]


@pytest.mark.parametrize("reverse,direction", [(None, "one_way"), ("表弟", "two_way")])
def test_relate_infers_direction_from_reverse(reverse, direction):
    backend, tools = setup()
    arguments = {"from_person_id": "p_a", "to_person_id": "p_b", "kind": "kin", "forward_detail": "表哥"}
    if reverse:
        arguments["reverse_detail"] = reverse
    result = tools["relationship_relate"].invoke(arguments, task_id="task", run_id="run")
    assert result.success and result.output["relation"]["direction"] == direction
    assert backend.writes[0][1]["source_ref"] == "agent:task:run"


def test_relate_invalidation_has_separate_result_and_rejects_mixed_fields():
    backend, tools = setup()
    tool = tools["relationship_relate"]
    assert tool.invoke({"relation_id": "r_1"}).output == {"status": "ok", "invalidated": "r_1"}
    assert not tool.invoke({"relation_id": "r_1", "kind": "friend"}).success
    assert not tool.invoke({"from_person_id": "p_a", "reverse_detail": "弟弟"}).success
    assert backend.writes == [("invalidate", "r_1")]


def test_partial_write_is_not_reported_as_atomic_success():
    backend, tools = setup()
    backend.add()
    backend.fail_key = "second"
    result = remember(tools, person_id="p_a", profile={"first": 1, "second": 2})
    assert not result.success
    assert "partial" in result.error
    assert len(backend.writes) == 1


def test_sync_and_async_have_same_results_and_execution_source():
    backend, tools = setup()
    backend.add()
    tool = tools["relationship_remember"]
    result = asyncio.run(tool.ainvoke({"person_id": "p_a", "profile": {"likes": "猫"}}, task_id="task", run_id="run"))
    assert result.success
    assert backend.writes[0][-1] == "agent:task:run"
    assert remember(tools, person_id="p_a").success
    assert tools["relationship_find"].can_run_parallel({})
    assert not tool.can_run_parallel({})


def test_find_context_and_unknown_person_errors():
    backend, tools = setup()
    backend.add()
    assert tools["relationship_find"].invoke({"query": "张三"}).output["items"][0]["person_id"] == "p_a"
    assert tools["relationship_context"].invoke({"person_id": "p_a"}).success
    assert not tools["relationship_context"].invoke({"person_id": "p_missing"}).success
    assert not tools["relationship_find"].invoke({"query": "张三", "limit": 1}).success
