"""Unit tests for live_session.build_config's quiz-related wiring, the
QUIZ_TOOL schema itself, and the small pure helpers around the correct_
answers omission fix. Doesn't need the
FakeLiveSession/websocket machinery test_live_session_routing.py uses,
since everything here is a pure function of its arguments.

Deliberately does NOT assert that any particular wording/phrase appears in
a system_instruction string - prompt text gets rewritten often and a
wording assertion tests nothing about actual model behavior, only that
nobody rephrased a sentence. Tests here check structural/conditional
behavior instead: a block is included or excluded based on input, dynamic
values actually get interpolated, and the tool schema declares what it's
supposed to.
"""

from thirtytutors import live_session, quizzes
from thirtytutors.tutor_instructions import SPACED_REPETITION_CONTEXT_TEMPLATE, TAUGHT_VOCAB_CONTEXT_TEMPLATE
from thirtytutors.tutor_tools import build_quiz_tool


def _minimal_profile():
    return {"id": "profile-1", "name": "Alex", "api_key": "fake-key"}


def _minimal_conv_config():
    return {"target_language": "Spanish", "native_language": "English"}


def _system_instructions(review_terms=None, taught_vocab=None):
    return live_session._build_instructions(
        _minimal_profile(),
        _minimal_conv_config(),
        None,
        review_terms or [],
        taught_vocab or [],
    )


def test_build_config_declares_both_mood_and_quiz_tools():
    # OpenAI Realtime receives tool schemas directly in session.update.
    import asyncio

    class _Socket:
        def __init__(self):
            self.events = []

        async def send(self, payload):
            import json
            self.events.append(json.loads(payload))

    sock = _Socket()
    asyncio.run(
        live_session._configure_session(
            sock,
            profile=_minimal_profile(),
            conv_config=_minimal_conv_config(),
            model_name="gpt-realtime-2.1",
            summary_text=None,
            review_terms=[],
            taught_vocab=[],
        )
    )
    names = {
        tool["name"]
        for tool in sock.events[0]["session"]["tools"]
        if tool.get("type") == "function"
    }
    assert names == {"set_mood", "start_quiz"}


def test_build_config_omits_spaced_repetition_block_without_review_terms():
    assert "Trouble spots" not in _system_instructions()


def test_build_config_includes_spaced_repetition_block_with_review_terms():
    review_terms = ["el clima", "sin embargo"]
    text = _system_instructions(review_terms=review_terms)
    expected_block = SPACED_REPETITION_CONTEXT_TEMPLATE.format(
        name="Alex", terms=", ".join(review_terms)
    )
    assert expected_block in text


def test_build_config_omits_spaced_repetition_block_with_empty_review_terms():
    assert "Trouble spots" not in _system_instructions(review_terms=[])


def test_build_config_omits_taught_vocab_block_without_taught_vocab():
    assert "Vocabulary already taught" not in _system_instructions()


def test_build_config_includes_taught_vocab_block_with_taught_vocab():
    taught_vocab = ["el clima", "sin embargo"]
    text = _system_instructions(taught_vocab=taught_vocab)
    expected_block = TAUGHT_VOCAB_CONTEXT_TEMPLATE.format(
        name="Alex", terms=", ".join(taught_vocab)
    )
    assert expected_block in text


def test_build_config_omits_taught_vocab_block_with_empty_taught_vocab():
    assert "Vocabulary already taught" not in _system_instructions(review_terms=[], taught_vocab=[])


# --- QUIZ_TOOL schema (design_plans/issues_fix.md: correct_answers omission) ---


def _quiz_item_schema():
    # Structural shape only (required fields, item_type enum) - none of the
    # tests using this depend on the actual language strings passed here,
    # only on _CHOICES_DESC_TEMPLATE/etc. being filled in without error.
    tool = build_quiz_tool(native_language="English", target_language="Spanish")
    params = tool.function_declarations[0].parameters
    return params, params.properties["items"].items


def test_quiz_tool_has_no_top_level_quiz_type():
    """quiz_type used to be a top-level model-supplied field, ambiguous for
    a mixed-type quiz and redundant with the per-item item_type below - now
    computed server-side instead (see quizzes.compute_quiz_type)."""
    params, _ = _quiz_item_schema()
    assert "quiz_type" not in params.properties


def test_quiz_tool_item_schema_requires_every_field():
    """The actual fix for the correct_answers omission bug: rather than two
    mechanic-specific optional field sets (which Gemini's schema can't make
    conditionally required), every item has one flat, fully-required field
    set disambiguated by item_type - so correct_answers can never be
    silently dropped from a fill_blank_dragdrop item."""
    _, item_schema = _quiz_item_schema()
    assert set(item_schema.required) == {
        "target_term",
        "question",
        "item_type",
        "choices",
        "correct_choice_index",
        "text_with_blanks",
        "correct_answers",
        "word_bank",
    }


def test_quiz_tool_item_type_is_the_only_type_enum():
    _, item_schema = _quiz_item_schema()
    assert set(item_schema.properties["item_type"].enum) == {"multiple_choice", "fill_blank_dragdrop"}


# --- quizzes.compute_quiz_type / live_session._validate_quiz_items ---
# compute_quiz_type moved from live_session.py to quizzes.py so the
# standalone Test Yourself review quizzes (routes_api.py's
# reviewable-quiz endpoints) could share it too, rather than each having
# its own copy - see quizzes.py's own docstring for that function.


def test_compute_quiz_type_uniform_multiple_choice():
    items = [{"item_type": "multiple_choice"}, {"item_type": "multiple_choice"}]
    assert quizzes.compute_quiz_type(items) == "multiple_choice"


def test_compute_quiz_type_uniform_dragdrop():
    items = [{"item_type": "fill_blank_dragdrop"}]
    assert quizzes.compute_quiz_type(items) == "fill_blank_dragdrop"


def test_compute_quiz_type_mixed():
    items = [{"item_type": "multiple_choice"}, {"item_type": "fill_blank_dragdrop"}]
    assert quizzes.compute_quiz_type(items) == "mixed"


def test_compute_quiz_type_empty_items_has_a_fallback():
    assert quizzes.compute_quiz_type([]) in {"multiple_choice", "fill_blank_dragdrop", "mixed"}


def test_validate_quiz_items_logs_blank_answer_count_mismatch(capsys):
    live_session._validate_quiz_items(
        [{"item_type": "fill_blank_dragdrop", "text_with_blanks": "{0} {1}", "correct_answers": ["only one"]}]
    )
    assert "mismatch" in capsys.readouterr().out


def test_validate_quiz_items_silent_when_counts_match(capsys):
    live_session._validate_quiz_items(
        [{"item_type": "fill_blank_dragdrop", "text_with_blanks": "{0} {1}", "correct_answers": ["a", "b"]}]
    )
    assert capsys.readouterr().out == ""


def test_validate_quiz_items_ignores_multiple_choice_items(capsys):
    live_session._validate_quiz_items([{"item_type": "multiple_choice", "text_with_blanks": "", "correct_answers": []}])
    assert capsys.readouterr().out == ""
