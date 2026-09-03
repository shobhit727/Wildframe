"""Gateway ads gate - TCF 2.0 GPP."""
import logging
logger = logging.getLogger(__name__)
def check_tcf(consent: str | None) -> bool:
    return bool(consent)
