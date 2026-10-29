import importlib.util
from pathlib import Path

import pytest


def release_module():
    path = Path(__file__).resolve().parents[2] / "scripts/ci/release.py"
    assert path.is_file(), "Publishing must validate versions and immutable image inputs"
    spec = importlib.util.spec_from_file_location("ci_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", ["latest", "v1.2", "v1.2.3;echo bad", "../main", "v01.2.3"])
def test_rejects_non_version_or_shell_input(value):
    with pytest.raises(ValueError):
        release_module().parse_version(value)


def test_prerelease_never_promotes_latest():
    policy = release_module()
    assert not policy.should_update_latest("1.2.3-rc.1", None)
    assert not policy.should_update_latest("1.2.3", "1.3.0")
    assert policy.should_update_latest("1.3.0", "1.2.3")


def test_digest_records_require_same_commit_and_both_architectures():
    policy = release_module()
    sha = "a" * 40
    records = [{"app": "webui", "arch": arch, "sha": sha, "digest": "sha256:" + char * 64}
               for arch, char in [("amd64", "b"), ("arm64", "c")]]
    assert len(policy.validate_records(records, "webui", sha)) == 2
    with pytest.raises(ValueError):
        policy.validate_records(records[:1], "webui", sha)
    with pytest.raises(ValueError):
        policy.validate_records(records, "webui", "d" * 40)


def test_existing_version_cannot_be_overwritten_with_new_platform_digests():
    policy = release_module()
    expected = ["sha256:" + "a" * 64, "sha256:" + "b" * 64]
    assert policy.same_platforms({"manifests": [{"digest": value} for value in expected]}, expected)
    assert not policy.same_platforms({"manifests": [{"digest": "sha256:" + "c" * 64}]}, expected)


def test_prepare_rejects_tag_that_differs_from_reusable_checks_sha(monkeypatch, tmp_path):
    policy = release_module()
    monkeypatch.setenv("GITHUB_REPOSITORY", "Removel/Icarus")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    monkeypatch.setattr(policy.subprocess, "check_output", lambda *args, **kwargs: "a" * 40)
    with pytest.raises(ValueError, match="same SHA"):
        policy.prepare("v1.2.3")


def test_prerelease_dry_run_never_queries_or_changes_latest(monkeypatch, tmp_path):
    import json

    policy = release_module()
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "digests"
    directory.mkdir()
    for app in policy.APPS:
        for arch in ["amd64", "arm64"]:
            (directory / f"{app}-{arch}.json").write_text(json.dumps({
                "app": app, "arch": arch, "sha": "a" * 40, "digest": "sha256:" + "b" * 64,
            }))
    def inspect(reference):
        assert not reference.endswith(":latest")
        return None
    monkeypatch.setattr(policy, "inspect_manifest", inspect)
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: pytest.fail("dry run must not push"))
    policy.promote("1.2.3-rc.1", "a" * 40, directory, dry_run=True)
    record = json.loads((tmp_path / "release-record.json").read_text())
    assert all(not row["update_latest"] for row in record["applications"])


@pytest.mark.parametrize("error", [
    b"unauthorized: authentication required",
    b"failed to resolve registry: DNS server not found",
    b"unexpected status: 503: backend not found",
])
def test_registry_error_is_not_treated_as_a_missing_tag(monkeypatch, error):
    from types import SimpleNamespace

    policy = release_module()
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stderr=error, stdout=b""))
    with pytest.raises(RuntimeError):
        policy.inspect_manifest("removel/icarus-webui:1.2.3")


@pytest.mark.parametrize("error", [
    b"ERROR: docker.io/removel/icarus-webui:1.2.3: not found",
    b"manifest unknown: manifest unknown",
])
def test_explicit_missing_manifest_allows_creation(monkeypatch, error):
    from types import SimpleNamespace

    policy = release_module()
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stderr=error, stdout=b""))
    assert policy.inspect_manifest("removel/icarus-webui:1.2.3") is None


def test_manifest_digest_uses_exact_registry_bytes(monkeypatch):
    import hashlib
    from types import SimpleNamespace

    policy = release_module()
    raw = b'{\n  "schemaVersion": 2, "manifests": []\n}'
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stderr=b"", stdout=raw))
    manifest = policy.inspect_manifest("removel/icarus-webui:1.2.3")
    assert manifest["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_failed_promotion_keeps_a_record_without_claiming_success(monkeypatch, tmp_path):
    import json
    import subprocess

    policy = release_module()
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "digests"
    directory.mkdir()
    for app in policy.APPS:
        for arch in ["amd64", "arm64"]:
            (directory / f"{app}-{arch}.json").write_text(json.dumps({
                "app": app, "arch": arch, "sha": "a" * 40, "digest": "sha256:" + "b" * 64,
            }))
    monkeypatch.setattr(policy, "inspect_manifest", lambda _: None)
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(policy.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        policy.promote("1.2.3", "a" * 40, directory, dry_run=False)
    record = json.loads((tmp_path / "release-record.json").read_text())
    assert record["status"] == "failed"
    assert all("manifest_digest" not in row for row in record["applications"])
