"""An inventory is never launch authority or physical acceptance."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('review_desktop_launch',
    ROOT / 'scripts/lantern/review-desktop-launch.py')
MODULE = importlib.util.module_from_spec(spec); spec.loader.exec_module(MODULE)


def inventory():
    return json.loads((ROOT / 'work/c-desktop-launch-closure-20261002/launch-inventory.json').read_text())


def test_actual_inventory_reports_open_routes_and_does_not_accept_stock_seat():
    result = MODULE.review(inventory())
    assert result['observed_stock_shell_controls_seat'] is True
    assert {entry['code'] for entry in result['findings']} >= {
        'stock_session_route', 'user_writable_launch_parent',
        'user_unit_precedence', 'unconfined_compositor'}
    for key in ('candidate_seat_qualified', 'alternate_launch_closed', 'desktop_pin_accepted', 'deployable'):
        assert result[key] is False


def test_absent_routes_or_claimed_receipts_never_become_acceptance():
    value = inventory()
    value.update(files={}, paths={}, user_unit_path=dict(rc=0, stdout='UnitPath=/usr/lib/systemd/user\n'),
        shell_security_label='claimed-confined', seat_qualified=True, alternate_launch_closed=True)
    result = MODULE.review(value)
    assert result['findings'] == []
    assert result['candidate_seat_qualified'] is result['alternate_launch_closed'] is False


@pytest.mark.parametrize('change', ['changed_lifecycle', 'false_lifecycle', 'mutation', 'wrong_schema'])
def test_mismatched_or_mutating_inventory_refuses(change):
    value = copy.deepcopy(inventory())
    if change == 'changed_lifecycle':
        value['lifecycle_after']['boot_id'] = 'different'
    elif change == 'false_lifecycle':
        value['same_lifecycle'] = False
    elif change == 'mutation':
        value['live_changes'] = ['started-session']
    else:
        value['schema'] = 'other'
    with pytest.raises(ValueError, match='read_only_inventory'):
        MODULE.review(value)
