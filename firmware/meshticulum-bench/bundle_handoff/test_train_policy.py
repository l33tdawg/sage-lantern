"""Explicit finite train admission; source-only, no radio or SSH."""
import copy
import unittest

import common
from meshticulum.bench.burst_protocol import policy_for_depth


class TrainPolicy(unittest.TestCase):
    def prepared(self, depth):
        return dict(comparison_mode='queued', data_burst=common.data_burst_policy(depth),
            policy=dict(operation_attempt_allowance=128,
                        operation_estimated_airtime_allowance_us=8_000_000))

    def test_default_and_explicit_selection_match_exact_public_sdk_bytes(self):
        for depth in (4, 8):
            with self.subTest(depth=depth):
                selected=common.selected_data_burst(dict(comparison_mode='queued',train_depth=depth))
                self.assertEqual(common.canonical(selected),common.canonical(policy_for_depth(depth)))
        self.assertEqual(common.selected_data_burst(dict(comparison_mode='queued')),
                         common.DATA_BURST_POLICY)
        self.assertIsNone(common.selected_data_burst(dict(comparison_mode='baseline')))

    def test_invalid_selection_never_creates_implicit_eight_authority(self):
        for depth in (True, False, 4.0, 8.0, '8', None, 0, 5, 16):
            with self.subTest(depth=depth),self.assertRaisesRegex(ValueError,'invalid_train_depth'):
                common.selected_data_burst(dict(comparison_mode='queued',train_depth=depth))
        with self.assertRaisesRegex(ValueError,'baseline_train_depth_changed'):
            common.selected_data_burst(dict(comparison_mode='baseline',train_depth=8))

    def test_scope_and_each_original_grant_bind_identical_selected_policy(self):
        for depth in (4,8):
            prepared=self.prepared(depth)
            scope={key:copy.deepcopy(prepared[key]) for key in ('comparison_mode','data_burst')}
            self.assertEqual(common.validate_mode_binding(prepared,scope),'queued')
            plan=dict(data_burst=copy.deepcopy(prepared['data_burst']),
                      operation_limits=common.operation_limits(prepared))
            common.validate_plan_mode(plan,prepared)
            changed=copy.deepcopy(scope)
            changed['data_burst']=common.data_burst_policy(12-depth)
            with self.assertRaisesRegex(ValueError,'scope_mode_changed'):
                common.validate_mode_binding(prepared,changed)
            changed=copy.deepcopy(scope)
            changed['data_burst']['max_depth']=float(depth)
            with self.assertRaisesRegex(ValueError,'scope_mode_changed'):
                common.validate_mode_binding(prepared,changed)
            plan['data_burst']=common.data_burst_policy(12-depth)
            with self.assertRaisesRegex(ValueError,'grant_mode_changed'):
                common.validate_plan_mode(plan,prepared)

    def test_partial_changed_extra_and_numeric_type_policies_refuse_before_use(self):
        for depth in (4,8):
            mutations=[lambda p:p.update(extra=True),lambda p:p.pop('schema'),
                       lambda p:p.update(max_hold_us=2_000_001),
                       lambda p:p.update(inter_packet_rx_us=0),
                       lambda p:p.update(reverse_listen_us=5_000),
                       lambda p:p.update(max_depth=float(depth))]
            if depth==8:
                mutations.extend((lambda p:p.pop('train_frames'),lambda p:p.update(train_frames=4)))
            for mutation in mutations:
                prepared=self.prepared(depth)
                mutation(prepared['data_burst'])
                with self.subTest(depth=depth,policy=prepared['data_burst']),self.assertRaisesRegex(
                        ValueError,'mode_policy_changed'):
                    common.validate_mode_binding(prepared)


if __name__=='__main__':
    unittest.main()
