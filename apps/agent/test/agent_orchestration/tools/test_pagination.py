import pytest

from apps.agent.src.agent_orchestration.tools.pagination import (
    page_number,
    page_size,
    result_limit,
    slice_page,
)


def test_pagination_defaults_and_stable_slice():
    assert page_number(None) == 1
    assert page_size(None) == 50
    page, has_more = slice_page(list(range(120)), page_num=2, page_size=50)
    assert page == list(range(50, 100))
    assert has_more is True


@pytest.mark.parametrize("value", [0, -1, True, 201])
def test_page_size_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        page_size(value)


def test_result_limit_uses_same_bounded_validation():
    assert result_limit(None) == 50
    assert result_limit(200) == 200
    with pytest.raises(ValueError):
        result_limit(201)
