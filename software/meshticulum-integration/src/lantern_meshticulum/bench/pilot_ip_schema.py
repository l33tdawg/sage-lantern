# SPDX-License-Identifier: Apache-2.0
"""Exact IP metadata decoding; never admission, keys, clocks or dispatch.

The zero secret is used only by Core's pure plan parser. Grant context excludes
it. This description cannot open an authority or substitute for a native claim.
"""
from meshticulum.contracts.identity import PeerID
from meshticulum.ip.authority import grant_from_plan
from meshticulum.ip.wire import IpLimits, require

IP_V1='meshticulum.ip-service-run.v1'
IP_V2='meshticulum.ip-service-run.v2'
IP_SCHEMAS=(IP_V1,IP_V2)
RADIO_SCHEMAS=('meshticulum.bench-run.v1','meshticulum.service-run.v1')


def ip_metadata_grant(plan):
    require(type(plan) is dict and plan.get('schema') in IP_SCHEMAS,'invalid_pilot_ip_schema')
    require(type(plan.get('limits')) is dict and set(plan['limits'])==set(IpLimits.__dataclass_fields__),
        'invalid_pilot_ip_limits')
    return grant_from_plan(plan,bytes(32),local_peer=PeerID(**plan['local_peer']),host_boot_id=plan['host_boot_id'])
