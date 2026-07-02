import pytest

from actop.smc import SMCReader, TemperatureReading

pytestmark = pytest.mark.local


def test_smc_reader_connects_and_discovers_keys():
    reader = SMCReader()
    try:
        assert reader.available is True
    finally:
        reader.close()


def test_smc_reader_returns_temperature_reading():
    reader = SMCReader()
    try:
        temps = reader.read_temperatures()
        assert isinstance(temps, TemperatureReading)
        assert isinstance(temps.cpu_temps_c, list)
        assert isinstance(temps.gpu_temps_c, list)
    finally:
        reader.close()


def test_smc_reader_temperatures_are_physical():
    reader = SMCReader()
    try:
        temps = reader.read_temperatures()
        # On Apple Silicon, we expect at least some CPU and GPU sensors
        assert len(temps.cpu_temps_c) > 0, "No CPU temperature sensors found"
        assert len(temps.gpu_temps_c) > 0, "No GPU temperature sensors found"

        # All returned values must be in the physical range (0, 150)
        for t in temps.cpu_temps_c:
            assert 0.0 < t < 150.0, "CPU temp {:.1f}C out of range".format(t)
        for t in temps.gpu_temps_c:
            assert 0.0 < t < 150.0, "GPU temp {:.1f}C out of range".format(t)
    finally:
        reader.close()


def test_smc_reader_close_is_idempotent():
    reader = SMCReader()
    reader.available  # trigger connection
    reader.close()
    reader.close()  # should not raise


def test_smc_reader_returns_fan_info():
    reader = SMCReader()
    try:
        fans = reader.read_fan_info()
        assert isinstance(fans, list)
        assert isinstance(reader.fan_available, bool)

        if reader.fan_available:
            # A fan-equipped Mac must report at least one tachometer, each a
            # physically plausible current RPM (0 is a legitimate idle
            # reading). max is either None (key absent) or a plausible RPM.
            assert len(fans) > 0
            for fan in fans:
                assert 0.0 <= fan.current < 20000.0
                assert fan.max is None or 0.0 < fan.max < 20000.0
        else:
            # Fanless Macs (e.g. MacBook Air) discover zero fan keys.
            assert fans == []
    finally:
        reader.close()
