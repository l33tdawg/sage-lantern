import importlib.util
import io
import json
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location('gnss_replay', Path(__file__).parents[1] / 'lantern/cf-gnss-replay.py')
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)


def sentence(body):
    check = 0
    for byte in body.encode(): check ^= byte
    return '$' + body + f'*{check:02X}\r\n'


def rmc(time='120000.000', date='270926', status='A', mode='A', lat='0310.0000', ns='N', lon='10140.0000', ew='E', talker='GN'):
    return sentence(f'{talker}RMC,{time},{status},{lat},{ns},{lon},{ew},0.0,0.0,{date},,,{mode},V')


def feed(tracker, value=None, when=1, wall='2026-09-27T12:00:00Z', trusted=True):
    return tracker.feed(value or rmc(), when, wall, trusted)


def test_valid_sample_is_never_hardware_or_navigation_acceptance():
    result = feed(G.Tracker())
    assert result['status'] == 'valid_sample'
    assert result['position']['latitude'] == pytest.approx(3 + 10 / 60)
    assert result['position']['longitude'] == pytest.approx(101 + 40 / 60)
    assert result['position']['navigation_status'] == 'V'
    assert result['current_position_verified'] is result['hardware_accepted'] is result['navigation_safety_verified'] is False


@pytest.mark.parametrize('status,mode', [('V','N'), ('A','E'), ('A','N')])
def test_invalid_or_dead_reckoned_status_revokes_previous_position(status, mode):
    tracker = G.Tracker();feed(tracker)
    result = feed(tracker, rmc(status=status, mode=mode), when=2)
    assert result['status'] == 'no_fix' and result['position'] is None


@pytest.mark.parametrize('fault', [lambda s:s[:-4]+'00\r\n', lambda s:s[1:], lambda s:s+'junk', lambda s:s.replace('GNRMC','GARMX'), lambda s:'$'+'x'*200])
def test_bad_checksum_framing_or_unknown_talker_revokes_fix(fault):
    tracker=G.Tracker();feed(tracker)
    assert feed(tracker,fault(rmc()),when=2)['position'] is None


def test_receipt_age_cannot_be_refreshed_by_gga_or_unrelated_sentences():
    tracker=G.Tracker();feed(tracker)
    gga=sentence('GNGGA,120000.000,0310.0000,N,10140.0000,E,1,08,1.0,10.0,M,0.0,M,,')
    assert feed(tracker,gga,when=3)['position']['age_seconds'] == 2
    assert feed(tracker,sentence('GNGSV,1,1,00'),when=5)['status'] == 'stale'
    assert tracker.snapshot(6)['position'] is None


def test_gga_never_creates_position_and_invalid_gga_revokes_rmc():
    gga=sentence('GNGGA,120000.000,,,,,0,00,,,,,,,')
    tracker=G.Tracker();assert feed(tracker,gga)['position'] is None
    tracker=G.Tracker();feed(tracker)
    assert feed(tracker,gga,when=2)['status'] == 'no_fix'


@pytest.mark.parametrize('wall,trusted', [(None,False),('2026-09-27T12:00:00Z',False),('2026-09-26T12:00:00Z',True),('2026-09-27T12:00:10Z',True)])
def test_untrusted_wall_clock_and_replayed_old_date_never_qualify(wall,trusted):
    assert feed(G.Tracker(),wall=wall,trusted=trusted)['position'] is None


def test_repeated_timestamp_does_not_refresh_age_and_restart_requires_new_tracker():
    tracker=G.Tracker();feed(tracker)
    assert feed(tracker,when=2)['position'] is None
    assert feed(tracker,rmc(time='120001.000'),when=3,wall='2026-09-27T12:00:01Z')['status']=='valid_sample'


def test_clock_rollback_and_stale_observations_cannot_resurrect_position():
    tracker=G.Tracker();feed(tracker,when=10)
    assert tracker.snapshot(9)['status']=='receipt_clock_reversed'
    assert tracker.snapshot(11)['position'] is None
    tracker=G.Tracker();feed(tracker)
    assert tracker.snapshot(5)['status']=='stale'
    assert tracker.snapshot(2)['position'] is None


@pytest.mark.parametrize('kwargs', [{'lat':'9060.0'},{'lat':'9000.0001'},{'lon':'18100.0'},{'lon':'10160.0'},{'ns':'E'},{'ew':'N'},{'lat':'nan'},{'date':'310226'},{'time':'126000.0'},{'date':'000026'}])
def test_invalid_coordinate_date_or_time_is_not_output(kwargs):
    assert feed(G.Tracker(),rmc(**kwargs))['position'] is None


def test_south_west_and_boundary_coordinates():
    result=feed(G.Tracker(),rmc(lat='9000.0',ns='S',lon='18000.0',ew='W'))
    assert result['position']['latitude']==-90 and result['position']['longitude']==-180


def test_midnight_advancing_date_accepted_and_receiver_reversal_rejected():
    tracker=G.Tracker()
    assert feed(tracker,rmc(time='235959.0'),wall='2026-09-27T23:59:59Z')['position']
    assert feed(tracker,rmc(time='000000.0',date='280926'),when=2,wall='2026-09-28T00:00:00Z')['position']
    assert feed(tracker,rmc(time='235959.0'),when=3,wall='2026-09-27T23:59:59Z')['position'] is None


@pytest.mark.parametrize('date,trusted', [('270999',True),('270999',False),('270926',False)])
def test_rejected_untrusted_or_future_sample_cannot_poison_accepted_time(date,trusted):
    tracker=G.Tracker();feed(tracker)
    assert feed(tracker,rmc(time='120002.0',date=date),when=2,trusted=trusted)['position'] is None
    recovered=feed(tracker,rmc(time='120001.0'),when=3,wall='2026-09-27T12:00:01Z')
    assert recovered['status']=='valid_sample'


def test_replay_is_stdin_only_and_expiry_is_explicit():
    source='\n'.join(json.dumps(row) for row in [dict(sentence=rmc(),received_monotonic=1,received_utc='2026-09-27T12:00:00Z',clock_trusted=True),{'now':5}])
    out=io.StringIO();G.replay(io.StringIO(source),out)
    rows=[json.loads(line) for line in out.getvalue().splitlines()]
    assert [r['status'] for r in rows]==['valid_sample','stale']
    assert all(r['source']=='offline-replay' and not r['current_position_verified'] for r in rows)


@pytest.mark.parametrize('raw', ['{"now":1,"now":2}', '{"now":NaN}', '{"device":"/dev/ttyS4"}', '{"now":-1}', 'x'*4097])
def test_unbounded_nonfinite_duplicate_or_device_input_refuses(raw):
    with pytest.raises((ValueError,TypeError)):G.replay(io.StringIO(raw),io.StringIO())
