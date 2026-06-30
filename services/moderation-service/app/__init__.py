"""Moderation Service package.

Owns the content review workflow: flagging content for review, the moderator
review queue, flag decisions (approve / reject / escalate), creator strikes,
and automatic suspension when a creator accumulates 3 active strikes.

Conventions mirror ``services/uploads-service`` and ``services/streaming-service``:
models / repositories / services / api layers, Column-style ORM, async
SQLAlchemy 2.0, pydantic-settings, FastAPI.
"""
