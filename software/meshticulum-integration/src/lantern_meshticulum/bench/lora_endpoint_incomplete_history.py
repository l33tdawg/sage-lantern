"""Exact expired ea4d endpoint failure; whole paid history, no permission/refund."""
from dataclasses import asdict
import hashlib
import json
import os
import re
from meshticulum.bench.wire import require
from .pilot_parent_caps import Cost,canonical
from . import capture_storage,lora_enrollment_incomplete_history as previous

CONTROLLER_DATABASE_SHA256 = 'acddeee82e3d578bb0390229c328bb7700d33ed85429bc0ae629eaab40b40e62'
FINAL_RESULT_SHA256 = 'a396116e0b967842f7f477b0582d3f196cc6f245e3aade3c832df0d3564921d9'
NATIVE_COUNTS = {'unit-a': {'claim_count': 1, 'prepared_count': 1, 'resource_count': 1, 'start_count': 1},
 'unit-b': {'claim_count': 0, 'prepared_count': 0, 'resource_count': 0, 'start_count': 0},
 'unit-c': {'claim_count': 1, 'prepared_count': 1, 'resource_count': 1, 'start_count': 0}}
NEXT_HISTORY_RECEIPT_SHA256 = '4a17d04dd7c3cc2f61ac62aa027909203ee3a14ef2c55abf4851aafff908b08e'
ORIGINAL_ABSOLUTE_END = 1792088947323303
ORIGINAL_HISTORY_RECEIPT_SHA256 = 'dcef33079caa071f9ff50dc044f2d42e41513133722ad4d80096de3fa09bdf0c'
ORIGINAL_MANIFEST_SHA256 = '61089fa0256c78b2ca31bcc2a8560f6df4665ea896ecf6c5022c9f2937602732'
ORIGINAL_MCU_ARTIFACT_SHA256 = '4bb48e8de4fe1b70eccb6f644250018e98646a0e2642f28949166abd1b42fcb7'
ORIGINAL_OPERATION_SHA256 = 'b1186a6002a5b8d3cca38844c349627ec4126f9e5b047e78573a18ac533b7b32'
ORIGINAL_OUTER_MONOTONIC_END = 1177041163332
ORIGINAL_PHASE_END = 1792088847195360
ORIGINAL_PHASE_START = 1792088247195360
ORIGINAL_RUNTIME_SHA256 = '5520f295e758cbcf87b59488b058238551a430fb9b9bcd89ce79b4ee315ac0ed'
ORIGINAL_SCOPE_SHA256 = '9bd679c58df85a45d883460ed5d88baed68ef911807e48606ee86224f313e5d9'
ORIGINAL_WINDOW_EXPIRY_SHA256 = '12eba494c6eb54a961b26e317be9a449a65754e5ccc7c0434d5f2f89fe036e21'
OWNER_SHA256 = {'unit-a': 'dcbb76c28cd7ebe5a4b06030d0725795b176a04808031f2734ee5f3faabb350a',
 'unit-b': '3fa7ff82f86f46d82dbe328e9af0f212da3568f365ec4f7ba6f09f43c758e393',
 'unit-c': '1de01a4c0b84f36d36aafef6a10c113695f6fd0fed9e9fecb4c5d92b46b817c6'}
PARENT = 'ea4d3adb8508e75f5a0e2e822ecc1cd0'
POLICY_DIGEST = '75a7ff373bbe95eb53d11e1d9c26331a240a61994d4426f1d6b289a5b87c80e0'
POLICY_SHA256 = 'd9ad14a3acd19fcf2d075be5cb3e12f34bf6b4ec6737f058e85905f7e1ad80ea'
PRIOR_SHA256 = {'unit-a': 'a43784cc993776c66d4367a3b8f1408130eb64a0723a74bab5ac7da1ae008f16',
 'unit-b': 'ab024de16e2bdd6db6c47589b3acf5c1117b3e45f0c1639daa9ea9a3f58f2a0f',
 'unit-c': '477de80f7a357d2a5b254abb31228d9b9c84236904ba4961eea7bd6713182c9e'}
PUBLIC_FILES = {'unit-a': {'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/capture.json': {'bytes': 1895108,
                                                                      'sha256': '0858985c2ab70e237cbec8d130af32b02bf62f644f0cf81432fe891450ba5ac6'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/history-binding.json': {'bytes': 417,
                                                                              'sha256': '8271bf644109883c3bcf4eb1d4718f932f33d5c309cf0444a3f397b246218368'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/history-identity-seal.json': {'bytes': 483057,
                                                                                    'sha256': '1ae0e3c6ae756b2474579a8bc179232b7775e1322b322a487f9daadea65c1567'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/legacy-grant-seal.json': {'bytes': 67656,
                                                                                'sha256': '0dfaff049fb4321872fc68c2b29460673805d72f728c8e3e8b3133e9a3c2082e'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/native.sqlite': {'bytes': 49152,
                                                                       'sha256': 'e1e6493c6f23ac11f6337728e485586995893005ba4aa1c92084bb91fe464969'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/native.sqlite.approval': {'bytes': 65,
                                                                                'sha256': '274463bba42942b66ba08810c9b7e568b66c1b9719c1738088c04b921d65f74b'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/native.sqlite.owner': {'bytes': 0,
                                                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/owner.json': {'bytes': 10522,
                                                                    'sha256': 'dcbb76c28cd7ebe5a4b06030d0725795b176a04808031f2734ee5f3faabb350a'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/policy.json': {'bytes': 2293,
                                                                     'sha256': 'd9ad14a3acd19fcf2d075be5cb3e12f34bf6b4ec6737f058e85905f7e1ad80ea'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prepared-identities/487fc155d86af1855fa9ade07ec215bc.json': {'bytes': 466,
                                                                                                                   'sha256': '6f7b63f9bba776ca33069680a32107b55b3a83f9fdb66afe85e9bd2bfc33b931'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prior.json': {'bytes': 23310,
                                                                    'sha256': 'a43784cc993776c66d4367a3b8f1408130eb64a0723a74bab5ac7da1ae008f16'},
            'runs/487fc155d86af1855fa9ade07ec215bc/active': {'bytes': 8,
                                                             'sha256': '8b86d3b824a855c1b156ba78756ca36a80a6f1b1290204878987a662e0951324'},
            'runs/487fc155d86af1855fa9ade07ec215bc/grant.json': {'bytes': 1326,
                                                                 'sha256': 'd61af73b893480231d3a3e8be813421bfc31843fef7a563e778335d02fab15da'},
            'runs/487fc155d86af1855fa9ade07ec215bc/provision.json': {'bytes': 3144,
                                                                     'sha256': 'b99f759c3b3559177d8d0a8b0b5d2c3ecad7dafd12dcb074df6d377519052d7b'},
            'runs/487fc155d86af1855fa9ade07ec215bc/result.json': {'bytes': 7031,
                                                                  'sha256': 'eb5caf990b1754dcd3befd4970c144f2cbb863d2fcd92390022679d1c21b1b13'},
            'runs/487fc155d86af1855fa9ade07ec215bc/started': {'bytes': 65,
                                                              'sha256': '4544f9961aad411312f9ef30c820faa5a0f1accd9a297a9a054cab31c802ace8'}},
 'unit-b': {'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/capture.json': {'bytes': 2242872,
                                                                      'sha256': '04f985217a300484cb4b70bc3a7aeb957d2091f9fb6763ee5ce1912da88e3f2f'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/history-binding.json': {'bytes': 417,
                                                                              'sha256': '0297a67f466774cd20d220961c4cbe234a37ca7f79c9315b1183f2dca567d594'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/history-identity-seal.json': {'bytes': 209961,
                                                                                    'sha256': 'e246e36c038d30f717758750f2a1f26852e08042a8bd8e94f86b05adfef5ad92'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/legacy-grant-seal.json': {'bytes': 102543,
                                                                                'sha256': '95539e6026afa5b68d01686e15c50661b2d3d1cca5079957d3c682e69f05859a'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/native.sqlite': {'bytes': 49152,
                                                                       'sha256': '66317d00d91fefc532990f86e75b81fa3f35b89339ba5563d0117251ae9eccd3'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/native.sqlite.approval': {'bytes': 65,
                                                                                'sha256': '6ac612ac4dc4511e3bc1ab0eace02c4c9c189d5899fdb6b83c7e9ea98bbf275c'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/native.sqlite.owner': {'bytes': 0,
                                                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/owner.json': {'bytes': 10522,
                                                                    'sha256': '3fa7ff82f86f46d82dbe328e9af0f212da3568f365ec4f7ba6f09f43c758e393'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/policy.json': {'bytes': 2293,
                                                                     'sha256': 'd9ad14a3acd19fcf2d075be5cb3e12f34bf6b4ec6737f058e85905f7e1ad80ea'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prior.json': {'bytes': 35149,
                                                                    'sha256': 'ab024de16e2bdd6db6c47589b3acf5c1117b3e45f0c1639daa9ea9a3f58f2a0f'}},
 'unit-c': {'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/capture.json': {'bytes': 2430055,
                                                                      'sha256': '1c9e0f50fbe8f92f7d89912e1f6ef849e863c7a1de9154a0e1686b0555114e2d'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/history-binding.json': {'bytes': 418,
                                                                              'sha256': '15efa81d24c439796b3563f47d16b809e077f194930e95146c9790e4dbb5a6ab'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/history-identity-seal.json': {'bytes': 530208,
                                                                                    'sha256': '13b2f145006a7d5dc5061f3fda3c0b498c3d33f2fa4613019299e8565b7f6d1b'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/legacy-grant-seal.json': {'bytes': 104449,
                                                                                'sha256': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/native.sqlite': {'bytes': 49152,
                                                                       'sha256': '79f2db2159dac259dfaccbe715c470dc02c150b8fc3e62856fb1284f58690281'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/native.sqlite.approval': {'bytes': 65,
                                                                                'sha256': '90c47ee039f18fbb566f54e527ff1d1c39566897678c88400d3087fc4b2aaf93'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/native.sqlite.owner': {'bytes': 0,
                                                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/owner.json': {'bytes': 10522,
                                                                    'sha256': '1de01a4c0b84f36d36aafef6a10c113695f6fd0fed9e9fecb4c5d92b46b817c6'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/policy.json': {'bytes': 2293,
                                                                     'sha256': 'd9ad14a3acd19fcf2d075be5cb3e12f34bf6b4ec6737f058e85905f7e1ad80ea'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prepared-identities/487fc155d86af1855fa9ade07ec215bc.json': {'bytes': 467,
                                                                                                                   'sha256': '640b6ae74e7881d076d4e5e42d8d91a5f16f4256fd5aeebb8309c7223c317a9b'},
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prior.json': {'bytes': 34626,
                                                                    'sha256': '477de80f7a357d2a5b254abb31228d9b9c84236904ba4961eea7bd6713182c9e'},
            'runs/487fc155d86af1855fa9ade07ec215bc/active': {'bytes': 8,
                                                             'sha256': '8b86d3b824a855c1b156ba78756ca36a80a6f1b1290204878987a662e0951324'},
            'runs/487fc155d86af1855fa9ade07ec215bc/grant.json': {'bytes': 1325,
                                                                 'sha256': '903235ce8279439f6a16edbec3ffa1b8f99ef55a2535729892818b8ea213e7e7'},
            'runs/487fc155d86af1855fa9ade07ec215bc/provision.json': {'bytes': 3133,
                                                                     'sha256': '7966c1a579a92fca1b8792434ef3095b49d4b426f2cdf1545b3c76aa01a32d4a'}}}
RAW_INTAKE_COMPLETE_SHA256 = 'e818e1c6fd3657c5435939800dcbc72ad3e83d75687d3498bcc60cd4d27036b4'
RAW_UNIT_SHA256 = {'unit-a': 'f410eabb6d89cb7ef81f4ec11fd25f5f7ecb3403addb7a414fb0e068172ff30f',
 'unit-b': '583cfc013c347d2762347d1904afaa92007936f8be7c1287564543ea4e27f1fa',
 'unit-c': '42fdb14f9e65659f919f5481a68d1109d373e56f563019f1951420691bf3cf35'}
RUN_ID = '487fc155d86af1855fa9ade07ec215bc'
CONTROLLER_ALLOCATION_ID = '6f5b9177e0b25d39f4daae0348b1cdec4f9467d716c17316363c4c8924616493'
PREPARED_CLAIMS = {'unit-a': {'application_deadline_monotonic_us': 548036438932,
            'application_expires_epoch_us': 1792088683731305,
            'child_caps': {'cost': {'estimated_airtime_us': 60000000,
                                    'ip_frames': 0,
                                    'radio_attempts': 256,
                                    'rx_stream_bytes': 0,
                                    'tx_stream_bytes': 0},
                           'digest': '7e183800daa1b96698d28ca944f0d2968911647b4a684b1321b22f2c7c23e50b',
                           'metadata': {'application_expires_epoch_us': 1792088683731305,
                                        'campaign_id': '3be215ef5c2e613c2809ebecf9676782',
                                        'cipher_frame_limit': 512,
                                        'format': 'MR1.request',
                                        'grant_digest': '88bf2875917d2565ac429cdb003ca1f82cdf1869840c418f206cf617c5289266',
                                        'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                                        'object': {'bytes': 74,
                                                   'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                                        'parent_id': 'ea4d3adb8508e75f5a0e2e822ecc1cd0',
                                        'plan_sha256': '26c68e146eb6682a13d01554f9c087c8a37ff715ef8e1a25b863c5a13f91abd0',
                                        'policy_digest': '75a7ff373bbe95eb53d11e1d9c26331a240a61994d4426f1d6b289a5b87c80e0',
                                        'profile': {'bandwidth_hz': 500000,
                                                    'chip_power_dbm': 0,
                                                    'coding_rate': 5,
                                                    'frequency_hz': 921375000,
                                                    'lease_ms': 90000,
                                                    'max_boot_attempts': 256,
                                                    'max_boot_estimated_airtime_us': 60000000,
                                                    'preamble': 8,
                                                    'spreading_factor': 7,
                                                    'sync_word': 66},
                                        'receiver': 'unit-c',
                                        'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                                        'run_id': '487fc155d86af1855fa9ade07ec215bc',
                                        'secret_sha256': '592cdb5e6ba5cb671dc94742abe1b8f1cd330f45b9cbabb0769fdd7daf99e3fc',
                                        'sender': 'unit-a',
                                        'sends': True,
                                        'source_run_id': None,
                                        'transport': 'lora',
                                        'unit': 'unit-a'}},
            'controller_allocation_id': '6f5b9177e0b25d39f4daae0348b1cdec4f9467d716c17316363c4c8924616493',
            'grant_context_sha256': '88bf2875917d2565ac429cdb003ca1f82cdf1869840c418f206cf617c5289266',
            'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
            'native_claim_id': '808b3ead431230fa680421a9a19e055eb246e0cf2b6afe7b67246e23840d7e45',
            'native_plan_sha256': '86fcad2c24516f894c7b4ea580029dcc269d8ae7b50adbd1b4ec07c241fba5ec',
            'original_plan_sha256': '26c68e146eb6682a13d01554f9c087c8a37ff715ef8e1a25b863c5a13f91abd0',
            'parent_id': 'ea4d3adb8508e75f5a0e2e822ecc1cd0',
            'policy_digest': '75a7ff373bbe95eb53d11e1d9c26331a240a61994d4426f1d6b289a5b87c80e0',
            'prepared': True,
            'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
            'run_id': '487fc155d86af1855fa9ade07ec215bc',
            'schema': 'meshticulum.native-parent-claim.v1',
            'secret_sha256': '592cdb5e6ba5cb671dc94742abe1b8f1cd330f45b9cbabb0769fdd7daf99e3fc',
            'unit': 'unit-a'},
 'unit-c': {'application_deadline_monotonic_us': 44379200610,
            'application_expires_epoch_us': 1792088683731305,
            'child_caps': {'cost': {'estimated_airtime_us': 60000000,
                                    'ip_frames': 0,
                                    'radio_attempts': 256,
                                    'rx_stream_bytes': 0,
                                    'tx_stream_bytes': 0},
                           'digest': '260e414d32b4e17c863655c67a3a77f8ab0dbcf08b6a8e03313788d4b9934cf6',
                           'metadata': {'application_expires_epoch_us': 1792088683731305,
                                        'campaign_id': '3be215ef5c2e613c2809ebecf9676782',
                                        'cipher_frame_limit': 512,
                                        'format': 'MR1.request',
                                        'grant_digest': '88bf2875917d2565ac429cdb003ca1f82cdf1869840c418f206cf617c5289266',
                                        'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                                        'object': {'bytes': 74,
                                                   'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                                        'parent_id': 'ea4d3adb8508e75f5a0e2e822ecc1cd0',
                                        'plan_sha256': '1cf12d488fec74e6c6a05b27dcce1257f27ba5478dc08aa59c0e422e785467fc',
                                        'policy_digest': '75a7ff373bbe95eb53d11e1d9c26331a240a61994d4426f1d6b289a5b87c80e0',
                                        'profile': {'bandwidth_hz': 500000,
                                                    'chip_power_dbm': 0,
                                                    'coding_rate': 5,
                                                    'frequency_hz': 921375000,
                                                    'lease_ms': 90000,
                                                    'max_boot_attempts': 256,
                                                    'max_boot_estimated_airtime_us': 60000000,
                                                    'preamble': 8,
                                                    'spreading_factor': 7,
                                                    'sync_word': 66},
                                        'receiver': 'unit-c',
                                        'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                                        'run_id': '487fc155d86af1855fa9ade07ec215bc',
                                        'secret_sha256': '592cdb5e6ba5cb671dc94742abe1b8f1cd330f45b9cbabb0769fdd7daf99e3fc',
                                        'sender': 'unit-a',
                                        'sends': False,
                                        'source_run_id': None,
                                        'transport': 'lora',
                                        'unit': 'unit-c'}},
            'controller_allocation_id': '6f5b9177e0b25d39f4daae0348b1cdec4f9467d716c17316363c4c8924616493',
            'grant_context_sha256': '88bf2875917d2565ac429cdb003ca1f82cdf1869840c418f206cf617c5289266',
            'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
            'native_claim_id': '8d401f270c411ed7809bc732ecf1f907fc3d9e8202ba5d3e9d5277275dbd46a6',
            'native_plan_sha256': '34476fdc3b2851509b43dafaacbc3e8ec34c9419e8876373bbd3e604842c6c63',
            'original_plan_sha256': '1cf12d488fec74e6c6a05b27dcce1257f27ba5478dc08aa59c0e422e785467fc',
            'parent_id': 'ea4d3adb8508e75f5a0e2e822ecc1cd0',
            'policy_digest': '75a7ff373bbe95eb53d11e1d9c26331a240a61994d4426f1d6b289a5b87c80e0',
            'prepared': True,
            'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
            'run_id': '487fc155d86af1855fa9ade07ec215bc',
            'schema': 'meshticulum.native-parent-claim.v1',
            'secret_sha256': '592cdb5e6ba5cb671dc94742abe1b8f1cd330f45b9cbabb0769fdd7daf99e3fc',
            'unit': 'unit-c'}}
NATIVE_PROJECTION_SHA256 = {'unit-a': '04d03c103fcbc055f5367768456d9c773da9af51a3000191191170452e869ef5',
 'unit-b': '78b9003f07ccd40a69acb5c33a7a0accc84dbecc349498e4d2f172d6d20d59b6',
 'unit-c': '6b9eec599610fb26c66672846b62aaad45148ad19bbdd83e5d3b89bb3bcd615b'}
DIRECTORY_NAMES = {'unit-a': {'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0': ['capture.json',
                                                         'history-binding.json',
                                                         'history-identity-seal.json',
                                                         'legacy-grant-seal.json',
                                                         'native.sqlite',
                                                         'native.sqlite.approval',
                                                         'native.sqlite.owner',
                                                         'owner.json',
                                                         'policy.json',
                                                         'prepared-identities',
                                                         'prior.json'],
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prepared-identities': ['487fc155d86af1855fa9ade07ec215bc.json'],
            'runs/487fc155d86af1855fa9ade07ec215bc': ['active',
                                                      'grant.json',
                                                      'provision.json',
                                                      'result.json',
                                                      'secret.bin',
                                                      'sender.bin',
                                                      'started']},
 'unit-b': {'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0': ['capture.json',
                                                         'history-binding.json',
                                                         'history-identity-seal.json',
                                                         'legacy-grant-seal.json',
                                                         'native.sqlite',
                                                         'native.sqlite.approval',
                                                         'native.sqlite.owner',
                                                         'owner.json',
                                                         'policy.json',
                                                         'prepared-identities',
                                                         'prior.json'],
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prepared-identities': []},
 'unit-c': {'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0': ['capture.json',
                                                         'history-binding.json',
                                                         'history-identity-seal.json',
                                                         'legacy-grant-seal.json',
                                                         'native.sqlite',
                                                         'native.sqlite.approval',
                                                         'native.sqlite.owner',
                                                         'owner.json',
                                                         'policy.json',
                                                         'prepared-identities',
                                                         'prior.json'],
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prepared-identities': ['487fc155d86af1855fa9ade07ec215bc.json'],
            'runs/487fc155d86af1855fa9ade07ec215bc': ['active',
                                                      'grant.json',
                                                      'provision.json',
                                                      'secret.bin']}}
DIRECTORY_IDENTITIES = {'unit-a': {'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0': [66307, 6415071, 16832, 0],
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prepared-identities': [66307, 6415072, 16832, 0],
            'runs/487fc155d86af1855fa9ade07ec215bc': [66307, 6415088, 16832, 0]},
 'unit-b': {'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0': [66307, 3779139, 16832, 0],
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prepared-identities': [66307, 3779140, 16832, 0]},
 'unit-c': {'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0': [66305, 11028905, 16832, 0],
            'parents/ea4d3adb8508e75f5a0e2e822ecc1cd0/prepared-identities': [66305, 11030293, 16832, 0],
            'runs/487fc155d86af1855fa9ade07ec215bc': [66305, 11030310, 16832, 0]}}
OPAQUE_LEAF_STATS = {'unit-a': {'runs/487fc155d86af1855fa9ade07ec215bc/secret.bin': [66307,
                                                                 6415090,
                                                                 33152,
                                                                 0,
                                                                 0,
                                                                 1,
                                                                 32,
                                                                 1792088512380937713,
                                                                 1792088512380937713],
            'runs/487fc155d86af1855fa9ade07ec215bc/sender.bin': [66307,
                                                                 6415092,
                                                                 33152,
                                                                 0,
                                                                 0,
                                                                 1,
                                                                 74,
                                                                 1792088512416937505,
                                                                 1792088512416937505]},
 'unit-b': {},
 'unit-c': {'runs/487fc155d86af1855fa9ade07ec215bc/secret.bin': [66305,
                                                                 11030312,
                                                                 33152,
                                                                 0,
                                                                 0,
                                                                 1,
                                                                 32,
                                                                 1792088513711521241,
                                                                 1792088513711521241]}}
H47_HISTORY_SHA256 = {'unit-a': '61f3f243d7681a7af120fd8c11f1558caefe87e36d7d12ab57263d83f84af5f5',
 'unit-b': '0ff982eb77f3ffe7b0d3ab586cf3d2e93cea049677d898c7bb37c57dbff11396',
 'unit-c': 'a20a0817b08f8d68a9ba7a897e58d457937feb0562af48e4e56b1f37e8c8300e'}
PROJECTION = ('status', 'scoped_rows_complete', 'pending_unknown', 'policy_row_sha256', 'prior_lineage', 'detail', 'files')
H47_PREFIX = previous.H46_PREFIX+(previous.PARENT,)
OWNER_SCHEMA = previous.OWNER_SCHEMA
KIND = previous.KIND
STAT_FIELDS = ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def declared_partition(policy):
    require(policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and digest(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==KIND,
        'ea4d_original_policy_changed')
    for local in policy.units:
        require(local.prior_debt.plus(Cost()if local.unit=='unit-b'else Cost(1024,240000000))==local.limits,
            'ea4d_whole_parent_reservation_changed')
    return True


def owner_view(record,policy,*,unit):
    declared_partition(policy)
    require(unit in OWNER_SHA256 and type(record)is dict and digest(record)==OWNER_SHA256[unit],
        'ea4d_raw_experiment_owner_changed')
    require(record['schema']==OWNER_SCHEMA and record['policy_digest']==POLICY_DIGEST
        and record['qualification_kind']==KIND and record['comparison_scope_sha256']==ORIGINAL_SCOPE_SHA256
        and record['history_receipt_sha256']==ORIGINAL_HISTORY_RECEIPT_SHA256
        and digest(record['lora_mcu_artifact'])==ORIGINAL_MCU_ARTIFACT_SHA256
        and record['runtime_sha256']==ORIGINAL_RUNTIME_SHA256 and record['manifest_sha256']==ORIGINAL_MANIFEST_SHA256
        and record['actual_cost']is None and record['complete_global_debt']is False
        and all(record[k]is False for k in ('alpha_qualified','full_source_green','private_production_enabled',
            'physical_qualification','rf_qualified')),'ea4d_historical_experiment_binding_changed')
    return record


def endpoint_rows(unit):
    """Exact materialized child names; no claim of successful legacy runs."""
    require(unit in OWNER_SHA256,'ea4d_unit_changed')
    prefix='runs/'+RUN_ID+'/'
    return ([]if unit=='unit-b'else[dict(run_id=RUN_ID,
        grant_sha256=PUBLIC_FILES[unit][prefix+'grant.json']['sha256'])])


def row_for(unit,policy,owner,prior):
    owner_view(owner,policy,unit=unit);local=policy.unit(unit)
    require(type(prior)is dict and digest(prior)==PRIOR_SHA256[unit]
        and prior['previous_parent_ids']==list(H47_PREFIX) and prior['full_caps']==asdict(local.prior_debt)
        and prior['actual_cost']is None and prior['complete_global_debt']is False and prior['refunds']==0,
        'ea4d_original_prior_changed')
    allocated=Cost()if unit=='unit-b'else Cost(256,60000000)
    unused=Cost()if unit=='unit-b'else Cost(768,180000000)
    return dict(policy=policy.to_dict(),policy_digest=POLICY_DIGEST,owner=owner,
        prior_kind='parent',prior_metadata=prior,
        database_sha256=PUBLIC_FILES[unit]['parents/'+PARENT+'/native.sqlite']['sha256'],
        projection_keys=list(PROJECTION),projection_sha256=NATIVE_PROJECTION_SHA256[unit],
        controller_endpoint_refs=[],runs=[],retired_unstarted=[],preparation_sha256={},legacy_sha256=None,
        paid_caps=asdict(local.limits),actual_native_paid_caps=asdict(local.prior_debt.plus(allocated)),
        actual_controller_paid_caps=asdict(local.prior_debt.plus(allocated)),
        conservative_unused_enrollment_caps=asdict(unused),conservative_unused_controller_caps=asdict(unused),
        native_controller_debt_views_may_be_summed=False,require_result_complete=False,
        failed_started_runs=None,original_cleanup_proved=False,
        public_metadata_sha256={k:v['sha256']for k,v in PUBLIC_FILES[unit].items()},
        endpoint_incomplete=dict(schema='meshticulum.retained-public-comparison-endpoint-incomplete.v1',
            unit=unit,known_run_scopes=[r['run_id']for r in endpoint_rows(unit)],
            actual_claim_resource_prepared_start_counts=NATIVE_COUNTS[unit],
            prepared_identity_count=len(DIRECTORY_NAMES[unit]['parents/'+PARENT+'/prepared-identities']),
            actual_cost=None,no_refund=True,physical_quiescence_observed=False,callback_terminal_proved=False,
            raw_intake_complete_sha256=RAW_INTAKE_COMPLETE_SHA256,raw_unit_sha256=RAW_UNIT_SHA256[unit],
            original_result_sha256=FINAL_RESULT_SHA256,original_operation_sha256=ORIGINAL_OPERATION_SHA256,
            original_window_expiry_sha256=ORIGINAL_WINDOW_EXPIRY_SHA256))


def qualify_row(unit,policy,value):
    require(type(value)is dict and canonical(value)==canonical(row_for(unit,policy,value['owner'],value['prior_metadata'])),
        'ea4d_failed_endpoint_or_whole_paid_debt_changed')
    return value['paid_caps']


def require_final_bindings():
    require(type(NEXT_HISTORY_RECEIPT_SHA256)is str and re.fullmatch('[0-9a-f]{64}',NEXT_HISTORY_RECEIPT_SHA256),
        'pending_ea4d_H48_history_receipt')
    return True


def verify_public_bytes(unit,policy,value,raws,previous_history,native):
    """Bind actual public files and real native rows; opaque bytes are excluded."""
    qualify_row(unit,policy,value);owner=owner_view(value['owner'],policy,unit=unit)
    expected=PUBLIC_FILES[unit];prefix='parents/'+PARENT+'/'
    require(set(raws)==set(expected) and all(type(raws[k])is bytes and len(raws[k])==pin['bytes']
        and hashlib.sha256(raws[k]).hexdigest()==pin['sha256']for k,pin in expected.items()),
        'ea4d_actual_public_bytes_changed')
    require(raws[prefix+'policy.json']==canonical(value['policy'])
        and raws[prefix+'owner.json']==canonical(value['owner'])
        and raws[prefix+'prior.json']==canonical(value['prior_metadata']),'ea4d_original_parent_input_changed')
    capture=json.loads(capture_storage.decode(raws[prefix+'capture.json']))
    require(capture['schema']=='meshticulum.private-resume-actual-capture.v1'
        and capture['parent_id']==PARENT and capture['unit']==unit and capture['actual_history_verified']is True
        and capture['global_debt_complete']is False and capture['original_unknown_actual_cost']is None and capture['refunds']==0
        and digest(capture['history'])==H47_HISTORY_SHA256[unit]
        and canonical(capture['history'])==canonical(previous_history)
        and tuple(v['policy']['parent_id']for v in capture['history']['previous'])==H47_PREFIX
        and capture['native_phase']==owner['native_phase'][unit]
        and capture['history_binding_sha256']==owner['history_binding_sha256'],'ea4d_original_capture_or_H47_prefix_changed')
    require(native['role']=='native'and native['unit']==unit and native['parent_id']==PARENT
        and native['policy_digest']==policy.digest and native['status']=='scoped_rows_observed'
        and native['scoped_rows_complete']is native['policy_expired_at_observation']is True
        and native['pending_unknown']is native['database_writes']is False
        and native['expected_database_sha256']==value['database_sha256']
        and digest({k:native[k]for k in PROJECTION})==NATIVE_PROJECTION_SHA256[unit],
        'ea4d_actual_native_rows_changed')
    detail=native['detail'];observed={k:detail[k]for k in NATIVE_COUNTS[unit]}
    require(observed==NATIVE_COUNTS[unit]and all(type(v)is int for v in observed.values())
        and detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']
        and detail['limit']==value['paid_caps'],'ea4d_actual_counts_or_unsummed_debt_changed')
    if unit!='unit-b':
        run_prefix='runs/'+RUN_ID+'/'
        grant=json.loads(raws[run_prefix+'grant.json']);provision=json.loads(raws[run_prefix+'provision.json'])
        receipt=provision['parent'];claim=detail['claims'][0]
        identity=json.loads(raws[prefix+'prepared-identities/'+RUN_ID+'.json'])
        require(provision['schema']=='meshticulum.provisioned-public-pilot.v2'
            and receipt==PREPARED_CLAIMS[unit] and provision['unit']==unit and provision['run_id']==RUN_ID
            and digest(grant)==receipt['native_plan_sha256']==claim['native_plan_sha256']
            and receipt['native_claim_id']==claim['native_claim_id']and receipt['prepared']is claim['prepared']is True
            and receipt['controller_allocation_id']==claim['controller_allocation_id']
            and grant['host_boot_id']==policy.unit(unit).host_boot_id and grant['unit']==unit
            and grant['session_id']==RUN_ID and grant['service']['format']=='MR1.request'
            and grant['service']['application_expires_epoch_us']==receipt['application_expires_epoch_us']
            and raws[run_prefix+'active']==b'retired\n','ea4d_prepared_native_plan_or_retirement_changed')
        require(identity['schema']=='meshticulum.private-resume-prepared-identity.v2'
            and identity['parent_id']==PARENT and identity['policy_digest']==policy.digest and identity['unit']==unit
            and identity['run_id']==RUN_ID and identity['directory_identity']==DIRECTORY_IDENTITIES[unit]['runs/'+RUN_ID]
            and identity['prepared_sha256']==digest(receipt)==claim['prepared_row_sha256']
            and identity['provision_sha256']==hashlib.sha256(raws[run_prefix+'provision.json']).hexdigest(),
            'ea4d_actual_prepared_identity_changed')
        if unit=='unit-a':
            outcome=json.loads(raws[run_prefix+'result.json'])
            require(claim['started']is True and outcome['schema']=='meshticulum.physical-service-run.v1'
                and outcome['unit']==unit and outcome['host_boot_id']==policy.unit(unit).host_boot_id
                and outcome['complete']is False and outcome['error']=='WireError:sender_receive_ready_requires_public32_sender'
                and outcome['parent']['native_claim_id']==receipt['native_claim_id']and outcome['parent']['started']is True,
                'ea4d_actual_prejob_start_failure_changed')
        else:require(claim['started']is False,'ea4d_actual_unstarted_receiver_changed')
    return True


def _names(reader,path):
    reader.directory(path)
    fd=os.open('.',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=reader._nodes[path][0])
    try:return sorted(os.listdir(fd))
    finally:os.close(fd);reader.directory(path)


def metadata_files(reader,state,unit,value,*,guard,clock,current_boot,previous_history):
    """Fresh exact metadata, complete native snapshot and stat-only opaque leaves."""
    from .pilot_parent_caps import ParentPolicy,boot
    from .pilot_parent_snapshot import Scope,collect
    policy=ParentPolicy.from_dict(value['policy']);qualify_row(unit,policy,value)
    owner=owner_view(value['owner'],policy,unit=unit)
    def current():
        guard();sample=clock();now_boot=current_boot();boot(now_boot)
        end=owner['native_phase'][unit]['deadline_monotonic_us']+ORIGINAL_ABSOLUTE_END-ORIGINAL_PHASE_END
        require(type(sample)is tuple and len(sample)==2 and all(type(v)is int and v>0 for v in sample)
            and sample[0]>ORIGINAL_ABSOLUTE_END
            and (now_boot!=policy.unit(unit).host_boot_id or sample[1]>end),'ea4d_original_clocks_not_expired')
        return sample
    def names():
        for relative,expected in DIRECTORY_NAMES[unit].items():
            current();path=state/relative
            require(_names(reader,path)==expected and reader.directories[path]==DIRECTORY_IDENTITIES[unit][relative],
                'ea4d_actual_directory_or_identity_changed')
        for relative,expected in OPAQUE_LEAF_STATS[unit].items():
            path=state/relative;reader.directory(path.parent)
            info=os.stat(path.name,dir_fd=reader._nodes[path.parent][0],follow_symlinks=False)
            actual=[getattr(info,k)for k in STAT_FIELDS]
            require(actual==expected,'ea4d_opaque_leaf_stat_changed')
    current();names();raws={}
    for relative,pin in PUBLIC_FILES[unit].items():
        current();raws[relative]=reader.read_held(state/relative,pin['bytes'])
    sampled=current();guarded=guard()
    scope=Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,PARENT,POLICY_DIGEST,
        value['database_sha256'],unit,current_boot(),min(sampled[0]+35000000,guarded['phase_end_epoch_us']))
    native=collect(state,scope,clock=current,boot_reader=current_boot,legacy_scope=None).to_dict()
    verify_public_bytes(unit,policy,value,raws,previous_history,native)
    # Establish the retained shared historical lock only after collect has
    # independently closed its exclusive observation owner handle.
    reader.read_held(state/'parents'/PARENT/'native.sqlite.owner',0,lock=True)
    reader.check();names();current()
    return {k:hashlib.sha256(v).hexdigest()for k,v in raws.items()}


def controller_caps(unit,value,observation):
    from .pilot_parent_caps import ParentPolicy
    policy=ParentPolicy.from_dict(value['policy']);qualify_row(unit,policy,value)
    detail=observation['detail'];sample=(observation['observation_original_epoch_us'],observation['observation_original_monotonic_us'])
    require(observation['role']=='controller'and observation['parent_id']==PARENT
        and observation['policy_digest']==POLICY_DIGEST and observation['expected_database_sha256']==CONTROLLER_DATABASE_SHA256
        and observation['status']=='scoped_rows_observed'and observation['scoped_rows_complete']is True
        and observation['policy_expired_at_observation']is True and observation['pending_unknown']is False
        and observation['database_writes']is False and type(detail['allocation_count'])is int and detail['allocation_count']==1
        and len(detail['allocations'])==1 and detail['allocations'][0]['allocation_id']==CONTROLLER_ALLOCATION_ID
        and all(type(v)is int and v>0 for v in sample)and sample[0]>ORIGINAL_ABSOLUTE_END
        and (observation['current_host_boot_id']!=policy.controller_boot_id or sample[1]>ORIGINAL_OUTER_MONOTONIC_END),
        'ea4d_controller_allocation_or_expiry_changed')
    require(len(detail['units'])==3 and {v['unit']for v in detail['units']}==set(OWNER_SHA256)
        and all(canonical(v['limit'])==canonical(asdict(policy.unit(v['unit']).limits))
            and canonical(v['prior_plus_prepaid_caps'])==canonical(asdict(policy.unit(v['unit']).prior_debt.plus(Cost()if v['unit']=='unit-b'else Cost(256,60000000))))
            and canonical(v['full_prepaid_caps'])==canonical(asdict(Cost()if v['unit']=='unit-b'else Cost(256,60000000)))
            for v in detail['units']),'ea4d_controller_unsummed_whole_reservation_changed')
    return value['paid_caps']
