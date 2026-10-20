# SPDX-License-Identifier: Apache-2.0
"""Worker drift cannot authorize or publish a pending adoption revision.

The fenced-worker regression was independently identified by Ember's source
review. These controls use the existing native/SDK models and owned PTYs.
"""
import base64
import os

import pytest

from lantern_meshticulum.sage import radio_native_adoption as adoption_module
from lantern_meshticulum.sage.forward_radio import ForwardRadioError
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_forward_radio_authorize_action import authority_clock, authorize
from tests.integrations.sage.test_forward_radio_facts_action import active, retained


@pytest.mark.parametrize('drift', ('fenced', 'identity'))
def test_worker_drift_during_original_authority_callback_refuses_publication(tmp_path, monkeypatch, drift):
    with authority_clock(monkeypatch) as clock, active(tmp_path, monkeypatch) as rig:
        before = rig.adoption._highest, rig.adoption._record
        row = rig.state.rows[rig.adoption.record_id]
        body = row['payload']['body']
        staged = row['revision'] + 1
        row['revision'] = body['revision'] = staged
        row['payload']['signature'] = base64.b64encode(rig.hardware.identity._signing_key.sign(
            adoption_module.DOMAIN + _canonical(body)).signature).decode()
        mutated = []

        def change_worker():
            if drift == 'fenced':
                rig.stage.worker.fenced = True
            else:
                rig.stage.worker.identity = object()
            mutated.append(True)

        clock['callback'] = change_worker
        clock['enabled'] = True
        try:
            with pytest.raises(ForwardRadioError):
                authorize(rig)
        finally:
            clock['enabled'] = False

        assert mutated == [True]
        assert (rig.adoption._highest, rig.adoption._record) == before
        assert rig.adoption._highest != staged and rig.stage._unknown
        assert all(os.fstat(fd).st_ino == inode for fd, inode in rig.saved_fds)
        retained(rig)
