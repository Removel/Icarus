import importlib
import pytest


def budget(**kwargs):
    try:
        cls = importlib.import_module('apps.argus.test.model_budget').ModelBudget
    except ModuleNotFoundError:
        cls = None
    assert cls is not None, 'bounded real-model test budgets are required'
    return cls(**kwargs)


def test_budget_blocks_before_next_upstream_attempt():
    value = budget(agent=3, scenario=2, semantic=1, safety=1)
    value.consume('agent')
    value.consume('agent')
    with pytest.raises(ValueError, match='scenario'):
        value.consume('agent')
    value.new_scenario()
    value.consume('agent')
    with pytest.raises(ValueError, match='agent'):
        value.consume('agent')
    value.consume('semantic')
    with pytest.raises(ValueError, match='semantic'):
        value.consume('semantic')
    assert value.counts == {'agent': 3, 'semantic': 1, 'safety': 0}


def test_auth_failure_stops_without_paid_retry_loop():
    value = budget()
    value.consume('semantic')
    value.stop('authentication_failed')
    for kind in ('agent', 'semantic', 'safety'):
        with pytest.raises(ValueError, match='authentication_failed'):
            value.consume(kind)
    assert value.counts == {'agent': 0, 'semantic': 1, 'safety': 0}
