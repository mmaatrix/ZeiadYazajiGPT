"""OpenAI Realtime function-tool schemas used by the tutor."""

MOOD_TOOL = {
    "type": "function",
    "name": "set_mood",
    "description": (
        "Silently express the tutor's emotional reaction to the current "
        "moment so the avatar's face reflects it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mood": {
                "type": "string",
                "enum": ["neutral", "happy", "sad", "fear", "love"],
            }
        },
        "required": ["mood"],
        "additionalProperties": False,
    },
}

_QUESTION_DESC_TEMPLATE = (
    "A short question or context line for the student, fully in {native_language}. "
    "It must not reveal any correct answer. For fill_blank_dragdrop, keep placeholders "
    "only in text_with_blanks, never in question."
)
_CHOICES_DESC_TEMPLATE = (
    "multiple_choice: 2+ answer options. fill_blank_dragdrop: empty array. "
    "Use {target_language}."
)
_WORD_BANK_DESC_TEMPLATE = (
    "fill_blank_dragdrop: all correct answers plus 1-3 distractors, shuffled. "
    "multiple_choice: empty array. Use {target_language}."
)


def build_quiz_tool(*, native_language: str, target_language: str) -> dict:
    fmt = {"native_language": native_language, "target_language": target_language}
    return {
        "type": "function",
        "name": "start_quiz",
        "description": (
            "Open a short quiz to check understanding. Invoke only after saying aloud "
            "that it is time for a quiz. Never invoke it as the first action on a new "
            "topic and do not combine it with a spoken repeat-after-me question in the "
            "same turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_term": {
                                "type": "string",
                                "description": "The word or phrase this item tests.",
                            },
                            "question": {
                                "type": "string",
                                "description": _QUESTION_DESC_TEMPLATE.format(**fmt),
                            },
                            "item_type": {
                                "type": "string",
                                "enum": ["multiple_choice", "fill_blank_dragdrop"],
                            },
                            "choices": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": _CHOICES_DESC_TEMPLATE.format(**fmt),
                            },
                            "correct_choice_index": {
                                "type": "integer",
                                "description": (
                                    "multiple_choice: index into choices. "
                                    "fill_blank_dragdrop: 0 (ignored)."
                                ),
                            },
                            "text_with_blanks": {
                                "type": "string",
                                "description": (
                                    "fill_blank_dragdrop: sentence with blanks marked "
                                    "{0}, {1}, etc. multiple_choice: empty string."
                                ),
                            },
                            "correct_answers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "fill_blank_dragdrop: one answer per blank in order. "
                                    "multiple_choice: empty array."
                                ),
                            },
                            "word_bank": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": _WORD_BANK_DESC_TEMPLATE.format(**fmt),
                            },
                        },
                        "required": [
                            "target_term",
                            "question",
                            "item_type",
                            "choices",
                            "correct_choice_index",
                            "text_with_blanks",
                            "correct_answers",
                            "word_bank",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    }
