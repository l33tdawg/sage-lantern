# SPDX-License-Identifier: Apache-2.0
"""Optional aggregate CPU timings; opens no state, authority or transport."""
from functools import wraps
import os
import threading
from time import monotonic_ns
from types import BuiltinFunctionType

_CLOCK=monotonic_ns
_NAMES=('native_current','native_available','native_validate','sql_active_start',
        'sql_enrollment','sql_claims','sql_resource','approval_bound','bundle_context',
        'owner_reader','runtime_reader')
_MAX=(1<<63)-1


class AggregateTiming:
    """Fixed-size counters. Nested elapsed times are inclusive, not additive."""
    __slots__=('_clock','_clock_ref','_owner','_rows','_row_refs')
    def __init__(self):
        if (type(_CLOCK) is not BuiltinFunctionType or _CLOCK.__module__!='time'
                or _CLOCK.__name__!='monotonic_ns'):
            raise RuntimeError('invalid_phase_performance_clock')
        self._clock=self._clock_ref=_CLOCK
        self._owner=(os.getpid(),threading.get_ident())
        self._rows={name:[0,0] for name in _NAMES}
        self._row_refs=tuple(self._rows.items())
    def start(self):
        if (self._clock is not self._clock_ref or self._clock_ref is not _CLOCK
                or type(self._clock_ref) is not BuiltinFunctionType
                or self._clock_ref.__module__!='time' or self._clock_ref.__name__!='monotonic_ns'
                or self._owner!=(os.getpid(),threading.get_ident())
                or type(self._rows) is not dict or type(self._row_refs) is not tuple or len(self._row_refs)!=len(_NAMES)
                or tuple(self._rows)!=_NAMES
                or any(self._rows.get(name) is not row for name,row in self._row_refs)):
            raise RuntimeError('phase_timing_owner_changed')
        return self._clock_ref()
    def finish(self,name,started):
        elapsed=max(0,(self.start()-started)//1000)
        row=self._rows[name]
        if type(row) is not list or len(row)!=2 or any(type(v) is not int or not 0<=v<=_MAX for v in row):
            raise RuntimeError('phase_timing_counter_changed')
        row[0]=min(_MAX,row[0]+1);row[1]=min(_MAX,row[1]+elapsed)
    def snapshot(self):
        self.start()
        return dict(clock_basis='host_monotonic_ns',nested_elapsed_times_inclusive=True,
                    phases={name:dict(calls=row[0],elapsed_us=row[1]) for name,row in self._row_refs})


def call(profile,name,function,*args,**kwargs):
    if profile is None:return function(*args,**kwargs)
    if type(profile) is not AggregateTiming:raise RuntimeError('invalid_phase_timing')
    started=profile.start()
    try:result=function(*args,**kwargs)
    except BaseException:
        # Telemetry must not replace an original authority/SQL exception.
        try:profile.finish(name,started)
        except Exception:pass
        raise
    profile.finish(name,started)
    return result


def phase(name):
    if name not in _NAMES:raise ValueError('unknown_phase_timing')
    def decorate(function):
        @wraps(function)
        def measured(self,*args,**kwargs):
            return call(getattr(self,'_phase_timing',None),name,function,self,*args,**kwargs)
        return measured
    return decorate


def enable(caps):
    """Called only for an explicitly selected already-admitted public run."""
    from .pilot_native_caps import NativeCaps,_Public32Approval
    from .gfsk_batch import BatchApproval as GfskApproval
    from .sf5_batch import BatchApproval as Sf5Approval
    if type(caps) is not NativeCaps or type(caps._approve) not in (_Public32Approval,GfskApproval,Sf5Approval):
        raise RuntimeError('phase_timing_requires_public_native_owner')
    profile=AggregateTiming();caps._phase_timing=profile
    caps._approve._phase_timing=profile
    context=getattr(caps._approve,'bundle',None)
    if context is not None:context._phase_timing=profile
    return profile
