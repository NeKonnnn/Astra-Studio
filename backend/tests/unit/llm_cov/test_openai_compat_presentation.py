"""openai_compat: помощники сборки HTML-презентаций (auto-continue слайдов) — чистые функции."""

import pytest

from backend.llm_providers import openai_compat as oc

pytestmark = pytest.mark.unit

S1 = '<div class="slide"><div>one</div></div>'
S2 = '<div class="slide"><div>two</div></div>'
S3 = '<div class="slide title-slide"><div>three</div></div>'
HEAD = "<head><style>.slide{width:1px}</style></head>"


class TestSlideCounting:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("slide", True), ("a slide b", True), ("slide-title", False), ("", False), (None, False)],
    )
    def test_class_token(self, value, expected):
        assert oc._class_attr_has_slide_token(value) is expected

    def test_counts_only_slide_token(self):
        html = f'{S1}<div class="slide-title">t</div><div class=\'x slide\'></div><p class=slide></p><p class=slide-title></p>'

        assert oc._count_html_slides(html) == 3

    def test_empty(self):
        assert oc._count_html_slides("") == 0


class TestMessageTexts:
    def test_filters_roles_and_shapes(self):
        messages = [
            "junk",
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "  "},
            {"role": "user", "content": "plain"},
            {"role": "USER", "content": [{"type": "text", "text": "a"}, "b", {"type": "image_url"}, 5]},
            {"role": "user", "content": []},
            {"role": "user", "content": None},
        ]

        assert oc._iter_message_texts(messages) == ["plain", "a\nb"]

    def test_custom_roles(self):
        msgs = [{"role": "assistant", "content": "x"}, {"role": "user", "content": "y"}]

        assert oc._iter_message_texts(msgs, roles=("assistant",)) == ["x"]
        assert oc._iter_message_texts(None) == []


class TestRequestedSlides:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", []),
            ("сделай 12 слайдов", [12]),
            ("ровно 7", [7]),
            ("exactly 9 slides", [9]),
            ("слайдов: 5", [5]),
            ("презентацию на 25 тем", [25]),
            ("presentation with 4 parts", [4]),
            ("500 слайдов", []),
            ("0 слайдов", []),
            ("5 слайдов и 9 страниц", [5, 9]),
            ("просто текст", []),
        ],
    )
    def test_parse_counts(self, text, expected):
        assert oc._parse_slide_counts_in_text(text) == expected

    def test_only_last_user_message_counts(self):
        msgs = [
            {"role": "user", "content": "сделай 40 слайдов"},
            {"role": "assistant", "content": "готово"},
            {"role": "user", "content": "теперь 7 слайдов или 3 слайда"},
        ]

        assert oc._requested_slide_count(msgs) == 7

    def test_last_message_without_number_gives_none(self):
        msgs = [
            {"role": "user", "content": "40 слайдов"},
            {"role": "user", "content": "спасибо"},
        ]

        assert oc._requested_slide_count(msgs) is None

    def test_no_user_messages(self):
        assert oc._requested_slide_count([{"role": "assistant", "content": "x"}]) is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("Спасибо за внимание!", True), ("Thank You For Your Attention", True), ("Вопросы и ответы", True), ("слайд 3", False), (None, False)],
    )
    def test_looks_finished(self, text, expected):
        assert oc._presentation_looks_finished(text) is expected


class TestHtmlComments:
    def test_trailing_incomplete_comment_removed(self):
        assert oc._strip_incomplete_trailing_html_comment("<p>a</p>\n<!-- СЛАЙД 3") == "<p>a</p>"

    def test_complete_comment_kept(self):
        text = "<p>a</p><!-- ok -->"

        assert oc._strip_incomplete_trailing_html_comment(text) == text

    def test_no_comment_and_empty(self):
        assert oc._strip_incomplete_trailing_html_comment("<p>a</p>") == "<p>a</p>"
        assert oc._strip_incomplete_trailing_html_comment("") == ""


class TestFragmentCleaning:
    def test_fallback_chrome_wraps_default_style(self):
        chrome = oc._presentation_fallback_chrome()

        assert chrome.startswith("<style>") and chrome.endswith("</style>")
        assert ".slide{width:297mm" in chrome

    def test_clean_slide_fragment_removes_document_noise(self):
        frag = "```html\n<!DOCTYPE html>\n<html><head><style>x</style></head><body>\n" + S1 + "\n</body></html>\n```<!-- tail"

        cleaned = oc._clean_slide_fragment(frag)

        assert cleaned == S1

    def test_clean_slide_fragment_removes_lonely_language_word(self):
        assert oc._clean_slide_fragment("html\n" + S1) == S1

    def test_clean_slide_fragment_empty(self):
        assert oc._clean_slide_fragment("") == ""

    def test_strip_reopen_prefix_repeated_noise(self):
        text = "```html\n<!DOCTYPE html>\n<html>\n<head><style>a</style></head>\n<body>\n" + S2

        assert oc._strip_presentation_reopen_prefix(text) == S2

    def test_strip_reopen_prefix_stops_after_four_rounds(self):
        text = "html" + chr(10) + "html" + chr(10) + "html" + chr(10) + "html" + chr(10) + "html" + chr(10) + "body"

        assert oc._strip_presentation_reopen_prefix(text) == "html" + chr(10) + "body"

    def test_strip_reopen_prefix_language_line_and_empty(self):
        assert oc._strip_presentation_reopen_prefix("html\n" + S2) == S2
        assert oc._strip_presentation_reopen_prefix(None) == ""
        assert oc._strip_presentation_reopen_prefix(S2) == S2


class TestMergeContinue:
    def test_empty_full(self):
        assert oc._merge_presentation_continue("prev", "") == ""
        assert oc._merge_presentation_continue("", "") == ""

    def test_no_previous_text(self):
        assert oc._merge_presentation_continue("", "full") == "full"

    def test_delta_with_reopen_is_stripped(self):
        prev = "```html\n" + S1
        full = prev + "\n```html\n<!DOCTYPE html>\n" + S2

        merged = oc._merge_presentation_continue(prev, full)
        assert merged.startswith(prev)
        assert "DOCTYPE" not in merged and S2 in merged

    def test_clean_delta_returns_full_unchanged(self):
        prev = S1
        full = S1 + S2

        assert oc._merge_presentation_continue(prev, full) == full

    def test_prev_shortened_by_opening_keeps_prev_and_strips_tail(self):
        # full не начинается с prev (prev был чуть укорочен), но длиннее него
        prev = "abcdef"
        full = "abcdeX```html\n<!DOCTYPE html>\n" + S2

        assert oc._merge_presentation_continue(prev, full) == prev + S2

    def test_full_not_longer_than_prev(self):
        assert oc._merge_presentation_continue("long previous text", "short") == "short"


class TestUnbalanced:
    @pytest.mark.parametrize(
        ("frag", "expected"),
        [
            ("", False),
            ("</body></html>", True),
            ('<div class="slide"><div>x</div></div>', False),
            ('<div class="slide"><div>x</div>', True),
            ('<div class="slide"><div>x</div></div><img src="a', True),
            ('<div class="slide"><div>ok</div></div><!-- tail', False),
            ('<div class="slide"><div>x</div></div></body></html>', False),
            ('<div class="slide"><div>x</div></div></html>', False),
            ('<div class="slide"><div>x</div></div></body>', False),
        ],
    )
    def test_last_slide_fragment_unbalanced(self, frag, expected):
        assert oc._last_slide_fragment_unbalanced(frag) is expected


class TestPresentationDetection:
    def test_looks_like_presentation_html(self):
        assert oc._looks_like_presentation_html("") is False
        assert oc._looks_like_presentation_html(S1) is True
        assert oc._looks_like_presentation_html("<div class='slide'>") is True
        assert oc._looks_like_presentation_html("--gpb-x .slide{}") is True  # банковский маркер: gpb + .slide
        assert oc._looks_like_presentation_html("--deck-x .slide{}") is False
        assert oc._looks_like_presentation_html("deck-slide only") is False
        assert oc._looks_like_presentation_html("обычный текст") is False

    def test_truncated_by_open_comment(self):
        assert oc._presentation_looks_truncated(S1 + "<!-- СЛАЙД 2") is True

    def test_truncated_by_open_tag_and_trailing_fence(self):
        assert oc._presentation_looks_truncated(S1 + '<div class="sl') is True
        assert oc._presentation_looks_truncated(S1 + "<p>half</p><im" + "\n```") is True

    def test_truncated_by_closing_tags_inside_comment(self):
        text = S1 + "<!-- x </body></html> -->"

        assert oc._presentation_looks_truncated(text) is True

    def test_truncated_by_unbalanced_last_slide(self):
        assert oc._presentation_looks_truncated(S1 + '<div class="slide"><div>x</div>') is True

    def test_complete_deck_is_not_truncated(self):
        assert oc._presentation_looks_truncated(S1 + S2 + "</body></html>") is False
        assert oc._presentation_looks_truncated("") is False
        assert oc._presentation_looks_truncated("просто текст") is False

    def test_html_document_unfinished(self):
        assert oc._html_document_unfinished("") is False
        assert oc._html_document_unfinished(S1) is False  # слайды уже есть
        assert oc._html_document_unfinished("```html\n<style>.slide{}</style>") is True
        assert oc._html_document_unfinished("<!doctype html><html><head><style>a") is True
        assert oc._html_document_unfinished("<html><head></head></html>") is False
        assert oc._html_document_unfinished("<html><head></head><body>text") is False
        assert oc._html_document_unfinished("просто текст") is False


class TestExtraction:
    def test_extract_slide_divs_splits_on_slide_starts(self):
        html = f"<body>{S1}\n{S2}\n<p class=slide>third</p></body></html>"

        slides = oc._extract_slide_divs(html)

        assert len(slides) == 3
        assert slides[0].startswith('<div class="slide"><div>one')
        assert slides[2].startswith("<p class=slide>third")
        assert not slides[2].endswith("</html>")

    def test_extract_slide_divs_empty_and_no_slides(self):
        assert oc._extract_slide_divs("") == []
        assert oc._extract_slide_divs("<p>text</p>") == []

    def test_extract_head_chrome_from_head(self):
        assert oc._extract_head_chrome(f"<html>{HEAD}<body>{S1}</body></html>") == "<style>.slide{width:1px}</style>"

    def test_extract_head_chrome_fallback_before_first_slide(self):
        html = '<link rel="stylesheet" href="a.css"><style>b{}</style>' + S1

        chrome = oc._extract_head_chrome(html)

        assert '<link rel="stylesheet" href="a.css">' in chrome and "<style>b{}</style>" in chrome

    def test_extract_head_chrome_without_slides(self):
        assert oc._extract_head_chrome("<style>x{}</style> text") == "<style>x{}</style>"
        assert oc._extract_head_chrome("") == ""
        assert oc._extract_head_chrome(None) == ""

    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            ("", ""),
            ("<p>a</p></html>", "<p>a</p></html>"),
            ("<p>a</p></body>", "<p>a</p></body>\n</html>"),
            ("<p>a</p>", "<p>a</p>\n</body>\n</html>"),
        ],
    )
    def test_ensure_document_closed(self, html, expected):
        assert oc._ensure_html_document_closed(html) == expected


class TestFinalize:
    def test_plain_text_is_untouched(self):
        assert oc._finalize_presentation_message("") == ""
        assert oc._finalize_presentation_message("просто текст") == "просто текст"

    def test_mixed_fence_and_raw_doctype_merge_into_one_block(self):
        text = (
            "Вот презентация:\n```html\n<!DOCTYPE html><html>" + HEAD + "<body>" + S1 + "\n```\n"
            "<!DOCTYPE html><html><body>" + S2 + S3 + "</body></html>"
        )

        out = oc._finalize_presentation_message(text)

        assert out.startswith("Вот презентация:\n\n```html\n<!DOCTYPE html>")
        assert out.count("```html") == 1 and out.endswith("```")
        assert out.count('class="slide') == 3
        assert "<style>.slide{width:1px}</style>" in out

    def test_duplicate_slides_are_collapsed(self):
        out = oc._finalize_presentation_message("```html\n<html><body>" + S1 + S1 + "</body></html>\n```")

        assert out.count("<div>one</div>") == 1

    def test_empty_head_gets_fallback_chrome(self):
        out = oc._finalize_presentation_message("<html><head></head><body>" + S1 + "</body></html>")

        assert ".slide{width:297mm" in out

    def test_head_without_style_gets_fallback_appended(self):
        text = '<html><head><link rel="stylesheet" href="a.css"></head><body>' + S1 + "</body></html>"

        out = oc._finalize_presentation_message(text)

        assert 'href="a.css"' in out and ".slide{width:297mm" in out

    def test_unbalanced_last_slide_is_dropped(self):
        broken = '<div class="slide"><div>broken'

        out = oc._finalize_presentation_message("```html\n<html><body>" + S1 + S2 + broken)

        assert "broken" not in out and "two" in out

    def test_unbalanced_title_slide_is_kept(self):
        broken_title = '<div class="slide title-slide"><div>thanks'

        out = oc._finalize_presentation_message("```html\n<html><body>" + S1 + broken_title)

        assert "thanks" in out

    def test_unbalanced_thank_you_slide_is_kept(self):
        broken = '<div class="slide"><div>Спасибо за внимание'

        out = oc._finalize_presentation_message("```html\n<html><body>" + S1 + broken)

        assert "Спасибо за внимание" in out

    def test_deck_markers_without_any_start_return_original(self):
        text = "--deck-x .slide{} обычный текст"

        assert oc._finalize_presentation_message(text) == text

    def test_fragment_losing_its_slide_element_after_cleaning_is_skipped(self):
        # корневой элемент слайда — <body class="slide">: тег вырезается очисткой
        text = '<body class="slide">без слайда</body>'

        assert oc._finalize_presentation_message(text) == text

    def test_only_non_slide_fragments_return_original(self):
        text = "<div class='slide-title-only'>x</div> <section class='deck-slide'>y</section> --deck-x .slide{}"

        assert oc._finalize_presentation_message(text) == text


class TestOpenForContinue:
    def test_closes_removed_and_fence_stripped(self):
        out = oc._open_presentation_for_continue("```html\n" + S1 + S2 + "\n</body></html>\n```")

        assert out.endswith("</div>") and "</html>" not in out and not out.endswith("```")

    def test_unbalanced_last_slide_is_cut_off(self):
        broken = '<div class="slide"><div>broken'

        out = oc._open_presentation_for_continue("```html\n" + S1 + broken)

        assert "broken" not in out and "one" in out

    def test_trailing_html_only_and_body_only_closers(self):
        assert oc._open_presentation_for_continue(S1 + "</html>") == S1
        assert oc._open_presentation_for_continue(S1 + "</body>") == S1

    def test_empty(self):
        assert oc._open_presentation_for_continue(None) == ""
