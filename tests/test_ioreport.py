import pytest

from actop.ioreport import IOReportSubscription, cf_release, cfstr, from_cfstr

pytestmark = pytest.mark.local


def test_cfstr_from_cfstr_roundtrip():
    test_str = "Hello, IOReport!"
    ref = cfstr(test_str)
    assert ref is not None
    result = from_cfstr(ref)
    cf_release(ref)
    assert result == test_str


def test_subscription_sample_returns_ref():
    sub = IOReportSubscription([("Energy Model", None)])
    try:
        sample = sub.sample()
        assert sample is not None
        cf_release(sample)
    finally:
        sub.close()
