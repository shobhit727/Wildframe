"""Behavioural tests for ``app/api/routes/age.py``.

The router is **defined but never mounted** on the production app
(``app/api/routes/__init__.py`` only includes ``auth`` and ``privacy``), so it
has zero coverage from the live app. These tests mount it onto a throwaway
``FastAPI()`` with a fake async session — no production refactor, no Postgres.

The jurisdiction policy lookup is the interesting part: a known
``wildframe_compliance.Jurisdiction`` resolves a real
``consent_minor_age``; anything else falls back to the module's
``CONSENT_AGES`` table and finally to 16.
"""

import contextlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.api.routes.age import CONSENT_AGES, router as age_router
from app.core.database import get_db
from app.models.age_verification import AgeVerification
from fastapi import FastAPI
from fastapi.testclient import TestClient
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import get_policy_for_jurisdiction


def _fake_session(record: AgeVerification | None = None):
    """A minimal AsyncSession double that records what the route persisted."""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock()

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=record)
    session.execute.return_value = result
    return session


@pytest.fixture
def age_app():
    app = FastAPI()
    app.include_router(age_router)
    return app


@pytest.fixture
def client_for(age_app):
    """Yield a context manager that mounts a client bound to a fake session."""

    @contextlib.contextmanager
    def _build(session):
        age_app.dependency_overrides[get_db] = lambda: session
        try:
            with TestClient(age_app) as client:
                yield client
        finally:
            age_app.dependency_overrides.clear()

    return _build


def _payload(**overrides):
    body = {
        "user_id": str(uuid.uuid4()),
        "declared_age": 30,
        "jurisdiction": "EU",
        "verification_method": "self_declare",
    }
    body.update(overrides)
    return body


class TestVerifyAge:
    def test_adult_in_a_known_jurisdiction_is_not_a_minor(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post("/age/verify", json=_payload(declared_age=30))

        assert response.status_code == 201
        body = response.json()
        assert body["is_minor"] is False
        assert body["verified"] is True
        assert body["consent_minor_age"] == get_policy_for_jurisdiction(
            Jurisdiction.EU
        ).consent_minor_age
        assert body["verified_age"] == 30
        assert body["verified_at"] is not None

    @pytest.mark.parametrize(
        ("jurisdiction", "declared_age", "expected_minor"),
        [
            ("EU", 15, True),
            ("EU", 16, False),
            ("US", 12, True),
            ("US", 13, False),
            ("IN", 17, True),
            ("IN", 18, False),
        ],
    )
    def test_minor_threshold_follows_the_jurisdiction_policy(
        self, client_for, jurisdiction, declared_age, expected_minor
    ):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post(
                    "/age/verify",
                    json=_payload(jurisdiction=jurisdiction, declared_age=declared_age),
                )

        assert response.status_code == 201
        assert response.json()["is_minor"] is expected_minor

    def test_jwt_claim_mirrors_the_verification_result(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post("/age/verify", json=_payload(declared_age=14))

        claim = response.json()["jwt_claim"]
        assert claim["age_verified"] is True
        assert claim["is_minor"] is True
        assert claim["minor_flag"] is True
        assert claim["consent_age"] == get_policy_for_jurisdiction(
            Jurisdiction.EU
        ).consent_minor_age
        # verified_at in the claim is a parseable ISO-8601 stamp.
        assert datetime.fromisoformat(claim["verified_at"]).tzinfo is not None

    def test_unknown_jurisdiction_falls_back_to_the_local_table(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post(
                    "/age/verify", json=_payload(jurisdiction="ZZ", declared_age=10)
                )

        assert response.status_code == 201
        body = response.json()
        # "ZZ" is not in CONSENT_AGES, so the hard-coded default of 16 applies.
        assert body["consent_minor_age"] == 16
        assert body["is_minor"] is True
        assert body["jurisdiction"] == "ZZ"

    def test_known_custom_jurisdiction_uses_its_consent_age_table_entry(
        self, client_for
    ):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post(
                    "/age/verify", json=_payload(jurisdiction="US-CA", declared_age=15)
                )

        assert response.status_code == 201
        # US-CA is a Jurisdiction (so the policy path wins), but assert the
        # result is one of the two possible sources to keep the test honest.
        assert response.json()["consent_minor_age"] in {
            get_policy_for_jurisdiction(Jurisdiction.US_CA).consent_minor_age,
            CONSENT_AGES["US-CA"],
        }

    def test_persists_an_age_verification_row(self, client_for):
        session = _fake_session()
        user_id = uuid.uuid4()

        with client_for(session) as client:
                response = client.post(
                    "/age/verify",
                    json=_payload(user_id=str(user_id), declared_age=17),
                )

        assert response.status_code == 201
        session.add.assert_called_once()
        record = session.add.call_args.args[0]
        assert isinstance(record, AgeVerification)
        assert record.user_id == user_id
        assert record.declared_age == 17
        assert record.verified_age == 17
        assert record.is_minor is False
        assert record.verified_by == "self"
        assert record.verified_at is not None
        session.flush.assert_awaited()
        session.commit.assert_awaited()
        session.refresh.assert_awaited()

    def test_document_verification_is_attributed_to_document(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post(
                    "/age/verify",
                    json=_payload(
                        verification_method="document",
                        document_type="passport",
                        declared_age=21,
                    ),
                )

        assert response.status_code == 201
        record = session.add.call_args.args[0]
        assert record.verified_by == "document"
        assert record.document_type == "passport"
        assert record.verification_method == "document"

    def test_id_check_verification_is_attributed_to_document(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post(
                    "/age/verify", json=_payload(verification_method="id_check")
                )

        assert response.status_code == 201
        assert session.add.call_args.args[0].verified_by == "document"

    def test_declared_age_zero_is_a_minor(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post("/age/verify", json=_payload(declared_age=0))

        assert response.status_code == 201
        assert response.json()["is_minor"] is True

    def test_declared_age_120_is_accepted(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post("/age/verify", json=_payload(declared_age=120))

        assert response.status_code == 201
        assert response.json()["is_minor"] is False

    @pytest.mark.parametrize("declared_age", [-1, 121])
    def test_out_of_range_declared_age_is_rejected(self, client_for, declared_age):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post(
                    "/age/verify", json=_payload(declared_age=declared_age)
                )

        assert response.status_code == 422
        session.add.assert_not_called()

    def test_unknown_verification_method_is_rejected_by_the_schema(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post(
                    "/age/verify", json=_payload(verification_method="telepathy")
                )

        assert response.status_code == 422

    def test_missing_user_id_is_rejected(self, client_for):
        session = _fake_session()
        body = _payload()
        body.pop("user_id")

        with client_for(session) as client:
                response = client.post("/age/verify", json=body)

        assert response.status_code == 422

    def test_short_jurisdiction_is_rejected(self, client_for):
        session = _fake_session()

        with client_for(session) as client:
                response = client.post("/age/verify", json=_payload(jurisdiction="E"))

        assert response.status_code == 422


class TestCheckAge:
    def _record(self, user_id, **overrides):
        fields = {
            "user_id": user_id,
            "verification_method": "self_declare",
            "declared_age": 20,
            "verified_age": 20,
            "is_minor": False,
            "jurisdiction": "EU",
            "consent_minor_age": 16,
            "document_type": None,
            "verified_at": datetime.now(UTC),
        }
        fields.update(overrides)
        record = MagicMock(spec=AgeVerification)
        for key, value in fields.items():
            setattr(record, key, value)
        return record

    def test_returns_the_stored_verification(self, client_for):
        user_id = uuid.uuid4()
        session = _fake_session(self._record(user_id))

        with client_for(session) as client:
                response = client.get(f"/age/check/{user_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == str(user_id)
        assert body["verified_age"] == 20
        assert body["is_minor"] is False
        assert body["jurisdiction"] == "EU"
        assert body["consent_minor_age"] == 16
        assert body["verified"] is True
        assert body["jwt_claim"] == {"age_verified": True, "is_minor": False}

    def test_missing_record_returns_404(self, client_for):
        session = _fake_session(None)

        with client_for(session) as client:
                response = client.get(f"/age/check/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Age verification not found"

    def test_minor_flag_is_propagated_from_the_stored_row(self, client_for):
        user_id = uuid.uuid4()
        session = _fake_session(self._record(user_id, is_minor=True, verified_age=14))

        with client_for(session) as client:
                response = client.get(f"/age/check/{user_id}")

        assert response.json()["is_minor"] is True
        assert response.json()["jwt_claim"] == {"age_verified": True, "is_minor": True}

    def test_unverified_record_reports_verified_false(self, client_for):
        user_id = uuid.uuid4()
        session = _fake_session(self._record(user_id, verified_at=None, verified_age=None))

        with client_for(session) as client:
                response = client.get(f"/age/check/{user_id}")

        body = response.json()
        assert body["verified"] is False
        assert body["verified_at"] is None
        assert body["verified_age"] is None
        assert body["jwt_claim"] == {"age_verified": False, "is_minor": False}

    def test_queries_by_user_id(self, client_for):
        user_id = uuid.uuid4()
        session = _fake_session(self._record(user_id))

        with client_for(session) as client:
                client.get(f"/age/check/{user_id}")

        session.execute.assert_awaited_once()
        statement = session.execute.await_args.args[0]
        compiled = str(statement).replace("\n", " ")
        assert "FROM age_verifications" in compiled
        assert f"age_verifications.user_id = :user_id_1" in compiled
        assert statement.compile().params["user_id_1"] == user_id

    def test_non_uuid_user_id_is_rejected(self, client_for):
        session = _fake_session(None)

        with client_for(session) as client:
                response = client.get("/age/check/not-a-uuid")

        assert response.status_code == 422
        session.execute.assert_not_awaited()


class TestRouterShape:
    def test_router_is_prefixed_and_tagged(self):
        assert age_router.prefix == "/age"
        assert age_router.tags == ["age-verification"]

    def test_routes_are_declared(self):
        paths = {(route.path, tuple(sorted(route.methods))) for route in age_router.routes}

        assert ("/age/verify", ("POST",)) in paths
        assert ("/age/check/{user_id}", ("GET",)) in paths

    def test_router_is_not_mounted_on_the_production_app(self):
        """Documents why these tests mount a throwaway app instead."""
        from app.api.routes import router as mounted_router
        from app.main import create_app

        mounted = set(create_app().openapi()["paths"])
        assert not any(path.startswith("/api/v1/age") for path in mounted)
        assert not any(getattr(r, "path", "").startswith("/age") for r in mounted_router.routes)

    def test_consent_ages_table_contents(self):
        assert CONSENT_AGES == {
            "EU": 16,
            "US": 13,
            "IN": 18,
            "GLOBAL": 16,
            "US-CA": 16,
        }
