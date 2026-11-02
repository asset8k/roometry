"""Read-only readiness against an isolated SQLite engine, never production."""
from threading import Event
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import event, inspect

from roometry.config import Settings
from roometry.main import create_app

pytestmark = pytest.mark.anyio

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.fixture
async def probe(tmp_path):
    app = create_app(Settings(_env_file=None, app_environment='test',
        database_url=f'sqlite+pysqlite:///{tmp_path / "probe.db"}',
        keepalive_token='test-only-monitor-token', upload_storage_dir=tmp_path / 'uploads'))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield app, client

async def test_authentication_rejections_never_access_database(probe):
    app, client = probe
    app.state.database.keepalive = Mock(side_effect=AssertionError('must not query'))
    for token in [None, '', 'incorrect', 'é']:
        # HTTP headers are ASCII; percent-like malformed values must still reject.
        response = await client.get('/internal/keepalive', headers={} if token is None else {'X-Keepalive-Token': token.encode().hex() if token == 'é' else token})
        assert response.status_code == 403
        assert response.json() == {'detail': 'Forbidden'}
        assert response.headers['cache-control'] == 'no-store'
    app.state.settings.keepalive_token = None
    assert (await client.get('/internal/keepalive', headers={'X-Keepalive-Token':'test-only-monitor-token'})).status_code == 403
    app.state.database.keepalive.assert_not_called()

async def test_real_read_and_public_health_are_separate(probe):
    app, client = probe
    queries = []
    event.listen(app.state.database.engine, 'before_cursor_execute', lambda conn, cursor, statement, *args: queries.append(statement))
    response = await client.get('/internal/keepalive', headers={'X-Keepalive-Token':'test-only-monitor-token'})
    assert response.status_code == 200
    assert response.json() == {'status':'ok','database':'ok'}
    assert response.headers['cache-control'] == 'no-store'
    assert queries == ['SELECT 1']
    assert inspect(app.state.database.engine).get_table_names() == []
    app.state.database.keepalive = Mock(side_effect=RuntimeError('private connection details'))
    assert (await client.get('/api/health')).json() == {'status':'ok','service':'roometry'}
    app.state.database.keepalive.assert_not_called()

@pytest.mark.parametrize('missing', [False, True])
async def test_failure_is_generic(probe, missing):
    app, client = probe
    if missing:
        database = app.state.database
        app.state.database = None
    else:
        app.state.database.keepalive = Mock(side_effect=RuntimeError('SECRET db host/password'))
    response = await client.get('/internal/keepalive', headers={'X-Keepalive-Token':'test-only-monitor-token'})
    assert response.status_code == 503
    assert response.json() == {'detail':'Service unavailable'}
    assert response.headers['cache-control'] == 'no-store'
    if missing:
        app.state.database = database

async def test_probe_deadline_and_busy_probe_are_bounded(probe, monkeypatch):
    app, client = probe
    import roometry.keepalive as module
    monkeypatch.setattr(module, 'PROBE_TIMEOUT_SECONDS', .03)
    released, finished = Event(), Event()
    def stalled():
        try:
            released.wait(2)
        finally:
            finished.set()
    app.state.database.keepalive = stalled
    try:
        response = await client.get('/internal/keepalive', headers={'X-Keepalive-Token':'test-only-monitor-token'})
        assert response.status_code == 503
        # A still-running driver call cannot spawn additional probes.
        assert app.state.keepalive_lock.locked()
        assert (await client.get('/internal/keepalive', headers={'X-Keepalive-Token':'test-only-monitor-token'})).status_code == 503
    finally:
        released.set()
        assert finished.wait(2)

async def test_actual_sql_execution_error_is_generic(probe):
    app, client = probe
    from sqlalchemy.exc import OperationalError
    def fail(conn, cursor, statement, *args):
        raise OperationalError(statement, {}, RuntimeError('private driver data'))
    event.listen(app.state.database.engine, 'before_cursor_execute', fail)
    response = await client.get('/internal/keepalive', headers={'X-Keepalive-Token':'test-only-monitor-token'})
    assert response.status_code == 503
    assert response.json() == {'detail':'Service unavailable'}


def test_postgres_probe_has_driver_pool_and_statement_deadlines(monkeypatch):
    from unittest.mock import MagicMock
    from roometry.database import Database
    import roometry.database as module
    engine = MagicMock()
    engine.dialect.name = 'postgresql'
    factory = Mock(return_value=engine)
    monkeypatch.setattr(module, 'create_engine', factory)
    database = Database('postgresql://test:unused@localhost/isolated')
    options = factory.call_args.kwargs
    assert options['pool_timeout'] == 5
    assert options['pool_pre_ping'] is True
    assert options['connect_args']['connect_timeout'] == 5
    assert options['connect_args']['tcp_user_timeout'] == 10000
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = 1
    database.keepalive()
    assert [str(call.args[0]) for call in connection.execute.call_args_list] == [
        "SET LOCAL statement_timeout = '3000ms'", 'SET TRANSACTION READ ONLY', 'SELECT 1']

async def test_connection_pool_exhaustion_returns_503(probe, tmp_path):
    from sqlalchemy import create_engine
    app, client = probe
    original = app.state.database.engine
    isolated = create_engine(f'sqlite+pysqlite:///{tmp_path / "pool.db"}',
                             pool_size=1, max_overflow=0, pool_timeout=.02,
                             connect_args={'check_same_thread': False})
    app.state.database.engine = isolated
    try:
        with isolated.connect():
            response = await client.get('/internal/keepalive', headers={'X-Keepalive-Token':'test-only-monitor-token'})
            assert response.status_code == 503
            assert response.json() == {'detail':'Service unavailable'}
    finally:
        app.state.database.engine = original
        isolated.dispose()

async def test_empty_configuration_disables_probe(probe):
    from pydantic import SecretStr
    app, client = probe
    app.state.settings.keepalive_token = SecretStr('')
    app.state.database.keepalive = Mock(side_effect=AssertionError('must not query'))
    response = await client.get('/internal/keepalive', headers={'X-Keepalive-Token':'test-only-monitor-token'})
    assert response.status_code == 403
    app.state.database.keepalive.assert_not_called()
