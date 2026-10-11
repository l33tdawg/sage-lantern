"""Owned local process refusals export bounded metadata; no native proof."""
import sys
import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import bundle_checkpoint_root as root,pilot_ssh as ssh
from tests.test_bundle_checkpoint_adapter import producer,ledger,data,tmp_path,root_observation


@pytest.mark.parametrize('code',(124,137))
def test_real_failed_local_callback_keeps_reason_returncode_and_unproved_remote(producer,monkeypatch,code):
    value,_=producer
    def failed(self,*args,**kwargs):return [sys.executable,'-c','raise SystemExit('+str(code)+')']
    monkeypatch.setattr(ssh.SSHOperator,'_argv',failed)
    value._refs=(*value._refs[:5],failed,*value._refs[6:])  # Explicit failure transport model.
    with pytest.raises(WireError):value._jobs([('unit-a','checkpoint-audit',{})],1)
    rows=value.diagnostics();assert len(rows)==1
    row=rows[0]
    assert row['returncode']==code and row['local_closed'] is True
    assert row['remote_terminal_proven'] is False and row['callback_completed'] is False
    assert row['error_type']=='WireError' and row['reason']=='checkpoint_native_callback_failed'
    assert set(row)=={'unit','action','remote_terminal_proven','remote_end_epoch_us','started_epoch_us','finished_epoch_us',
        'returncode','local_closed','callback_completed','error_type','reason','cleanup_error_type'}


def test_diagnostic_rows_are_bounded_and_do_not_export_arbitrary_fields(producer):
    value,_=producer
    value.callback_attempts=[dict(unit='unit-a',action='checkpoint-audit',raw_body='must not export')]
    assert 'raw_body' not in value.diagnostics()[0]
    value.callback_attempts*=17
    with pytest.raises(WireError):value.diagnostics()


def test_token_shaped_arbitrary_exception_is_redacted(producer,monkeypatch):
    value,_=producer
    def token_error(job):raise WireError('token_shaped_private_value_123')
    monkeypatch.setattr(root.Job,'wait',token_error)
    value._job_refs=(root.Job,root.Job.__init__,token_error,root.Job.close)
    with pytest.raises(WireError):value._jobs([('unit-a','checkpoint-audit',{})],1)
    row=value.diagnostics()[0]
    assert row['reason']=='exception_detail_omitted' and row['remote_terminal_proven'] is False
    assert 'token_shaped_private_value_123' not in str(row)


@pytest.mark.parametrize('accessor',('pid','thread'))
def test_diagnostic_accessor_refuses_foreign_process_or_thread(producer,monkeypatch,accessor):
    value,_=producer
    with monkeypatch.context() as scope:
        if accessor=='pid':
            current=root.os.getpid();scope.setattr(root.os,'getpid',lambda:current+1)
        else:
            current=root.threading.get_native_id();scope.setattr(root.threading,'get_native_id',lambda:current+1)
        with pytest.raises(WireError,match='checkpoint_diagnostic_owner_or_bound_changed'):value.diagnostics()
