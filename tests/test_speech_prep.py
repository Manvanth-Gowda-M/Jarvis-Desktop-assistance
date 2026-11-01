import pytest
from assistant.speech import prepare_spoken_answer


def test_speech_prep_empty():
    assert prepare_spoken_answer("") == "I've finished processing your request."
    assert prepare_spoken_answer(None) == "I've finished processing your request."


def test_speech_prep_code_removal():
    text = "Here is the calculation:\n```python\nprint(1 + 1)\n```\nThe answer is 2."
    spoken = prepare_spoken_answer(text)
    assert "print(1 + 1)" not in spoken
    assert "```" not in spoken


def test_speech_prep_url_and_markdown_cleaning():
    text = (
        "Check [Google](https://www.google.com) for details.\n"
        "Sources: https://example.com/source\n"
        "- Item 1\n"
        "- Item 2\n"
        "**Bold text** and *italic text*."
    )
    spoken = prepare_spoken_answer(text)
    assert "https://" not in spoken
    assert "Sources:" not in spoken
    assert "**" not in spoken
    assert "Google for details" in spoken


def test_speech_prep_sentence_truncation():
    text = (
        "This is sentence one. "
        "This is sentence two. "
        "This is sentence three, which is quite long and detailed. "
        "This is sentence four."
    )
    spoken = prepare_spoken_answer(text, max_chars=300)
    assert "sentence one" in spoken
    assert "sentence two" in spoken
    assert "sentence four" not in spoken
    assert "More details are in the terminal." in spoken
