"""Coverage for media-pipeline repositories (mocked AsyncSession)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models import PipelineJobStatus
from app.repositories import (
    PipelineJobRepository,
    PipelineStageLogRepository,
    TranscodingJobRepository,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def session():
    return AsyncMock()


def _result(value):
    res = MagicMock()
    res.scalar_one_or_none.return_value = value
    res.scalars.return_value.all.return_value = value or []
    return res


class TestPipelineJobRepository:
    async def test_create(self, session):
        repo = PipelineJobRepository(session)
        job = MagicMock()
        await repo.create(job)
        session.add.assert_called_once_with(job)
        session.flush.assert_awaited_once()

    async def test_get(self, session):
        repo = PipelineJobRepository(session)
        session.execute.return_value = _result("found")
        assert await repo.get(uuid4()) == "found"

    async def test_get_by_upload_session(self, session):
        repo = PipelineJobRepository(session)
        session.execute.return_value = _result(None)
        assert await repo.get_by_upload_session(uuid4()) is None

    async def test_save_updates_timestamp(self, session):
        repo = PipelineJobRepository(session)
        job = MagicMock()
        await repo.save(job)
        assert job.updated_at is not None
        session.flush.assert_awaited_once()

    async def test_list_by_status(self, session):
        repo = PipelineJobRepository(session)
        session.execute.return_value = _result([MagicMock()])
        rows = await repo.list_by_status(PipelineJobStatus.PENDING, limit=5)
        assert len(rows) == 1


class TestPipelineStageLogRepository:
    async def test_record(self, session):
        repo = PipelineStageLogRepository(session)
        log = MagicMock()
        await repo.record(log)
        session.add.assert_called_once_with(log)

    async def test_list_for_job(self, session):
        repo = PipelineStageLogRepository(session)
        session.execute.return_value = _result([MagicMock(), MagicMock()])
        assert len(await repo.list_for_job(uuid4())) == 2


class TestTranscodingJobRepository:
    async def test_create(self, session):
        repo = TranscodingJobRepository(session)
        job = await repo.create(uuid4(), "https://src/file.mp4")
        assert job.content_id
        assert job.source_url == "https://src/file.mp4"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_get_by_content_id(self, session):
        repo = TranscodingJobRepository(session)
        session.execute.return_value = _result("job")
        assert await repo.get_by_content_id(uuid4()) == "job"

    async def test_update_progress_existing(self, session):
        repo = TranscodingJobRepository(session)
        job = MagicMock(progress_percentage=0)
        session.get.return_value = job
        await repo.update_progress(uuid4(), 42)
        assert job.progress_percentage == 42
        session.flush.assert_awaited_once()

    async def test_update_progress_missing(self, session):
        repo = TranscodingJobRepository(session)
        session.get.return_value = None
        result = await repo.update_progress(uuid4(), 42)
        assert result is None
        session.flush.assert_not_awaited()
