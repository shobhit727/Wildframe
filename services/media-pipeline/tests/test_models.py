import os
from contextlib import ExitStack
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from app.models import (
    Base,
    DeliveryProtocol,
    PipelineJob,
    PipelineJobStatus,
    PipelineStageLog,
    PipelineStageStatus,
    StreamingQualityProfile,
    TranscodingJob,
    TranscodingStatus,
    VideoManifest,
)


@pytest.fixture
def session():
    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            postgres = stack.enter_context(PostgresContainer("postgres:15"))
            url = postgres.get_connection_url()
        engine = create_engine(make_url(url).set(drivername="postgresql+psycopg2"))
        stack.callback(engine.dispose)
        Base.metadata.create_all(engine)
        connection = stack.enter_context(engine.connect())
        transaction = connection.begin()
        stack.callback(transaction.rollback)
        yield stack.enter_context(Session(bind=connection))


def persist(session, row):
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def test_pipeline_job_defaults(session):
    job = persist(session, PipelineJob(content_id=uuid4(), upload_session_id=uuid4()))
    assert job.id is not None
    assert job.status == PipelineJobStatus.PENDING
    assert job.current_stage is None
    assert job.stage_versions == {}
    assert job.retries == 0
    assert job.context == {}
    assert job.created_at is not None


def test_pipeline_job_custom_initialization(session):
    job = persist(
        session,
        PipelineJob(
            content_id=uuid4(),
            upload_session_id=uuid4(),
            idempotency_key="test-key",
            current_stage="encode",
            status=PipelineJobStatus.PENDING,
            retries=2,
            context={"key": "value"},
        ),
    )
    assert job.idempotency_key == "test-key"
    assert job.current_stage == "encode"
    assert job.status == PipelineJobStatus.PENDING
    assert job.retries == 2
    assert job.context == {"key": "value"}
    assert job.leased_by is None


def test_transcoding_job_defaults_and_progress(session):
    job = persist(
        session, TranscodingJob(content_id=uuid4(), source_url="https://example.com/video.mp4")
    )
    assert job.status == TranscodingStatus.PENDING
    assert job.progress_percentage == 0
    assert job.output_hls_url is None
    assert job.output_dash_url is None


def test_video_manifest_defaults_and_variants(session):
    manifest = persist(
        session,
        VideoManifest(
            episode_id=uuid4(),
            content_id=uuid4(),
            protocol=DeliveryProtocol.HLS,
            manifest_url="https://example.com/manifest.m3u8",
            manifest_content="#EXTM3U",
        ),
    )
    assert manifest.include_subtitles is True
    assert manifest.include_closed_captions is True
    assert manifest.variants == []
    assert manifest.available_bitrates == []


def test_quality_profile_defaults_and_codecs(session):
    profile = persist(session, StreamingQualityProfile())
    assert profile.bitrates == []
    assert profile.resolutions == []
    profile.bitrates = [1000, 2000]
    profile.resolutions = ["720p", "1080p"]
    session.flush()
    session.refresh(profile)
    assert profile.bitrates == [1000, 2000]
    assert profile.resolutions == ["720p", "1080p"]


def test_pipeline_stage_log_defaults(session):
    job = persist(session, PipelineJob(content_id=uuid4(), upload_session_id=uuid4()))
    log = persist(
        session,
        PipelineStageLog(
            job_id=job.id,
            stage="encode",
            status=PipelineStageStatus.SUCCESS,
        ),
    )
    assert log.status == PipelineStageStatus.SUCCESS
    assert log.duration_ms == 0
    assert log.message is None
    assert log.created_at is not None
    assert log.job_id == job.id
