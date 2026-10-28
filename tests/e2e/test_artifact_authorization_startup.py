# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Persisted custom artifacts remain authorized without a plugin manager."""

import asyncio

import httpx
import pytest
from sqlalchemy import insert

from powercontext.builtin.persistence.artifacts import RepositoryArtifactDraft
from powercontext.builtin.persistence.custom_artifacts import StoredArtifactContent, custom_artifact_repository
from powercontext.builtin.persistence.tables import ARTIFACT_FAMILY_SCHEMAS_TABLE
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import AccessAuditContext, AccessRole, CreateBinding, PrincipalRef, ResourceRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.profiles import ARTIFACT_FAMILY_PROFILES
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, DashboardConfig, McpConfig, MetricsConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_atomic_memory_search_authorization_snapshot import _isolated_database


@pytest.mark.parametrize(
    "backend,role,mode",
    [("sqlite", "all", "global"), ("sqlite", "all", "dedicated"), ("oceanbase", "api", "global")],
)
def test_custom_artifact_authorization_survives_server_restart(tmp_path, monkeypatch, backend, role, mode):
    async def scenario():
        family = "startup-report"
        admin = PrincipalRef(type="service", id="admin")
        reader = PrincipalRef(type="user", id="reader")
        async with _isolated_database(backend, tmp_path) as database:
            settings = ServerSettings(
                database=database,
                workspace=tmp_path,
                access=AccessControlConfig(mode="enforced"),
                runtime=RuntimeConfig(
                    artifact_processing_families=(),
                    artifact_processing_role=role,
                    artifact_processing_supervisor_mode=mode,
                ),
                dashboard=DashboardConfig(enabled=False),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            )
            await asyncio.to_thread(prepare_server_database, settings, evidence_dir=tmp_path / "evidence")
            async with open_builtin_access_control(database, bootstrap_administrators=(admin,)) as access:

                def application(principal):
                    return create_server_app(
                        settings=settings,
                        scheduler_path=tmp_path / "scheduler.db",
                        access_control=access,
                        authentication_provider=StaticBearerAuthenticationProvider("test-token", principal),
                    )

                app = application(admin)
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer test-token"},
                    ) as client,
                ):
                    scopes = []
                    for name in ("visible", "forbidden"):
                        response = await client.post(
                            "/v1/scopes", json={"title": name, "summary": name, "idempotency_key": name}
                        )
                        assert response.status_code == 201, response.text
                        scopes.append(response.json()["scope_id"])
                    async with app.state.application._records().database.transaction() as connection:
                        await connection.execute(
                            insert(ARTIFACT_FAMILY_SCHEMAS_TABLE).values(
                                family=family,
                                schema_version="1",
                                declaration={"family": family, "schema_version": "1"},
                            )
                        )
                        for scope in scopes:
                            await custom_artifact_repository(family).create(
                                connection,
                                scope,
                                "report",
                                RepositoryArtifactDraft(
                                    family=family,
                                    content=StoredArtifactContent(schema_version="1", content={"text": "retained"}),
                                ),
                            )
                    await access.create_binding(
                        admin,
                        CreateBinding(
                            subject=reader,
                            resource=ResourceRef.scope(scopes[0]),
                            role=AccessRole.SCOPE_VIEWER,
                            idempotency_key="reader",
                        ),
                        context=AccessAuditContext(transport="test", operation="artifact-startup"),
                    )
                # Model an empty process-local registry on restart, preserving built-in profiles.
                monkeypatch.delitem(ARTIFACT_FAMILY_PROFILES, family, raising=False)
                app = application(reader)
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer test-token"},
                    ) as client,
                ):
                    path = f"/v1/scopes/{scopes[0]}/artifacts/{family}/report"
                    for suffix in ("", "/revisions/1"):
                        response = await client.get(path + suffix)
                        assert response.status_code == 200, response.text
                        assert response.json()["content"] == {"text": "retained"}
                        assert response.json()["revision"] == 1
                    denied = await client.get(f"/v1/scopes/{scopes[1]}/artifacts/{family}/report")
                    assert denied.status_code == 403, denied.text
                    unknown = await client.get(f"/v1/scopes/{scopes[0]}/artifacts/unregistered-family/report")
                    assert unknown.status_code in {404, 422}, unknown.text

    asyncio.run(scenario())
