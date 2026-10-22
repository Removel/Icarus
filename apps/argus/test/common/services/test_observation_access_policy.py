"""Sensitive data is checked before truncation on every observation exit."""

from apps.argus.test.factories import item_factory


def test_access_policy_filters_full_text_metadata_and_scope_permission():
    from apps.argus.src.common.services.observation_access_policy import ObservationAccessPolicy

    policy = ObservationAccessPolicy(['SeCrEt'], permission_for=lambda _: 2)
    assert not policy.allows(item_factory('a' * 25000 + 'ＳＥＣＲＥＴ'))
    assert not policy.allows(item_factory('safe', sender_name='secret person'))
    assert policy.allows(item_factory('safe', is_self=True))
    assert not ObservationAccessPolicy([], permission_for=lambda _: 0).allows(item_factory('safe'))
