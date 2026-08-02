"""Streaming service API routes."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.streaming import (
    AudioTrackRequest,
    AudioTrackResponse,
    EndStreamingRequest,
    HeartbeatRequest,
    ManifestResponse,
    RecordMetricsRequest,
    StartStreamingRequest,
    StreamingSessionResponse,
    StreamingStatsResponse,
    SubtitleRequest,
    SubtitleResponse,
)
from app.services.streaming import StreamingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/streaming", tags=["streaming"])


async def get_streaming_service(db: Annotated[AsyncSession, Depends(get_db)]) -> StreamingService:
    """Get streaming service instance."""
    return StreamingService(db)


# Session Management Endpoints


@router.post(
    "/sessions/start", response_model=StreamingSessionResponse, status_code=status.HTTP_201_CREATED
)
async def start_streaming(
    data: StartStreamingRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
) -> StreamingSessionResponse:
    """Start streaming session."""
    try:
        session = await service.start_streaming(
            user_id=UUID("00000000-0000-0000-0000-000000000000"),  # Would come from auth context
            content_id=data.content_id,
            media_key="",  # Would come from content service
            content_type=data.content_type,
            device_type=data.device_type,
            ip_address="",  # Would come from request context
            user_agent="",  # Would come from request context
            preferred_quality=data.preferred_quality,
            preferred_audio_lang=data.preferred_audio_language,
            preferred_subtitle_lang=data.preferred_subtitle_language,
        )

        manifest = await service.get_manifest("")
        if not manifest:
            raise ValueError("Manifest not found")

        subtitles = await service.list_subtitles("")
        audio_tracks = await service.list_audio_tracks("")

        return StreamingSessionResponse(
            id=session.id,
            session_token=session.session_token,
            manifest=ManifestResponse(
                id=manifest.id,
                media_key=manifest.media_key,
                hls_master_url=manifest.hls_master_url,
                dash_mpd_url=manifest.dash_mpd_url,
                available_bitrates=manifest.available_bitrates,
                available_subtitles=[SubtitleResponse.model_validate(s) for s in subtitles],
                available_audio=[AudioTrackResponse.model_validate(a) for a in audio_tracks],
                duration_seconds=manifest.duration_seconds,
                segment_duration_seconds=manifest.segment_duration_seconds,
            ),
            current_playback_position=session.played_until_seconds,
            duration_seconds=session.duration_seconds,
            stream_quality=session.stream_quality,
            available_subtitles=[SubtitleResponse.model_validate(s) for s in subtitles],
            available_audio=[AudioTrackResponse.model_validate(a) for a in audio_tracks],
            cdn_edge="cdn.edge.region1",
            created_at=session.created_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error starting stream: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to start streaming"
        )


@router.get("/sessions/{session_token}", response_model=StreamingSessionResponse)
async def get_session(
    session_token: str, service: Annotated[StreamingService, Depends(get_streaming_service)]
) -> StreamingSessionResponse:
    """Get streaming session."""
    try:
        session = await service.get_streaming_session(session_token)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        manifest = await service.get_manifest("")
        if not manifest:
            raise ValueError("Manifest not found")

        subtitles = await service.list_subtitles("")
        audio_tracks = await service.list_audio_tracks("")

        return StreamingSessionResponse(
            id=session.id,
            session_token=session.session_token,
            manifest=ManifestResponse(
                id=manifest.id,
                media_key=manifest.media_key,
                hls_master_url=manifest.hls_master_url,
                dash_mpd_url=manifest.dash_mpd_url,
                available_bitrates=manifest.available_bitrates,
                available_subtitles=[SubtitleResponse.model_validate(s) for s in subtitles],
                available_audio=[AudioTrackResponse.model_validate(a) for a in audio_tracks],
                duration_seconds=manifest.duration_seconds,
                segment_duration_seconds=manifest.segment_duration_seconds,
            ),
            current_playback_position=session.played_until_seconds,
            duration_seconds=session.duration_seconds,
            stream_quality=session.stream_quality,
            available_subtitles=[SubtitleResponse.model_validate(s) for s in subtitles],
            available_audio=[AudioTrackResponse.model_validate(a) for a in audio_tracks],
            cdn_edge="cdn.edge.region1",
            created_at=session.created_at,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get session"
        )


@router.post("/sessions/{session_token}/heartbeat")
async def heartbeat(
    session_token: str,
    data: HeartbeatRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
) -> dict:
    """Send heartbeat for streaming session."""
    try:
        session = await service.heartbeat(
            data.session_token, data.played_until_seconds, data.bandwidth_mbps, data.current_bitrate
        )
        return {"status": "alive", "session_id": str(session.id)}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error sending heartbeat: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send heartbeat"
        )


@router.post("/sessions/{session_token}/metrics")
async def record_metrics(
    session_token: str,
    data: RecordMetricsRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
) -> dict:
    """Record streaming metrics."""
    try:
        metrics = await service.record_metrics(
            data.session_token,
            data.bandwidth_mbps,
            data.bitrate_kbps,
            data.quality.value,
            data.rebuffering_seconds,
            data.packets_lost,
            data.latency_ms,
            data.cpu_usage_percent,
            data.memory_usage_mb,
        )
        return {"status": "recorded", "metrics_id": str(metrics.id)}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error recording metrics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to record metrics"
        )


@router.post("/sessions/{session_token}/end", status_code=status.HTTP_204_NO_CONTENT)
async def end_streaming(
    session_token: str,
    data: EndStreamingRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
) -> None:
    """End streaming session."""
    try:
        await service.end_streaming(data.session_token, data.played_until_seconds)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error ending stream: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to end streaming"
        )


@router.get("/sessions/{session_token}/stats", response_model=StreamingStatsResponse)
async def get_streaming_stats(
    session_token: str, service: Annotated[StreamingService, Depends(get_streaming_service)]
) -> StreamingStatsResponse:
    """Get streaming statistics."""
    try:
        stats = await service.get_session_stats(session_token)
        await service.get_streaming_session(session_token)

        return StreamingStatsResponse(
            session_id=stats["session_id"],
            total_watched_seconds=stats["total_watched_seconds"],
            average_bandwidth_mbps=stats["average_bandwidth_mbps"],
            average_bitrate_kbps=stats["average_bitrate_kbps"],
            buffer_events=stats["buffer_events"],
            total_buffer_seconds=stats["total_buffer_seconds"],
            video_quality=stats["video_quality"],
            audio_language="en",
            subtitle_language=None,
            completion_percentage=stats["completion_percentage"],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get stats"
        )


# Buffering Events


@router.post("/sessions/{session_token}/buffering")
async def record_buffering(
    session_token: str,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    buffer_seconds: Annotated[float, Query(gt=0)],
) -> dict:
    """Record buffering event."""
    try:
        await service.record_buffering(session_token, buffer_seconds)
        return {"status": "recorded"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error recording buffering: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to record buffering"
        )


# Subtitle Management


@router.post("/subtitles", response_model=SubtitleResponse, status_code=status.HTTP_201_CREATED)
async def add_subtitle(
    data: SubtitleRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    media_key: Annotated[str, Query()],
) -> SubtitleResponse:
    """Add subtitle track."""
    try:
        subtitle = await service.add_subtitle(
            media_key,
            data.language,
            data.language_name,
            data.subtitle_url,
            data.format,
            data.is_default,
            data.is_forced,
        )
        return subtitle
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error adding subtitle: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add subtitle"
        )


@router.get("/subtitles", response_model=list[SubtitleResponse])
async def list_subtitles(
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    media_key: Annotated[str, Query()],
) -> list[SubtitleResponse]:
    """List subtitles for media."""
    try:
        subtitles = await service.list_subtitles(media_key)
        return subtitles
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error listing subtitles: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list subtitles"
        )


# Audio Track Management


@router.post("/audio", response_model=AudioTrackResponse, status_code=status.HTTP_201_CREATED)
async def add_audio_track(
    data: AudioTrackRequest,
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    media_key: Annotated[str, Query()],
) -> AudioTrackResponse:
    """Add audio track."""
    try:
        track = await service.add_audio_track(
            media_key,
            data.language,
            data.language_name,
            data.codec,
            data.bitrate_kbps,
            data.channels,
            data.is_default,
        )
        return track
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error adding audio track: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add audio track"
        )


@router.get("/audio", response_model=list[AudioTrackResponse])
async def list_audio_tracks(
    service: Annotated[StreamingService, Depends(get_streaming_service)],
    media_key: Annotated[str, Query()],
) -> list[AudioTrackResponse]:
    """List audio tracks for media."""
    try:
        tracks = await service.list_audio_tracks(media_key)
        return tracks
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error listing audio tracks: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list audio tracks"
        )
