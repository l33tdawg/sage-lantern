"""Metrics-only observation around the unchanged public example CLI.

Sender receipt is observed immediately after authenticated flow.absorb applies
the completing ACK. Endpoint completion is separately gated by physical drain.
"""
import ctypes
import json
import os
from pathlib import Path
import runpy
import signal
import sys
import time


def clock():
    return dict(epoch_us=time.time_ns()//1000,monotonic_us=time.monotonic_ns()//1000)


def emit(value):
    print(json.dumps(value,sort_keys=True),flush=True)


def terminate(signum,frame):
    raise SystemExit(128+signum)


def observe_session(session,record,*,unit,part_index,clock=clock,emit=emit):
    """Wrap only observation points; preserve arguments, results and cleanup."""
    record['worker_ready']=clock()
    emit(dict(sdk_qual_event='ready',unit=unit,part=part_index,at=record['worker_ready']))
    endpoint_step,step,close=session.endpoint.step,session.step,session.close
    flow=session.endpoint.flow
    absorb=flow.absorb

    def observed_absorb(*args,**kwargs):
        before=flow.complete
        result=absorb(*args,**kwargs)
        if session.endpoint.sends and not before and flow.complete and 'authenticated_receipt' not in record:
            at=clock()
            record['authenticated_receipt']=at
            record['frozen_flow_complete']=at
            record['authenticated_receipt_boundary']='immediately after authenticated ACK application by flow.absorb; before endpoint physical drain'
        return result

    flow.absorb=observed_absorb

    def observed_endpoint_step():
        result=endpoint_step()
        if result and 'endpoint_complete' not in record:
            at=clock()
            record['endpoint_complete']=at
            record['endpoint_complete_boundary']='BenchFlow.step returned complete after physical settlement/release/STATUS gate'
            record['flow_complete']=at
        return result

    def observed_step():
        result=step()
        if 'first_data' not in record:
            for event in session.endpoint.events:
                if (event.get('event')=='tx' and event.get('outer_kind')=='DATA'
                        and type(event.get('worker_call_elapsed_us')) is int):
                    record['first_data']=dict(worker_call_started_monotonic_us=event['mono_us']-event['worker_call_elapsed_us'],
                        worker_call_finished_monotonic_us=event['mono_us'],outcome=event['outcome'],
                        timing_boundary='host worker call; not independently measured RF start')
                    break
        if 'first_queued_data' not in record:
            for event in session.endpoint.events:
                if event.get('event')=='tx_queued' and event.get('outer_kind')=='DATA':
                    record['first_queued_data']=dict(queue_submit_monotonic_us=event['queue_submit_mono_us'],
                        queue_accept_monotonic_us=event['queue_accept_mono_us'],
                        queue_accept_elapsed_us=event['queue_accept_elapsed_us'],
                        timing_boundary='host first queued DATA submit; separate from local RF_DONE and peer ACK')
                    break
        if session.completed_at is not None and 'receiver_verified' not in record:
            record['receiver_verified']=clock()
        if result and 'session_retired' not in record:
            record['session_retired']=clock()
        return result

    def observed_close():
        try:
            record['flow_snapshot_before_close']=session.endpoint.snapshot()
        finally:
            try:
                value=close()
            finally:
                try:
                    record['flow_snapshot']=session.endpoint.snapshot()
                    worker=session.endpoint.worker
                    if hasattr(worker,'physical_snapshot'):
                        record['physical_diagnostics']=worker.physical_snapshot()
                except Exception as error:
                    record['flow_snapshot']=record.get('flow_snapshot_before_close',{})
                    record['post_close_snapshot_error']=type(error).__name__
        return value

    session.endpoint.step=observed_endpoint_step
    session.step,session.close=observed_step,observed_close
    return session


def main():
    signal.signal(signal.SIGTERM,terminate)
    signal.signal(signal.SIGHUP,terminate)
    parent=int(sys.argv[1]);root=Path(sys.argv[2]);unit=sys.argv[3];part_index=int(sys.argv[4]);argv=sys.argv[5:]
    # A dead management supervisor cannot leave an unbounded orphan worker.
    assert ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGTERM,0,0,0)==0
    assert os.getppid()==parent
    record=dict(schema='meshticulum.public-sdk-entry-metrics.v2',unit=unit,part=part_index,entry_started=clock(),
        authority_or_transport_changed=False,completed=False,
        receipt_timing_boundary='authenticated flow.absorb completion, separately from drained endpoint completion')
    record['runtime_environment']=runpy.run_path(str(root/'verify_environment.py'))['verify'](root,unit)
    sys.path.insert(0,str(root/'examples'))
    import radio_file
    original=radio_file.run_object

    def instrument(args,raw,admission,version,payload,window,factory):
        def observed_factory(authority,worker,journal,cipher):
            return observe_session(factory(authority,worker,journal,cipher),record,
                unit=unit,part_index=part_index)
        return original(args,raw,admission,version,payload,window,observed_factory)

    radio_file.run_object=instrument
    sys.argv=[str(root/'examples/radio_bundle.py'),*argv]
    try:
        runpy.run_path(sys.argv[0],run_name='__main__')
        record['cli_complete']=clock()
        record['completed']=True
    except BaseException as error:
        # Diagnostic export is best effort and never replaces the original failure.
        try:
            value=getattr(error,'host_phase_timings',None)
            if value is not None:record['host_phase_timings']=value
            if getattr(error,'host_phase_timings_unavailable',False):record['host_phase_timings_unavailable']=True
        except Exception:
            record['host_phase_timings_unavailable']=True
        raise
    finally:
        record['entry_finished']=clock()
        emit(dict(sdk_qual_event='metrics',value=record))


if __name__=='__main__':
    main()
