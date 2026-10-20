"""Late descriptor effects remain inside the original fresh source snapshot."""
import pytest

from lantern_meshticulum.sage import forward_native_relay as native
from tests.integrations.sage.test_forward_native_relay import relay_rig


@pytest.mark.parametrize('member', ['__code__', '__defaults__', '__kwdefaults__'])
def test_later_method_descriptor_cannot_mutate_an_earlier_source_pin(tmp_path, monkeypatch, member):
    with relay_rig(tmp_path) as r:
        function = native._structural_values
        before = getattr(function, member)
        replacement = (lambda *a, **kw: ()).__code__ if member == '__code__' else \
            (None,) if member == '__defaults__' else {'unused': None}
        cls = type(r.intention)
        original_method = cls.to_dict
        invoked = []

        class LaterDescriptor:
            def __get__(self, instance, owner):
                invoked.append(True)
                setattr(function, member, replacement)
                return original_method

        with monkeypatch.context() as patch:
            patch.setattr(cls, 'to_dict', LaterDescriptor())
            try:
                with pytest.raises(native.ForwardNativeRelayError):
                    r.owner._available()
                assert invoked
            finally:
                setattr(function, member, before)
        r.owner._available()
        assert not r.state.rows and r.accounting.debt == native.Cost(7, 7000, 3, 11, 13)
