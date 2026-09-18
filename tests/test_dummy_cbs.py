from ivr.auth.cbs_dummy import MPIN_LOCK_THRESHOLD, DummyCBSClient
from ivr.auth.models import AccountFlag, MpinCheckResult


def test_resolve_cif_by_mobile_match(cbs):
    assert cbs.resolve_cif_by_mobile("9876500001") == "CIF1001"


def test_resolve_cif_by_mobile_no_match(cbs):
    assert cbs.resolve_cif_by_mobile("0000000000") is None


def test_resolve_cif_by_identity_match(cbs):
    assert cbs.resolve_cif_by_identity("789012", "1111") == "CIF1001"


def test_resolve_cif_by_identity_no_match(cbs):
    assert cbs.resolve_cif_by_identity("000000", "0000") is None


def test_get_registered_mobile_missing_returns_none(cbs):
    assert cbs.get_registered_mobile("CIF1004") is None


def test_get_account_flags(cbs):
    assert cbs.get_account_flags("CIF1003") == {AccountFlag.WATCHLIST}
    assert cbs.get_account_flags("CIF1001") == set()


def test_validate_mpin_correct_resets_fail_count(cbs):
    customer = cbs.get_customer("CIF1001")
    customer.mpin_fail_count = 2
    assert cbs.validate_mpin("CIF1001", "4321") == MpinCheckResult.CORRECT
    assert customer.mpin_fail_count == 0


def test_validate_mpin_incorrect_increments_fail_count(cbs):
    assert cbs.validate_mpin("CIF1001", "0000") == MpinCheckResult.INCORRECT
    assert cbs.get_customer("CIF1001").mpin_fail_count == 1


def test_validate_mpin_locks_after_threshold(cbs):
    for _ in range(MPIN_LOCK_THRESHOLD - 1):
        assert cbs.validate_mpin("CIF1001", "0000") == MpinCheckResult.INCORRECT
    assert cbs.validate_mpin("CIF1001", "0000") == MpinCheckResult.LOCKED
    assert cbs.get_customer("CIF1001").mpin_locked is True


def test_validate_mpin_pre_locked_customer_stays_locked_even_if_correct(cbs):
    assert cbs.validate_mpin("CIF1005", "1357") == MpinCheckResult.LOCKED


def test_custom_customer_list_overrides_default_fixtures():
    from ivr.auth.cbs_dummy import CustomerRecord

    custom = DummyCBSClient([
        CustomerRecord(
            cif="X1",
            name="Test Person",
            registered_mobiles=["1112223333"],
            mpin="0000",
            account_number="99999999999999",
            card_number="9999999999999999",
        )
    ])
    assert custom.resolve_cif_by_mobile("1112223333") == "X1"
    assert custom.resolve_cif_by_mobile("9876500001") is None  # default fixture data not present
