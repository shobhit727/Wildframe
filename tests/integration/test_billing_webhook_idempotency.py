"""Billing webhook idempotency and signature enforcement (durable, live).

Replays the same Stripe event twice through the real billing-service webhook
(bypassing the gateway, as Stripe would) and asserts the durable-inbox
contract: the first delivery is handled, the replay is acknowledged as
idempotent, and a re-signed duplicate for the same invoice is deduplicated
by stripe_invoice_id.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import BILLING_SERVICE, stripe_event, stripe_signature, register_user

pytestmark = pytest.mark.integration

WEBHOOK_PATH = f"{BILLING_SERVICE}/billing/webhooks/stripe"


def _invoice_paid_payload(
    user_id: str, invoice_id: str | None = None, event_id: str | None = None
) -> bytes:
    return stripe_event(
        event_id=event_id or f"evt_it_{uuid4hex()}",
        event_type="invoice.paid",
        object_payload={
            "id": invoice_id or f"in_it_{uuid4hex()}",
            "object": "invoice",
            "customer": "cus_it_1",
            "status": "paid",
            "currency": "usd",
            "total": 1000,
            "lines": {
                "data": [
                    {"id": "il_it_1", "metadata": {"user_id": user_id}},
                ]
            },
        },
    )


class TestSignatureEnforcement:
    def test_unsigned_payload_rejected(self, client: httpx.Client, user_a: dict) -> None:
        payload = _invoice_paid_payload(user_a["user_id"])
        response = client.post(WEBHOOK_PATH, content=payload)
        assert response.status_code == 400

    def test_forged_signature_rejected(self, client: httpx.Client, user_a: dict) -> None:
        payload = _invoice_paid_payload(user_a["user_id"])
        response = client.post(
            WEBHOOK_PATH, content=payload, headers={"Stripe-Signature": "t=1,v1=deadbeef"}
        )
        assert response.status_code == 400


class TestDuplicateEventHandling:
    def test_replay_is_idempotent(self, client: httpx.Client, user_a: dict) -> None:
        payload = _invoice_paid_payload(user_a["user_id"])
        headers = {"Stripe-Signature": stripe_signature(payload)}

        first = client.post(WEBHOOK_PATH, content=payload, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json().get("handled") is True

        replay = client.post(WEBHOOK_PATH, content=payload, headers=headers)
        assert replay.status_code == 200, replay.text
        assert replay.json().get("idempotent") is True, "replay must be acknowledged, not re-handled"

    def test_resigned_duplicate_invoice_deduped(
        self, client: httpx.Client, user_a: dict
    ) -> None:
        """A second delivery with a new event id but the same invoice id must
        not create a second invoice row (unique stripe_invoice_id)."""
        invoice_id = f"in_it_{uuid4hex()}"
        first_payload = _invoice_paid_payload(user_a["user_id"], invoice_id=invoice_id)
        first = client.post(
            WEBHOOK_PATH,
            content=first_payload,
            headers={"Stripe-Signature": stripe_signature(first_payload)},
        )
        assert first.status_code == 200, first.text

        second_payload = stripe_event(
            event_id=f"evt_it_{uuid4hex()}",
            event_type="invoice.paid",
            object_payload={
                "id": invoice_id,
                "object": "invoice",
                "customer": "cus_it_1",
                "status": "paid",
                "currency": "usd",
                "total": 1000,
                "lines": {
                    "data": [
                        {"id": "il_it_2", "metadata": {"user_id": user_a["user_id"]}},
                    ]
                },
            },
        )
        second = client.post(
            WEBHOOK_PATH,
            content=second_payload,
            headers={"Stripe-Signature": stripe_signature(second_payload)},
        )
        assert second.status_code == 200, second.text


def uuid4hex() -> str:
    import uuid as _uuid

    return _uuid.uuid4().hex


@pytest.fixture(scope="module")
def user_a(client: httpx.Client) -> dict:
    return register_user(client)
