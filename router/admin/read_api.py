"""First slice of the admin API. Mount only behind the trusted Unix socket proxy."""
from pathlib import Path
import secrets

from fastapi import Depends, FastAPI, Header, Request
from pydantic import BaseModel, Field
from typing import Literal
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from .read_models import RoutesView, SubscriptionsView
from .read_store import ReadUnitOfWork


class OperatorRequired(Exception):
    pass


class IncompleteSnapshot(Exception):
    pass


class ProbeRequest(BaseModel):
    scenario: Literal['general', 'gemini', 'ru']
    ids: list[str] = Field(min_length=1, max_length=100)


class MockRequest(BaseModel):
    action: Literal['node', 'route', 'add', 'rename_sub', 'replace_url', 'refresh', 'delete']
    sub_id: str = Field(default='', max_length=200)
    node_id: str = Field(default='', max_length=200)
    pools: dict[str, bool] = Field(default_factory=dict)
    custom_name: str | None = Field(default=None, max_length=200)
    rename: bool = False
    scenario: Literal['general', 'gemini', 'ru'] = 'general'
    primary: str = Field(default='', max_length=200)
    fallbacks: list[str] = Field(default_factory=list, max_length=100)
    version: int = 0
    name: str = Field(default='', max_length=200)
    url: str = Field(default='', max_length=4096)
    tg_url: str = Field(default='', max_length=300)
    confirm: str = Field(default='', max_length=20)


def create_app(engine: Engine, *, operator: str, preview_assets: Path | None = None, probe_service=None, mock_store=None) -> FastAPI:
    if not operator or not operator.isascii() or any(c.isspace() for c in operator):
        raise ValueError('invalid operator configuration')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def require_operator(x_vpn_operator: str | None = Header(default=None)) -> None:
        if x_vpn_operator is None or not secrets.compare_digest(x_vpn_operator.encode(), operator.encode()):
            raise OperatorRequired

    @app.exception_handler(OperatorRequired)
    def not_operator(_request, _exc):
        return JSONResponse({'error': {'code': 'operator_required'}}, status_code=403)

    @app.exception_handler(IncompleteSnapshot)
    def incomplete(_request, _exc):
        return JSONResponse({'error': {'code': 'snapshot_unavailable'}}, status_code=503)

    @app.exception_handler(OperationalError)
    def storage(_request, exc):
        sqlite_code = getattr(exc.orig, 'sqlite_errorcode', 0) or 0
        code = 'storage_busy' if sqlite_code & 0xFF in (5, 6) else 'storage_unavailable'
        return JSONResponse({'error': {'code': code}}, status_code=503)

    @app.get('/api/subs', dependencies=[Depends(require_operator)])
    def subscriptions() -> SubscriptionsView:
        with ReadUnitOfWork(engine) as repo:
            return repo.subscriptions()

    @app.get('/api/routes', dependencies=[Depends(require_operator)])
    def routes() -> RoutesView:
        with ReadUnitOfWork(engine) as repo:
            snapshot = repo.routes()
            if snapshot is None:
                raise IncompleteSnapshot
            return snapshot

    if mock_store is not None:
        @app.post('/api/mock', dependencies=[Depends(require_operator)])
        def mock_action(data: MockRequest, request: Request):
            if request.headers.get('origin') != 'https://test.ai.esmien.ru' or request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'error': 'forbidden'}, status_code=403)
            from .mock_store import MockError
            try:
                if data.action == 'node': mock_store.node(data.sub_id, data.node_id, data.pools, data.custom_name, data.rename)
                elif data.action == 'route': mock_store.route(data.scenario, data.primary, data.fallbacks, data.version)
                elif data.action == 'add': mock_store.add(data.name, data.url, data.tg_url)
                elif data.action == 'rename_sub': mock_store.rename_subscription(data.sub_id, data.name, data.version)
                elif data.action == 'replace_url': mock_store.replace_url(data.sub_id, data.url)
                elif data.action == 'refresh': mock_store.refresh(data.sub_id)
                elif data.action == 'delete': mock_store.delete(data.sub_id, data.confirm)
            except MockError as exc:
                return JSONResponse({'error': str(exc)}, status_code=400)
            return {'ok': True, 'scope': 'mock_only'}

    if probe_service is not None:
        @app.post('/api/routes/probe', dependencies=[Depends(require_operator)])
        def launch_probe(data: ProbeRequest, request: Request):
            if request.headers.get('origin') != 'https://test.ai.esmien.ru' or request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'error': 'forbidden'}, status_code=403)
            if mock_store is not None:
                from sqlalchemy import select
                from .read_store import nodes, own
                with engine.connect() as c:
                    valid = set(c.execute(select(nodes.c.node_id)).scalars()) | set(c.execute(select(own.c.tag)).scalars())
                if not set(data.ids) <= valid:
                    return JSONResponse({'error': 'invalid_selection'}, status_code=400)
            try:
                return {'job_id': probe_service.launch(data.scenario, data.ids)}
            except ValueError:
                return JSONResponse({'error': 'invalid_selection'}, status_code=400)
            except RuntimeError:
                return JSONResponse({'error': 'probe_busy'}, status_code=409)

        @app.get('/api/routes/probe/{job}', dependencies=[Depends(require_operator)])
        def read_probe(job: str):
            result = probe_service.read(job)
            return result if result is not None else JSONResponse({'error': 'not_found'}, status_code=404)

    if preview_assets is not None:
        @app.get('/', dependencies=[Depends(require_operator)], include_in_schema=False)
        def preview():
            return FileResponse(preview_assets / 'read-preview.html', media_type='text/html')

        @app.get('/read-preview.js', dependencies=[Depends(require_operator)], include_in_schema=False)
        def preview_script():
            return FileResponse(preview_assets / 'read-preview.js', media_type='text/javascript')

    return app
