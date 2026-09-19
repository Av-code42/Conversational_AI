from ivr.telephony.digit_normalizer import normalize_spoken_digits


def test_plain_digit_words():
    assert normalize_spoken_digits("four three two one") == "4321"


def test_already_digits():
    assert normalize_spoken_digits("456789") == "456789"


def test_mixed_digit_words_and_numerals():
    assert normalize_spoken_digits("seven eight nine zero one two") == "789012"


def test_ignores_stray_number_before_the_real_answer():
    # Regression: used to concatenate the "6" from "last 6 digits" onto the
    # real answer, producing "6456789" instead of "456789".
    assert normalize_spoken_digits("my last 6 digits for the account is 456789") == "456789"


def test_ignores_stray_digit_word_before_the_real_answer():
    assert normalize_spoken_digits("my four digit pin is one one one one") == "1111"


def test_picks_the_longer_run_regardless_of_order():
    assert normalize_spoken_digits("456789 is my account number, six digits") == "456789"


def test_tie_keeps_the_last_run():
    assert normalize_spoken_digits("not 1234, i mean 5678") == "5678"


def test_no_digits_returns_empty_string():
    assert normalize_spoken_digits("what's my balance") == ""


def test_empty_input_returns_empty_string():
    assert normalize_spoken_digits("") == ""


def test_punctuation_is_ignored():
    assert normalize_spoken_digits("4-3-2-1") == "4321"
