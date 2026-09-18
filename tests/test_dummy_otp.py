from ivr.auth.models import OTPCheckResult
from ivr.auth.otp_dummy import DummyOTPGateway


def test_send_then_validate_correct_code():
    gw = DummyOTPGateway()
    ref = gw.send_otp("9876500001")
    code = gw.last_code_for(ref)
    assert gw.validate_otp(ref, code) == OTPCheckResult.CORRECT


def test_wrong_code_is_incorrect():
    gw = DummyOTPGateway()
    ref = gw.send_otp("9876500001")
    assert gw.validate_otp(ref, "000000") == OTPCheckResult.INCORRECT


def test_unknown_reference_is_not_found():
    gw = DummyOTPGateway()
    assert gw.validate_otp("no-such-reference", "123456") == OTPCheckResult.NOT_FOUND


def test_code_is_single_use(clock):
    gw = DummyOTPGateway(clock=clock)
    ref = gw.send_otp("9876500001")
    code = gw.last_code_for(ref)
    assert gw.validate_otp(ref, code) == OTPCheckResult.CORRECT
    assert gw.validate_otp(ref, code) == OTPCheckResult.NOT_FOUND


def test_code_expires_after_ttl(clock):
    gw = DummyOTPGateway(clock=clock, ttl_seconds=180)
    ref = gw.send_otp("9876500001")
    code = gw.last_code_for(ref)
    clock.advance(181)
    assert gw.validate_otp(ref, code) == OTPCheckResult.EXPIRED


def test_code_still_valid_just_under_ttl(clock):
    gw = DummyOTPGateway(clock=clock, ttl_seconds=180)
    ref = gw.send_otp("9876500001")
    code = gw.last_code_for(ref)
    clock.advance(179)
    assert gw.validate_otp(ref, code) == OTPCheckResult.CORRECT


def test_resend_issues_a_distinct_reference(clock):
    # The gateway itself doesn't proactively invalidate the old reference --
    # AuthFlow enforces "only the latest reference is live" by simply never
    # using the old one again once it resends (see test_auth_flow.py's
    # resend tests). This just checks each send_otp call is independent.
    gw = DummyOTPGateway(clock=clock)
    ref1 = gw.send_otp("9876500001")
    ref2 = gw.send_otp("9876500001")
    assert ref2 != ref1
    assert gw.validate_otp(ref1, gw.last_code_for(ref1)) == OTPCheckResult.CORRECT
    assert gw.validate_otp(ref2, gw.last_code_for(ref2)) == OTPCheckResult.CORRECT
