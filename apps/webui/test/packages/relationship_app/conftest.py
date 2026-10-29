import pytest
from test.rela_service import fake_rela


@pytest.fixture
def rela(page):
    state = fake_rela(page)
    # Exercise the public route independently of the account-menu tests.
    page.goto('/#/people')
    return state
