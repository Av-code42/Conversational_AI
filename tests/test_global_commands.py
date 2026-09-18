import pytest

from ivr.shared.global_commands import GlobalCommand, detect_global_command, is_no_further_help_needed


@pytest.mark.parametrize("text", ["agent", "I want an agent", "representative please", "human", "operator"])
def test_escalate_words_detected(text):
    assert detect_global_command(text) == GlobalCommand.ESCALATE_TO_CSR


@pytest.mark.parametrize("text", ["customer service", "talk to someone", "speak to a person"])
def test_escalate_phrases_detected(text):
    assert detect_global_command(text) == GlobalCommand.ESCALATE_TO_CSR


@pytest.mark.parametrize("text", ["restart", "start over", "start again", "main menu"])
def test_start_over_detected(text):
    assert detect_global_command(text) == GlobalCommand.START_OVER


@pytest.mark.parametrize("text", ["repeat", "pardon", "say that again", "come again"])
def test_repeat_detected(text):
    assert detect_global_command(text) == GlobalCommand.REPEAT


@pytest.mark.parametrize("text", ["balance enquiry", "I want to check my balance", "cheque book please", ""])
def test_ordinary_utterances_do_not_match(text):
    assert detect_global_command(text) is None


# The critical regression: digit-heavy factor-capture input (MPIN, OTP,
# account/card digits) must never false-positive as a global command, since
# "0"/"zero" are completely ordinary digits to say. This is precisely why
# AuthFlow never calls detect_global_command on its own turns -- see the
# module docstring in global_commands.py.
@pytest.mark.parametrize(
    "text",
    ["0000", "789012", "4321", "zero one two three", "one zero zero zero", "9876500001"],
)
def test_digit_strings_never_match_as_global_commands(text):
    assert detect_global_command(text) is None


@pytest.mark.parametrize(
    "text",
    ["no", "nope", "bye", "goodbye", "no thanks", "that's all", "nothing else", "I'm done"],
)
def test_no_further_help_needed_detected(text):
    assert is_no_further_help_needed(text)


@pytest.mark.parametrize("text", ["I need a new chequebook", "what's my balance", ""])
def test_no_further_help_needed_not_detected_for_real_requests(text):
    assert not is_no_further_help_needed(text)


def test_no_further_help_needed_is_not_a_global_command():
    # "no idea what to do" legitimately contains the word "no" -- this must
    # NOT be treated as "caller is done" by detect_global_command, since
    # that check runs unconditionally at every intent-capture turn, not
    # just right after "anything else?". is_no_further_help_needed is
    # deliberately separate and only ever called by CoordinatorFlow in that
    # specific context -- see its docstring in global_commands.py.
    assert detect_global_command("no idea what to do") is None
