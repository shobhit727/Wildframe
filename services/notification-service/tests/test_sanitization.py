"""Tests for `app.templates` - escaping and truncation of user-controlled text.

`app.templates` is the notification service's content-security boundary: every
user-supplied title/message flows through `sanitize_text` (HTML-escaped, for the
email HTML part) and `sanitize_plain` (tag-stripped, for the text part) before a
template is rendered. The truncation branches matter because a long message
would otherwise overflow the `VARCHAR(1000)` column and fail the insert.

These are deliberately adversarial: script tags, attribute-breaking quotes,
control bytes and multi-byte characters.
"""

from app.templates import (
    _TEMPLATES,
    render_template,
    sanitize_plain,
    sanitize_text,
)


# ---------------------------------------------------------------------------
# sanitize_text - HTML escaping
# ---------------------------------------------------------------------------


def test_script_tags_are_escaped():
    assert sanitize_text("<script>alert(1)</script>") == (
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    )


def test_quotes_are_escaped_so_attributes_cannot_be_broken_out_of():
    assert sanitize_text('" onerror="alert(1)') == "&quot; onerror=&quot;alert(1)"
    assert sanitize_text("it's") == "it&#x27;s"


def test_ampersands_are_escaped_first():
    assert sanitize_text("Tom & Jerry") == "Tom &amp; Jerry"


def test_control_characters_are_stripped():
    assert sanitize_text("a\x00b\x1fc\x7fd") == "abcd"


def test_newlines_and_tabs_survive():
    # Only the C0 control *other* than \t \n \r is stripped.
    assert sanitize_text("line1\nline2\tend") == "line1\nline2\tend"


def test_falsy_values_coerce_to_an_empty_string():
    # `str(value or "")` - so 0 / False / None all collapse to "".
    assert sanitize_text(None) == ""
    assert sanitize_text("") == ""
    assert sanitize_text(0) == ""
    assert sanitize_text(False) == ""


def test_truthy_non_strings_are_stringified():
    assert sanitize_text(12345) == "12345"


# ---------------------------------------------------------------------------
# sanitize_text - truncation
# ---------------------------------------------------------------------------


def test_sanitize_text_truncates_to_the_max_length():
    result = sanitize_text("x" * 500, max_length=255)

    assert len(result) == 255
    assert result == "x" * 255


def test_sanitize_text_leaves_shorter_values_alone():
    assert sanitize_text("short", max_length=255) == "short"


def test_sanitize_text_truncates_on_the_escaped_length():
    """Escaping expands the string, so truncation happens after escaping."""
    # 5 '<' become 5 x 4 chars = 20; capped at 10.
    assert len(sanitize_text("<" * 5, max_length=10)) == 10


def test_sanitize_text_without_a_limit_never_truncates():
    assert len(sanitize_text("y" * 5000)) == 5000


# ---------------------------------------------------------------------------
# sanitize_plain - tag stripping
# ---------------------------------------------------------------------------


def test_tags_are_stripped_but_not_escaped():
    assert sanitize_plain("<b>bold</b> text") == "bold text"
    assert sanitize_plain("a & b") == "a & b"


def test_a_lone_angle_bracket_is_left_alone():
    assert sanitize_plain("a < b") == "a < b"


def test_known_defect_sanitize_plain_eats_a_bare_comparison():
    """Characterisation test for a reported defect (NOT an assertion of intent).

    `app/templates.py:11` uses the greedy ``<[^>]+>`` tag pattern, so any pair of
    angle brackets in a message is removed as if it were markup. A plain-text
    notification containing "5 < 6 & 7 > 2" silently loses " < 6 & 7 >" from the
    text/plain part of the email (the HTML part is unaffected, because
    `sanitize_text` only escapes). Users comparing quantities in a notification
    would receive a corrupted message.
    """
    assert sanitize_plain("5 < 6 & 7 > 2") == "5  2"
    # The HTML part is not affected - only the tag-stripped plain part is.
    assert sanitize_text("5 < 6 & 7 > 2") == "5 &lt; 6 &amp; 7 &gt; 2"


def test_script_content_survives_as_plain_text():
    """Tags go; the text between them is kept, which is the intent for plain mail."""
    assert sanitize_plain("<script>alert(1)</script>") == "alert(1)"


def test_control_characters_are_stripped():
    assert sanitize_plain("a\x00b\x07c") == "abc"


def test_sanitize_plain_truncates_to_the_max_length():
    result = sanitize_plain("z" * 400, max_length=100)

    assert len(result) == 100


def test_sanitize_plain_truncates_after_stripping():
    # 200 '<' are tags-ish pairs; the stripped string is then capped.
    result = sanitize_plain("<>" * 200, max_length=50)

    assert len(result) <= 50


def test_sanitize_plain_without_a_limit_never_truncates():
    assert len(sanitize_plain("<b>" + "q" * 3000 + "</b>")) == 3000


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------


def test_generic_template_renders_title_and_message():
    subject, html_body, text_body = render_template("generic", title="T", message="M")

    assert subject == "T"
    assert html_body == "<p>M</p>"
    assert text_body == "M"


def test_welcome_template_has_a_fixed_subject():
    subject, html_body, text_body = render_template("welcome", title="T", message="M")

    assert subject == "Welcome to Wildframe!"
    assert "<h2>Welcome to Wildframe!</h2>" in html_body
    assert text_body == "Welcome to Wildframe! M"


def test_new_episode_template_uses_both_fields():
    subject, html_body, text_body = render_template("new_episode", title="S5E1", message="Out")

    assert subject == "New episode: S5E1"
    assert "<h2>S5E1</h2><p>Out</p>" == html_body
    assert text_body == "New episode: S5E1. Out"


def test_an_unknown_template_falls_back_to_generic():
    assert render_template("nope", title="T", message="M") == render_template(
        "generic", title="T", message="M"
    )


def test_html_output_escapes_both_fields():
    _, html_body, _ = render_template("new_episode", title="<i>x</i>", message="<b>y</b>")

    assert "<i>" not in html_body
    assert "&lt;i&gt;x&lt;/i&gt;" in html_body
    assert "<b>y</b>" not in html_body


def test_text_output_strips_tags_from_both_fields():
    _, _, text_body = render_template("new_episode", title="<i>x</i>", message="<b>y</b>")

    assert text_body == "New episode: x. y"


def test_text_output_is_stripped_of_surrounding_whitespace():
    _, _, text_body = render_template("generic", title="T", message="   padded   ")

    assert text_body == "padded"


def test_context_values_are_coerced_to_strings():
    subject, _, _ = render_template("generic", title=12345, message="M")

    assert subject == "12345"


def test_a_none_context_value_renders_the_literal_none():
    """`render_template` stringifies before sanitising, so `None` is *not* blanked.

    Not reachable from the API (`message` is a required Body field), but pinned
    here so a caller that starts passing None notices the literal "None" mail.
    """
    subject, _, text_body = render_template("generic", title="T", message=None)

    assert subject == "T"
    assert text_body == "None"


def test_every_shipped_template_renders_with_the_documented_context():
    for name in _TEMPLATES:
        subject, html_body, text_body = render_template(
            name, title="T", message="M"
        )
        assert isinstance(subject, str) and subject
        assert isinstance(html_body, str) and html_body
        assert isinstance(text_body, str)
