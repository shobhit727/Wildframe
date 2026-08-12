"""Notification message templates.

Every user-controlled field is HTML-escaped before interpolation, so template
rendering cannot be used for HTML/script injection into email or web surfaces.
"""

import html
import re

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TAGS = re.compile(r"<[^>]+>")

# (subject, html_body, text_body) triplets; {field} placeholders are filled
# with sanitized (escaped) values for HTML, tag-stripped plain values for text.
_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "generic": (
        "{title}",
        "<p>{message}</p>",
        "{message}",
    ),
    "welcome": (
        "Welcome to Wildframe, {name}!",
        "<h2>Welcome to Wildframe, {name}!</h2><p>{message}</p>",
        "Welcome to Wildframe, {name}! {message}",
    ),
    "new_episode": (
        "New episode: {title}",
        "<h2>{title}</h2><p>{message}</p>",
        "New episode: {title}. {message}",
    ),
}


def sanitize_text(value: str, max_length: int | None = None) -> str:
    """Escape HTML and strip control characters from user-controlled text."""
    cleaned = _CONTROL_CHARS.sub("", str(value or ""))
    escaped = html.escape(cleaned, quote=True)
    if max_length is not None and len(escaped) > max_length:
        escaped = escaped[:max_length]
    return escaped


def sanitize_plain(value: str, max_length: int | None = None) -> str:
    """Strip HTML tags and control characters, no HTML escaping."""
    cleaned = _CONTROL_CHARS.sub("", str(value or ""))
    stripped = _TAGS.sub("", cleaned)
    if max_length is not None and len(stripped) > max_length:
        stripped = stripped[:max_length]
    return stripped


def render_template(name: str, **context: object) -> tuple[str, str, str]:
    """Render a named template to (subject, html_body, text_body).

    All context values are treated as untrusted user input.
    HTML output receives HTML-escaped values; text output receives
    tag-stripped, control-char-free plain text.
    """
    subject_tmpl, html_tmpl, text_tmpl = _TEMPLATES.get(name, _TEMPLATES["generic"])
    safe_context = {key: sanitize_text(str(value)) for key, value in context.items()}
    plain_context = {key: sanitize_plain(str(value)) for key, value in context.items()}
    subject = subject_tmpl.format(**safe_context)
    html_body = html_tmpl.format(**safe_context)
    text_body = text_tmpl.format(**plain_context).strip()
    return subject, html_body, text_body
