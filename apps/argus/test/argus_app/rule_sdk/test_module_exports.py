import pytest

from argus_app.rule_sdk import RuleEngine
from argus_app.rule_sdk.engine import RuleError
from argus_app.rule_sdk.checks import check_source


@pytest.mark.parametrize('member', ['enum', 'sys', 'builtins'])
async def test_module_alias_cannot_reach_real_modules_or_worker_emitter(member):
    source = f'''import re

def main(event, channel):
    alias = re
    return {{"passed": alias.{member} is not None, "reason": "must be blocked"}}
'''
    # Even paths missed by syntactic alias tracking hit an export facade at runtime.
    assert check_source(source).passed
    async def call(name, args):
        raise AssertionError('No capability should be called')
    with pytest.raises(RuleError, match='has no attribute'):
        await RuleEngine().run(source, {}, {}, call)


async def test_safe_exports_support_direct_from_import_and_helpers():
    source = '''from json import dumps
from math import sqrt

def main(event, channel):
    return {"passed": sqrt(16) == 4, "reason": dumps({"ok": True})}
'''
    async def call(name, args):
        raise AssertionError('No capability call')
    result = await RuleEngine().run(source, {}, {}, call)
    assert result == {'passed': True, 'reason': '{"ok": true}'}
