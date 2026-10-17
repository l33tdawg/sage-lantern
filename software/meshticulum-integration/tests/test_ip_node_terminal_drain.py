"""Actual child TCP jobs with a real final-DATA retransmission before close."""
import json
import subprocess
import sys

import pytest

from meshticulum.bench.services import resource
from tests.test_ip_transport import BOOT
from tests.test_ip_node import CHILD, prepare, ready, reap


# This delays the receiver's first complete ACK pump under the existing grant.
# The unchanged sender's ReliableFlow must perform an actual timed retransmit.
# No frame, receipt, authority decision, budget or deadline is manufactured.
DELAYED_CHILD = '''from meshticulum.ip.flow import IpFlow
import time
original_pump=IpFlow._pump
def delayed_final_ack(self):
 if not self.sends and self.flow.complete and not getattr(self,'test_delayed_final_ack',False):
  self.test_delayed_final_ack=True
  time.sleep(.65)
 return original_pump(self)
IpFlow._pump=delayed_final_ack
''' + CHILD


@pytest.mark.parametrize('format', ['MR1.request', 'MR1.response'])
def test_actual_timed_retry_does_not_reset_terminal_public_jobs(tmp_path, format):
    request = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 16356)
    payload = request if format == 'MR1.request' else resource.BlockServer(lambda _: b'x' * 16356).handle(request)
    directories, grant = prepare(tmp_path, payload, format)
    def start(directory, unit):
        return subprocess.Popen([sys.executable, '-B', '-c', DELAYED_CHILD,
                                 unit, str(directory), BOOT], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    receiver = start(directories[1], 'unit-b'); sender = None
    try:
        ready(receiver, directories[1])
        sender = start(directories[0], 'unit-a')
        outputs = []
        for process in (sender, receiver):
            out, err = process.communicate(timeout=10)
            assert process.returncode == 0, (out, err)
            outputs.append(json.loads(out))
        assert outputs[0]['flow']['retransmits'] >= 1
        for result in outputs:
            assert result['complete'] and result['flow']['complete']
            assert result['facts_before'] == result['facts_after']
            assert not result.get('cleanup_errors')
            assert not result['radio_opened'] and not result['publisher_verified'] and not result['private_admission']
            assert result['original_application_expires_epoch_us'] == grant.application_expires_epoch_us
            assert not result['byte_journal']['fenced']
            assert result['byte_journal']['pending_receive_stream_bytes'] == 0
            assert result['byte_journal']['unknown_receive_stream_bytes'] == 0
            assert result['test_tcp_loss']['known_rx_stream_bytes'] == result['byte_journal']['received_stream_bytes']
            assert not result['byte_journal']['peer_receipt_established']
            assert result['parent_caps']['starts'] == 1
        assert outputs[0]['byte_journal']['known_kernel_accepted_stream_bytes'] == outputs[1]['byte_journal']['received_stream_bytes']
        assert outputs[1]['byte_journal']['known_kernel_accepted_stream_bytes'] == outputs[0]['byte_journal']['received_stream_bytes']
        assert (directories[1] / 'receiver.bin').read_bytes() == payload
        assert outputs[1]['application_status'] == 'public_block_quarantined'
    finally:
        reap(sender); reap(receiver)
