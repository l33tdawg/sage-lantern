"""Explicit finite train admission; source-only, no radio or SSH."""
import copy
import unittest
from unittest.mock import patch

import common
import controller
from meshticulum.bench import burst_protocol
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

    def test_explicit_logical_window_keeps_native_train_and_guard_bytes(self):
        selected=common.selected_data_burst(dict(comparison_mode='queued',train_depth=8,
                                                flow_window_frames=32))
        expected=common.data_burst_policy(8)
        expected.update(schema='meshticulum.data-burst-policy.v3',flow_window_frames=32)
        self.assertEqual(selected,expected)
        self.assertEqual(selected['max_depth'],8)
        self.assertEqual(selected['train_frames'],8)
        self.assertEqual(selected['inter_packet_rx_us'],5000)
        self.assertEqual(selected['reverse_listen_us'],100000)
        self.assertEqual(selected['max_hold_us'],2000000)

    def test_logical_window_requires_explicit_exact_config(self):
        invalid=[None,True,False,32.0,'32',0,4,8,16,31,33,64]
        for value in invalid:
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'invalid_flow_window_frames'):
                common.selected_data_burst(dict(comparison_mode='queued',train_depth=8,
                                                flow_window_frames=value))
            with self.subTest(helper=value),self.assertRaisesRegex(ValueError,'invalid_flow_window_frames'):
                common.data_burst_policy(8,flow_window_frames=value)
        for config in (dict(comparison_mode='queued'),dict(comparison_mode='queued',train_depth=4),
                       dict(comparison_mode='baseline'),dict(comparison_mode='baseline',train_depth=8)):
            with self.subTest(config=config),self.assertRaises(ValueError):
                common.selected_data_burst(dict(config,flow_window_frames=32))
        with self.assertRaisesRegex(ValueError,'requires_train_eight'):
            common.data_burst_policy(4,flow_window_frames=32)

    def test_selected_policy_requires_exact_sdk_support_without_breaking_old_defaults(self):
        for depth in (4,8):
            config=dict(comparison_mode='queued',train_depth=depth)
            self.assertEqual(controller.checked_data_burst(config),policy_for_depth(depth))
        self.assertIsNone(controller.checked_data_burst(dict(comparison_mode='baseline')))
        config=dict(comparison_mode='queued',train_depth=8,flow_window_frames=32)
        selector=getattr(burst_protocol,'policy_for_flow_window',None)
        if selector is None:
            with self.assertRaisesRegex(ValueError,'sdk_flow_window_policy_unavailable'):
                controller.checked_data_burst(config)
        else:
            self.assertEqual(controller.checked_data_burst(config),selector(32))
        # Model an installed legacy SDK even while running the new SDK suite.
        with patch.object(burst_protocol,'policy_for_flow_window',None,create=True):
            self.assertEqual(controller.checked_data_burst(dict(comparison_mode='queued',train_depth=8)),
                             policy_for_depth(8))
            with self.assertRaisesRegex(ValueError,'sdk_flow_window_policy_unavailable'):
                controller.checked_data_burst(config)
        with patch.object(burst_protocol,'policy_for_flow_window',return_value=policy_for_depth(8),create=True):
            with self.assertRaisesRegex(ValueError,'sdk_train_policy_mismatch'):
                controller.checked_data_burst(config)

    def test_window32_scope_and_each_grant_bind_exact_policy_without_cap_changes(self):
        prepared=self.prepared(8)
        operation=common.operation_limits(prepared)
        prepared['data_burst']=common.data_burst_policy(8,flow_window_frames=32)
        scope={key:copy.deepcopy(prepared[key]) for key in ('comparison_mode','data_burst')}
        plan=dict(data_burst=copy.deepcopy(prepared['data_burst']),operation_limits=operation)
        self.assertEqual(common.validate_mode_binding(prepared,scope),'queued')
        common.validate_plan_mode(plan,prepared)
        self.assertEqual(common.operation_limits(prepared),operation)
        for legacy in (common.data_burst_policy(4),common.data_burst_policy(8)):
            changed=copy.deepcopy(scope);changed['data_burst']=legacy
            with self.assertRaisesRegex(ValueError,'scope_mode_changed'):
                common.validate_mode_binding(prepared,changed)
            changed=copy.deepcopy(plan);changed['data_burst']=legacy
            with self.assertRaisesRegex(ValueError,'grant_mode_changed'):
                common.validate_plan_mode(changed,prepared)
            old=dict(prepared,data_burst=legacy)
            with self.assertRaisesRegex(ValueError,'scope_mode_changed'):
                common.validate_mode_binding(old,scope)
            with self.assertRaisesRegex(ValueError,'grant_mode_changed'):
                common.validate_plan_mode(plan,old)
        changed=copy.deepcopy(plan);changed['operation_limits']['sender']['attempts']+=1
        with self.assertRaisesRegex(ValueError,'operation_limit_changed'):
            common.validate_plan_mode(changed,prepared)

    def test_partial_changed_or_coerced_window32_policy_is_refused(self):
        for mutate in (lambda p:p.pop('flow_window_frames'),lambda p:p.update(flow_window_frames=32.0),
                       lambda p:p.update(flow_window_frames=True),lambda p:p.update(flow_window_frames=31),
                       lambda p:p.update(train_frames=32),lambda p:p.update(max_depth=32),
                       lambda p:p.update(schema='meshticulum.data-burst-policy.v2'),
                       lambda p:p.update(extra=True),lambda p:p.update(reverse_listen_us=0)):
            prepared=self.prepared(8)
            prepared['data_burst']=common.data_burst_policy(8,flow_window_frames=32)
            mutate(prepared['data_burst'])
            with self.subTest(policy=prepared['data_burst']),self.assertRaisesRegex(ValueError,'mode_policy_changed'):
                common.validate_mode_binding(prepared)


if __name__=='__main__':
    unittest.main()
