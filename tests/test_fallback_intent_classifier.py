from ivr.coordinator.fallback_intent_classifier import FallbackIntentClassifier


class _StubClassifier:
    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.called_with = None

    def classify(self, text):
        self.called_with = text
        if self.raises:
            raise self.raises
        return self.result


def test_uses_primary_result_when_it_succeeds():
    primary = _StubClassifier(result="balance_enquiry")
    fallback = _StubClassifier(result="statement_request")
    classifier = FallbackIntentClassifier(primary=primary, fallback=fallback)

    assert classifier.classify("what's my balance") == "balance_enquiry"
    assert fallback.called_with is None  # never invoked


def test_falls_back_when_primary_raises():
    primary = _StubClassifier(raises=RuntimeError("API down"))
    fallback = _StubClassifier(result="statement_request")
    classifier = FallbackIntentClassifier(primary=primary, fallback=fallback)

    assert classifier.classify("can I get a statement") == "statement_request"
    assert fallback.called_with == "can I get a statement"


def test_primary_returning_none_is_not_treated_as_failure():
    # A confident "no match" from the primary is a real answer, not an
    # error -- shouldn't trigger the fallback.
    primary = _StubClassifier(result=None)
    fallback = _StubClassifier(result="should_not_be_used")
    classifier = FallbackIntentClassifier(primary=primary, fallback=fallback)

    assert classifier.classify("what time is it") is None
    assert fallback.called_with is None
