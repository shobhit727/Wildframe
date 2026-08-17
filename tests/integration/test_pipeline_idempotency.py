"""Media-pipeline duplicate-job and retry behavior (idempotency keys).

The audit calls out upload/media-pipeline retry and duplicate-job behavior:
starting the same upload twice with the same idempotency key must yield the
same job, never a duplicate pipeline run.
"""

from __future__ import annotations

import uuid as uuidlib

import httpx
import pytest

from conftest import MEDIA_SERVICE, auth_headers, fetch_content_id, ip_keyed, register_user

pytestmark = pytest.mark.integration


class TestPipelineIdempotency:
    @pytest.fixture()
    def content_id(self, client: httpx.Client, user_a: dict) -> str:
        content_id = fetch_content_id(client, user_a["access_token"])
        if content_id is None:
            pytest.skip("catalog is empty")
        return content_id

    def _start_job(
        self,
        client: httpx.Client,
        user_a: dict,
        upload_session_id: str,
        content_id: str,
        idempotency_key: str,
    ) -> httpx.Response:
        return client.post(
            f"{MEDIA_SERVICE}/pipeline/jobs/{upload_session_id}/start",
            headers=auth_headers(user_a["access_token"]),
            json={
                "content_id": content_id,
                "upload_session_id": upload_session_id,
                "storage_key": f"s3://integration-test/{idempotency_key}.mp4",
                "idempotency_key": idempotency_key,
            },
        )

    def test_same_idempotency_key_returns_same_job(
        self, client: httpx.Client, user_a: dict, content_id: str
    ) -> None:
        upload_session_id = str(uuidlib.uuid4())
        idempotency_key = str(uuidlib.uuid4())

        first = self._start_job(
            client, user_a, upload_session_id, content_id, idempotency_key
        )
        assert first.status_code == 200, first.text
        job_id = first.json()["job_id"]

        replay = self._start_job(
            client, user_a, upload_session_id, content_id, idempotency_key
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["job_id"] == job_id, "replayed start must return the same job"

    def test_distinct_idempotency_keys_create_distinct_jobs(
        self, client: httpx.Client, user_a: dict, content_id: str
    ) -> None:
        first = self._start_job(
            client, user_a, str(uuidlib.uuid4()), content_id, str(uuidlib.uuid4())
        )
        second = self._start_job(
            client, user_a, str(uuidlib.uuid4()), content_id, str(uuidlib.uuid4())
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["job_id"] != second.json()["job_id"]

    def test_job_detail_retrievable(
        self, client: httpx.Client, user_a: dict, content_id: str
    ) -> None:
        response = self._start_job(
            client, user_a, str(uuidlib.uuid4()), content_id, str(uuidlib.uuid4())
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]

        detail = client.get(
            f"{MEDIA_SERVICE}/pipeline/jobs/{job_id}",
            headers=auth_headers(user_a["access_token"]),
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["job"]["job_id"] == job_id
        assert body["job"]["status"] in {"pending", "processing", "completed", "failed"}

    def test_pipeline_requires_authentication(self, client: httpx.Client) -> None:
        response = ip_keyed(
            client, "post", f"{MEDIA_SERVICE}/pipeline/jobs/{uuidlib.uuid4()}/start", json={}
        )
        assert response.status_code == 401


@pytest.fixture(scope="module")
def user_a(client: httpx.Client) -> dict:
    return register_user(client)
