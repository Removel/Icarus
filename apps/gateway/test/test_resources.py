import base64
import pytest
from fastapi.testclient import TestClient

from apps.gateway.src.app import create_app
from apps.gateway.test.test_app import RuntimeStub
from icarus.core.identity import SessionIdentity
from icarus.core.resource_ref import ResourceRef
from icarus.runtime.assets import MAX_IMAGE_BYTES, PersistenceRuntime


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


@pytest.fixture
def images(tmp_path):
    with TestClient(create_app(RuntimeStub(), resource_data_dir=tmp_path / 'data')) as client:
        yield client, tmp_path / 'data'


def test_upload_reference_import_and_saved_image_roundtrip(images, tmp_path):
    client, directory = images
    uploaded = client.post('/resources', content=PNG, headers={'Content-Type': 'image/png'})
    assert uploaded.status_code == 200
    reference = uploaded.json()
    assert reference['resource_id'].startswith('webui/')
    resource = ResourceRef(**reference)
    assert resource.resolve(directory / 'incoming').read_bytes() == PNG
    assert client.get('/resources', params=reference).content == PNG

    runtime = PersistenceRuntime(directory, tmp_path / 'workspace')
    runtime.start()
    with runtime.open_session(session_id='session') as session:
        image = session.import_image(resource.resolve(directory / 'incoming'))
    result = client.get('/resources', params={
        'resource_id': image.source, 'workspace_path': str(tmp_path / 'workspace'), 'session_id': 'session',
    })
    assert result.status_code == 200 and result.content == PNG
    assert result.headers['content-type'] == 'image/png'
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['x-content-type-options'] == 'nosniff'


@pytest.mark.parametrize('media_type,data', [
    ('image/png', PNG), ('image/jpeg', b'\xff\xd8\xffjpeg'),
    ('image/gif', b'GIF89agif'), ('image/webp', b'RIFF\x00\x00\x00\x00WEBPimage'),
])
def test_supported_signatures(images, media_type, data):
    client, _ = images
    result = client.post('/resources', content=data, headers={'Content-Type': media_type})
    assert result.status_code == 200
    assert result.json()['media_type'] == media_type


def test_rejects_unsupported_mismatch_and_oversized_stream(images):
    client, directory = images
    for media_type, content in [('image/svg+xml', b'<svg/>'), ('image/jpeg', PNG), ('image/png', b'not an image'), ('image/png', b'')]:
        assert client.post('/resources', content=content, headers={'Content-Type': media_type}).status_code == 415
    assert client.post('/resources', content=b'x', headers={
        'Content-Type': 'image/png', 'Content-Length': str(MAX_IMAGE_BYTES + 1),
    }).status_code == 413
    assert client.post('/resources', content=iter([PNG, b'x' * MAX_IMAGE_BYTES]), headers={
        'Content-Type': 'image/png',
    }).status_code == 413
    assert not list(directory.glob('incoming/webui/*'))


@pytest.mark.parametrize('resource_id', ['../secret.png', '/secret.png', 'webui/../../secret.png',
    'assets/../secret.png', 'webui/a.svg', 'assets/' + 'a' * 64 + '.html'])
def test_rejects_arbitrary_paths(images, resource_id):
    client, _ = images
    assert client.get('/resources', params={'resource_id': resource_id}).status_code == 400


def test_asset_requires_valid_session_and_stays_in_its_directory(images, tmp_path):
    client, directory = images
    resource_id = 'assets/' + 'a' * 64 + '.png'
    assert client.get('/resources', params={'resource_id': resource_id}).status_code == 400
    params = {'resource_id': resource_id, 'workspace_path': str(tmp_path / 'workspace'), 'session_id': 'session'}
    assert client.get('/resources', params=params).status_code == 404
    identity = SessionIdentity.create(tmp_path / 'workspace', 'session')
    target = directory / 'workspaces' / identity.workspace_key / 'sessions/session' / resource_id
    target.parent.mkdir(parents=True)
    outside = tmp_path / 'outside.png'
    outside.write_bytes(PNG)
    target.symlink_to(outside)
    assert client.get('/resources', params=params).status_code == 404
