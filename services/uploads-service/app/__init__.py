"""Uploads Service package.

Owns the *front end* of the media pipeline: getting content into the system
safely via signed, chunked/resumable uploads and handing a verified upload off
to the media-pipeline for processing.

Conventions mirror ``services/billing`` and ``services/streaming-service``:
models / repositories / services / api layers, Column-style ORM, async
SQLAlchemy 2.0, pydantic-settings, FastAPI.
"""
