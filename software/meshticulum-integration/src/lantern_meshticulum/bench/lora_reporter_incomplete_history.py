"""Exact expired 291fcf reporter failure; retains full paid limits and unknown audit."""
from dataclasses import asdict
import hashlib
import json
import os
import re
from meshticulum.bench.wire import require
from lantern_meshticulum.bench.pilot_parent_caps import Cost, canonical
from lantern_meshticulum.bench import capture_storage, lora_management_incomplete_history as previous

CONTROLLER_DATABASE_SHA256 = 'f9c734e79c67fb31dfe25c48fc6728a2cbcf49a7b05c8e08c9dad6ee8d2704c9'
CONTROLLER_ALLOCATIONS = [{'allocation_id': '3055d18c796c3d68541ec4be25f860939b5d06f4ffa5a791bf68c65523cd547e',
  'children': [{'application_expires_epoch_us': 1792218477458185,
                'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                'child_digest': 'f2f96f0cce808f65cb3812f62fd1a54ad133b9ba7101a5abfe5568d2fc2a82e8',
                'cipher_frame_limit': 512,
                'format': 'MR1.response',
                'full_caps': {'estimated_airtime_us': 60000000,
                              'ip_frames': 0,
                              'radio_attempts': 256,
                              'rx_stream_bytes': 0,
                              'tx_stream_bytes': 0},
                'grant_digest': 'e7817f8783c250af5e3745c613d8091382db004bc722d207ed023ab44943431f',
                'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                'object': {'bytes': 8192,
                           'sha256': 'b3d5819d794eae7a6781f2b57be166a348fa4df54fd844733e7c85a1ccf6c740'},
                'plan_sha256': '9e3bd31bf8a32a27185dc2b064eaf1a3a10e54dffe5d8c28d5626cefdc0462b2',
                'receiver': 'unit-a',
                'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                'run_id': '891a03b0fd31296af6956eee7d09dc2f',
                'secret_sha256': '50252327759d545331f1986089416b73236d9809694ffc9f591d783a12195cd6',
                'sender': 'unit-c',
                'sends': False,
                'transport': 'lora',
                'unit': 'unit-a'},
               {'application_expires_epoch_us': 1792218477458185,
                'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                'child_digest': '21d82d5a109e7ae3199c1f6836731250f7537e0f4d7ae46ca768acb01f4a6451',
                'cipher_frame_limit': 512,
                'format': 'MR1.response',
                'full_caps': {'estimated_airtime_us': 60000000,
                              'ip_frames': 0,
                              'radio_attempts': 256,
                              'rx_stream_bytes': 0,
                              'tx_stream_bytes': 0},
                'grant_digest': 'e7817f8783c250af5e3745c613d8091382db004bc722d207ed023ab44943431f',
                'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                'object': {'bytes': 8192,
                           'sha256': 'b3d5819d794eae7a6781f2b57be166a348fa4df54fd844733e7c85a1ccf6c740'},
                'plan_sha256': 'edf009c8126eef8b6722fdfebf92be5a245dd9739372861ee122c0c42e81214e',
                'receiver': 'unit-a',
                'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                'run_id': '891a03b0fd31296af6956eee7d09dc2f',
                'secret_sha256': '50252327759d545331f1986089416b73236d9809694ffc9f591d783a12195cd6',
                'sender': 'unit-c',
                'sends': True,
                'transport': 'lora',
                'unit': 'unit-c'}],
  'row_sha256': 'b815f0f26261f1f3d1696f2ddda00d29bcdc607877e076aea439abca9ee0ea11',
  'run_id': '891a03b0fd31296af6956eee7d09dc2f'},
 {'allocation_id': 'fd5d5a0b9b76a2804e4da04197a9d5ef124f21633788d568708f0f3a31464f9d',
  'children': [{'application_expires_epoch_us': 1792218477458185,
                'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                'child_digest': '262a7efc67e4839206105691aaeb0935c395a60d69c722835032ffd88b97b8b0',
                'cipher_frame_limit': 512,
                'format': 'MR1.request',
                'full_caps': {'estimated_airtime_us': 60000000,
                              'ip_frames': 0,
                              'radio_attempts': 256,
                              'rx_stream_bytes': 0,
                              'tx_stream_bytes': 0},
                'grant_digest': 'e9c6af9ae71f836eceba9ec59635f03969465c972dde772173a35dd8a97abe0e',
                'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                'object': {'bytes': 74,
                           'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                'plan_sha256': '8c03256391a09d98c6c35b520a6cab117fb2a72b65f3737265926342adfc0f49',
                'receiver': 'unit-c',
                'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                'run_id': 'caca7959819077eaf5d1456b4ea31500',
                'secret_sha256': 'fe169e51dc5f984bcf1217b7f7c39e1ea2ee3ae5c5c148921b16aa4b9c7eca8d',
                'sender': 'unit-a',
                'sends': True,
                'transport': 'lora',
                'unit': 'unit-a'},
               {'application_expires_epoch_us': 1792218477458185,
                'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                'child_digest': 'cb297ddcbfc77e428cd7e9df9bfbd64764fb66fcb89358d4eb82b48f5d8b2262',
                'cipher_frame_limit': 512,
                'format': 'MR1.request',
                'full_caps': {'estimated_airtime_us': 60000000,
                              'ip_frames': 0,
                              'radio_attempts': 256,
                              'rx_stream_bytes': 0,
                              'tx_stream_bytes': 0},
                'grant_digest': 'e9c6af9ae71f836eceba9ec59635f03969465c972dde772173a35dd8a97abe0e',
                'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                'object': {'bytes': 74,
                           'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                'plan_sha256': '87c0ecee099e8da51db1b9a61684c29d51d8666af03baec076aee76e759e93d8',
                'receiver': 'unit-c',
                'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                'run_id': 'caca7959819077eaf5d1456b4ea31500',
                'secret_sha256': 'fe169e51dc5f984bcf1217b7f7c39e1ea2ee3ae5c5c148921b16aa4b9c7eca8d',
                'sender': 'unit-a',
                'sends': False,
                'transport': 'lora',
                'unit': 'unit-c'}],
  'row_sha256': '52bda2c1de061947c406a3fdb3a43bc52e6ff3f050176696f622a7ad76ae026e',
  'run_id': 'caca7959819077eaf5d1456b4ea31500'}]
FINAL_RESULT_SHA256 = 'c9c8f24b47f65bc78c1443d2df365d595319ef0a403a80e4b5848f3f082177b3'
NATIVE_COUNTS = {'unit-a': {'claim_count': 2, 'prepared_count': 2, 'resource_count': 1, 'start_count': 2},
 'unit-b': {'claim_count': 0, 'prepared_count': 0, 'resource_count': 0, 'start_count': 0},
 'unit-c': {'claim_count': 2, 'prepared_count': 2, 'resource_count': 1, 'start_count': 2}}
NEXT_HISTORY_RECEIPT_SHA256 = '25d5b0ee2c7c6f5f3e4c70fdb96fbe868d67399e1ef4b2c7765fc81455569a6f'
ORIGINAL_ABSOLUTE_END = 1792218747819986
ORIGINAL_HISTORY_RECEIPT_SHA256 = '20550444f2a434ad64f40a3d1c019df4066572823e1c797529e5f5e11074dfd0'
ORIGINAL_MANIFEST_SHA256 = '2ec2301138bad11bdd295aa9d5392c0c53c6b5ff216944ec6e971d7dc4b9d5c7'
ORIGINAL_MCU_ARTIFACT_SHA256 = '4bb48e8de4fe1b70eccb6f644250018e98646a0e2642f28949166abd1b42fcb7'
ORIGINAL_OPERATION_SHA256 = 'f48f7c83f41aa74930849b2d880b5e04984e128f858f0f00698b61be53c144b1'
ORIGINAL_OUTER_MONOTONIC_END = 1204178541081
ORIGINAL_PHASE_END = 1792218640105136
ORIGINAL_PHASE_START = 1792218040105136
ORIGINAL_RUNTIME_SHA256 = '3d7716d5c5d5038203880cf3ddbe8f0d9a36bfa569039133075b8dec31b0477b'
ORIGINAL_SCOPE_SHA256 = 'fc507188288f2831c1836766a2993099c1d763e6b6ce033f9946d784d6860f36'
ORIGINAL_WINDOW_EXPIRY_SHA256 = 'd3041c601d1f6e4eef982e0e57dafc7ae65fe97220339db2d79b5bc4be8a3032'
OWNER_SHA256 = {'unit-a': '4dbd26c00838e5d40d433f138737dc9b7fdc82cd5a43f1b18cf4fc2196da2a58',
 'unit-b': '327a3812808cc6599277bef1dba8f05c10e4438dd54402dfadf83da282d8e197',
 'unit-c': '0a2ccfd6a8f6bfb9654e63cb150811578891f5c6d75dc24059dce8126e92995e'}
PARENT = '291fcf787d7b336134735a239a6e334d'
POLICY_DIGEST = '6ec91261a76631d1798d2a5b0ded4be4c142b8885d9447ff81d6da5462c01e35'
POLICY_SHA256 = '29656d5bd5534f243dfadae495c3cc97ff3e297ae812f5969c895659f80cf22a'
PRIOR_SHA256 = {'unit-a': '7dd5c69e54e4fa1ed66f7ee148826d20b4d9263a97cffaa7bcdda1e34bac0e8f',
 'unit-b': 'f532027bf8fa169c3c60106bb318f92159541c2b33859ddb9d8f25be8dc706a6',
 'unit-c': '59f1461b28457f3a1ef6c0443eb519a8cf1a99397b34deca604b34ca17038483'}
PUBLIC_FILES = {'unit-a': {'parents/291fcf787d7b336134735a239a6e334d/capture.json': {'bytes': 1976157,
                                                                      'sha256': 'e36a389301deee436955d9989731c59cbe476928b4f401e2289a4226b0d61039'},
            'parents/291fcf787d7b336134735a239a6e334d/history-binding.json': {'bytes': 417,
                                                                              'sha256': '000ca50e87244257a1016c8bd99cdf34a8fde9d12197ee38c60ddd287df97584'},
            'parents/291fcf787d7b336134735a239a6e334d/history-identity-seal.json': {'bytes': 490089,
                                                                                    'sha256': 'd416a3b0de6c3fa244b508dba087e90c159a193250fac0a1094d8849ad30852e'},
            'parents/291fcf787d7b336134735a239a6e334d/legacy-grant-seal.json': {'bytes': 67656,
                                                                                'sha256': '0dfaff049fb4321872fc68c2b29460673805d72f728c8e3e8b3133e9a3c2082e'},
            'parents/291fcf787d7b336134735a239a6e334d/native.sqlite': {'bytes': 65536,
                                                                       'sha256': '447d07972adfa43185af7d4e25300ac276493fc133d08f63b11b8396dbbdccd0'},
            'parents/291fcf787d7b336134735a239a6e334d/native.sqlite.approval': {'bytes': 65,
                                                                                'sha256': 'e9071c1563e99d249ed94c14a6978fd03c427a70795cb1791c4a67e5e31b1fbb'},
            'parents/291fcf787d7b336134735a239a6e334d/native.sqlite.owner': {'bytes': 0,
                                                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'parents/291fcf787d7b336134735a239a6e334d/owner.json': {'bytes': 10524,
                                                                    'sha256': '4dbd26c00838e5d40d433f138737dc9b7fdc82cd5a43f1b18cf4fc2196da2a58'},
            'parents/291fcf787d7b336134735a239a6e334d/policy.json': {'bytes': 2293,
                                                                     'sha256': '29656d5bd5534f243dfadae495c3cc97ff3e297ae812f5969c895659f80cf22a'},
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities/891a03b0fd31296af6956eee7d09dc2f.json': {'bytes': 466,
                                                                                                                   'sha256': '5d625cc71959bf66f56675bcbd93e2f73e5ed45f2338f1df38e4d346d33252a7'},
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities/caca7959819077eaf5d1456b4ea31500.json': {'bytes': 466,
                                                                                                                   'sha256': '9ca35416104256d7bf7deac8ed73f5bee2ce1e96c62589081adb706906b51edc'},
            'parents/291fcf787d7b336134735a239a6e334d/prior.json': {'bytes': 23380,
                                                                    'sha256': '7dd5c69e54e4fa1ed66f7ee148826d20b4d9263a97cffaa7bcdda1e34bac0e8f'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/active': {'bytes': 8,
                                                             'sha256': '8b86d3b824a855c1b156ba78756ca36a80a6f1b1290204878987a662e0951324'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/attempts.sqlite': {'bytes': 20480,
                                                                      'sha256': '82d24d4fe7307037a0d88b34f3facfb9eceec548310beed1abfa623237e10b47'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/attempts.sqlite.owner': {'bytes': 0,
                                                                            'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/grant.json': {'bytes': 1329,
                                                                 'sha256': '0da16f4e61577323c4c9e1a2be981f8a9a6d72e91cd8644ecaf6d2bcc58e750f'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/job.json': {'bytes': 407,
                                                               'sha256': 'd7e9ee11dad03b9641cf5385d486bda9fae6fc7dc745d9e76e02a0896c242959'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/provision.json': {'bytes': 3140,
                                                                     'sha256': 'b2f43918fab590d9cb29f98dc6cc88d9c6ab1f34497494da3da4d73183ca6163'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/radio-ready.json': {'bytes': 437,
                                                                       'sha256': '5ab282bb96e5b7f756b7c5eb11f4ec6dd145cc6264a15fe913a2211653b9476f'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/result.json': {'bytes': 40712,
                                                                  'sha256': '7bad878f6f78a233488e0482497c947e5f370b402493eec4df3e4876c1719590'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/started': {'bytes': 65,
                                                              'sha256': '05c72e55545f64279f71ec71e726a83a56e537b6fc834534f0ad0aa1a567beda'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/verified-piece.json': {'bytes': 660,
                                                                          'sha256': '8e2d2768c056acb5b7543f1e018f711415e0c226f9e7566d1710a03446b3c4f6'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/wire.sqlite': {'bytes': 8192,
                                                                  'sha256': 'a4d7a86b169b0e5823f06ef1cc563e75007cdce8dbcb9c1b89b0a9454d03d39d'},
            'runs/caca7959819077eaf5d1456b4ea31500/active': {'bytes': 8,
                                                             'sha256': '8b86d3b824a855c1b156ba78756ca36a80a6f1b1290204878987a662e0951324'},
            'runs/caca7959819077eaf5d1456b4ea31500/attempts.sqlite': {'bytes': 12288,
                                                                      'sha256': '089694ed7940027052046579e37d9e0265ca38888713122b08b9dc6feeb4fbbf'},
            'runs/caca7959819077eaf5d1456b4ea31500/attempts.sqlite.owner': {'bytes': 0,
                                                                            'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'runs/caca7959819077eaf5d1456b4ea31500/grant.json': {'bytes': 1326,
                                                                 'sha256': '1c2c0c18570ab098f7c1b497d71d64b9e1ac1b15d2d706ef269e80dfc6b33370'},
            'runs/caca7959819077eaf5d1456b4ea31500/job.json': {'bytes': 407,
                                                               'sha256': '3374b3cf3799aa0e088b1c1e2d04ad1eb658098db235f8d4e9b48500ae3b4745'},
            'runs/caca7959819077eaf5d1456b4ea31500/provision.json': {'bytes': 3144,
                                                                     'sha256': '4b4e95a76021ec519ef96de77013b5829f9531b196fc529561ecd17748cdf799'},
            'runs/caca7959819077eaf5d1456b4ea31500/radio-ready.json': {'bytes': 468,
                                                                       'sha256': 'f8c51198b854c59f58eb332197e7b261c8392f596576f4106477c4b6068890d6'},
            'runs/caca7959819077eaf5d1456b4ea31500/result.json': {'bytes': 11166,
                                                                  'sha256': 'ab07d08385f0beccedd58c6d06021a3674aac028da57892c1e62bc1675dfeeee'},
            'runs/caca7959819077eaf5d1456b4ea31500/started': {'bytes': 65,
                                                              'sha256': 'e33a46b5dd6d0c9cb1258b1fc4c2648d340a99096676feb99b4c261577b76822'},
            'runs/caca7959819077eaf5d1456b4ea31500/wire.sqlite': {'bytes': 8192,
                                                                  'sha256': '04469abbba090db23e05fa50838321a9c6d6af27bf51fbe237fdb3bc71afcc29'}},
 'unit-b': {'parents/291fcf787d7b336134735a239a6e334d/capture.json': {'bytes': 2346727,
                                                                      'sha256': '5bee3b0079062ac8abf180a39f9ab4453db20cdd7650b2f69286d8885be1ebc2'},
            'parents/291fcf787d7b336134735a239a6e334d/history-binding.json': {'bytes': 417,
                                                                              'sha256': '60784c4256dc72a889d252ae64626fa3eff73f6c243515c2cab9c1502b752904'},
            'parents/291fcf787d7b336134735a239a6e334d/history-identity-seal.json': {'bytes': 215409,
                                                                                    'sha256': 'd293c4b63f92e703c4cc448bd85c7c692f0e277a122b716ff99602ff111cb279'},
            'parents/291fcf787d7b336134735a239a6e334d/legacy-grant-seal.json': {'bytes': 102543,
                                                                                'sha256': '95539e6026afa5b68d01686e15c50661b2d3d1cca5079957d3c682e69f05859a'},
            'parents/291fcf787d7b336134735a239a6e334d/native.sqlite': {'bytes': 49152,
                                                                       'sha256': 'f72a69f6636257ed651d54ad2b9f33ee05b7201c4d7624f15735b3c8bf2ed317'},
            'parents/291fcf787d7b336134735a239a6e334d/native.sqlite.approval': {'bytes': 65,
                                                                                'sha256': '4e6bde089967e17c57af6702547be8f9d600037caf14433936ae962ba7796ec3'},
            'parents/291fcf787d7b336134735a239a6e334d/native.sqlite.owner': {'bytes': 0,
                                                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'parents/291fcf787d7b336134735a239a6e334d/owner.json': {'bytes': 10524,
                                                                    'sha256': '327a3812808cc6599277bef1dba8f05c10e4438dd54402dfadf83da282d8e197'},
            'parents/291fcf787d7b336134735a239a6e334d/policy.json': {'bytes': 2293,
                                                                     'sha256': '29656d5bd5534f243dfadae495c3cc97ff3e297ae812f5969c895659f80cf22a'},
            'parents/291fcf787d7b336134735a239a6e334d/prior.json': {'bytes': 35219,
                                                                    'sha256': 'f532027bf8fa169c3c60106bb318f92159541c2b33859ddb9d8f25be8dc706a6'}},
 'unit-c': {'parents/291fcf787d7b336134735a239a6e334d/capture.json': {'bytes': 2533504,
                                                                      'sha256': '0ca3ec9edbae3dd8906be9c038fd671a14c7b7870ff6b77f7f72aab9a7f6156f'},
            'parents/291fcf787d7b336134735a239a6e334d/history-binding.json': {'bytes': 418,
                                                                              'sha256': '5e44eed329e3263391244f5a5db78e6c595d39e2d982fd2005703c64a2a211a5'},
            'parents/291fcf787d7b336134735a239a6e334d/history-identity-seal.json': {'bytes': 536822,
                                                                                    'sha256': 'a51e7abd2974537dbac36bae39d95a79ac7aa41aeac986d9a1691ad0c3ec8e70'},
            'parents/291fcf787d7b336134735a239a6e334d/legacy-grant-seal.json': {'bytes': 104449,
                                                                                'sha256': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc'},
            'parents/291fcf787d7b336134735a239a6e334d/native.sqlite': {'bytes': 65536,
                                                                       'sha256': '452f4db6b76bb5f4e797016fbc154682aa17e4e84cfe4a51791c76ab647bc2fa'},
            'parents/291fcf787d7b336134735a239a6e334d/native.sqlite.approval': {'bytes': 65,
                                                                                'sha256': 'b7bb6dc1651198f114f00597d70638ed02cad082e9642ce070ed481030efdfb8'},
            'parents/291fcf787d7b336134735a239a6e334d/native.sqlite.owner': {'bytes': 0,
                                                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'parents/291fcf787d7b336134735a239a6e334d/owner.json': {'bytes': 10524,
                                                                    'sha256': '0a2ccfd6a8f6bfb9654e63cb150811578891f5c6d75dc24059dce8126e92995e'},
            'parents/291fcf787d7b336134735a239a6e334d/policy.json': {'bytes': 2293,
                                                                     'sha256': '29656d5bd5534f243dfadae495c3cc97ff3e297ae812f5969c895659f80cf22a'},
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities/891a03b0fd31296af6956eee7d09dc2f.json': {'bytes': 467,
                                                                                                                   'sha256': '3ae2b9ff2fb97266d201414c6f6ddcc55ecf88c129c1c3bbd2bab3fc6bff125e'},
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities/caca7959819077eaf5d1456b4ea31500.json': {'bytes': 467,
                                                                                                                   'sha256': '0883410986f9fcaf16134900c759426636ffe482acdae1c357a7d13671f30092'},
            'parents/291fcf787d7b336134735a239a6e334d/prior.json': {'bytes': 34696,
                                                                    'sha256': '59f1461b28457f3a1ef6c0443eb519a8cf1a99397b34deca604b34ca17038483'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/active': {'bytes': 8,
                                                             'sha256': '8b86d3b824a855c1b156ba78756ca36a80a6f1b1290204878987a662e0951324'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/attempts.sqlite': {'bytes': 20480,
                                                                      'sha256': '54b82419c1a48fc473ac90256081ce0d299270a7e4bcea091dd51fba9fb523a4'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/attempts.sqlite.owner': {'bytes': 0,
                                                                            'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/grant.json': {'bytes': 1329,
                                                                 'sha256': 'f3b649d94cb7453154378dd69b4fe3a59846198f3fe40697df59245668d6c830'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/job.json': {'bytes': 406,
                                                               'sha256': '832009d79a578a743ada6f25f336aabf63b413ee0eb58011c0c1e5d73a5d86d0'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/provision.json': {'bytes': 3325,
                                                                     'sha256': '4b39528472ed10bc7b655cf104e491bd5ac8cfe7ae179ae3f01fe0b46f89b59f'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/radio-ready.json': {'bytes': 467,
                                                                       'sha256': '0904be28b7b1db0a69c94057f8955133d63b6b478a98fdc98956dfc71f2ba65f'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/result.json': {'bytes': 44272,
                                                                  'sha256': 'ca21acf2fa77763732c0aa5c2a81fd044adfd5b7db1c1eca2979e4cc30dae44b'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/started': {'bytes': 65,
                                                              'sha256': '05c72e55545f64279f71ec71e726a83a56e537b6fc834534f0ad0aa1a567beda'},
            'runs/891a03b0fd31296af6956eee7d09dc2f/wire.sqlite': {'bytes': 8192,
                                                                  'sha256': '4aa69b135b74961869459d818ea0ceb62a321c3b46b4cd6a474d2a84b96aae86'},
            'runs/caca7959819077eaf5d1456b4ea31500/active': {'bytes': 8,
                                                             'sha256': '8b86d3b824a855c1b156ba78756ca36a80a6f1b1290204878987a662e0951324'},
            'runs/caca7959819077eaf5d1456b4ea31500/attempts.sqlite': {'bytes': 12288,
                                                                      'sha256': 'cab513642c38cbba7a3898c3cfe72ded26133cd41cbd74ec122c7dc393622ba2'},
            'runs/caca7959819077eaf5d1456b4ea31500/attempts.sqlite.owner': {'bytes': 0,
                                                                            'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'runs/caca7959819077eaf5d1456b4ea31500/grant.json': {'bytes': 1326,
                                                                 'sha256': 'cd78e7786563dc8acf231a0b5c8a443f7ba9afc43d12486372ea861560ac6167'},
            'runs/caca7959819077eaf5d1456b4ea31500/job.json': {'bytes': 406,
                                                               'sha256': '1f1bbcc5d6f85cb71805bf663051bf1a68c1ff76ab5e6adfa5730677a6a93a88'},
            'runs/caca7959819077eaf5d1456b4ea31500/provision.json': {'bytes': 3135,
                                                                     'sha256': '2c62f146b828725b8ff026deaa58629ddd56e1412c2f54cb44754c89e1ad6b85'},
            'runs/caca7959819077eaf5d1456b4ea31500/radio-ready.json': {'bytes': 436,
                                                                       'sha256': 'd86369c6b1c47208cbce3be10f3a1f20d7bbea46f9947c0f64116ed6c3eda580'},
            'runs/caca7959819077eaf5d1456b4ea31500/result.json': {'bytes': 11330,
                                                                  'sha256': '5a0a1e0061a82c2f010bea955d21b9ec79004bf0ded79d3cfbc00dda1ca6dab1'},
            'runs/caca7959819077eaf5d1456b4ea31500/started': {'bytes': 65,
                                                              'sha256': 'e33a46b5dd6d0c9cb1258b1fc4c2648d340a99096676feb99b4c261577b76822'},
            'runs/caca7959819077eaf5d1456b4ea31500/wire.sqlite': {'bytes': 8192,
                                                                  'sha256': 'af53d0c6400a4ec5de162ad54d23fec0a588e72dd705a8a559da1f730500812f'}}}
RAW_INTAKE_COMPLETE_SHA256 = 'e481562f3f6d563123984037d8c7c07dc038ed19d78f3688ca642a301bfc0681'
RAW_UNIT_SHA256 = {'unit-a': '846684c2cb02390006f03736424179ad1d667fd70edf6cbfd3be28f784a26ab4',
 'unit-b': '65cf260651747b4696889d2d0f8068d0a296d566f154ec1860b03ba29ae7f41d',
 'unit-c': '4d950d352ddc52cc8d942fdb074a225300a8e791759de42c0552216044200a36'}
RUN_IDS = ('caca7959819077eaf5d1456b4ea31500', '891a03b0fd31296af6956eee7d09dc2f')
RUN_FORMATS = {'891a03b0fd31296af6956eee7d09dc2f': 'MR1.response', 'caca7959819077eaf5d1456b4ea31500': 'MR1.request'}
PREPARED_CLAIMS = {'unit-a': {'891a03b0fd31296af6956eee7d09dc2f': {'application_deadline_monotonic_us': 677830165812,
                                                 'application_expires_epoch_us': 1792218477458185,
                                                 'child_caps': {'cost': {'estimated_airtime_us': 60000000,
                                                                         'ip_frames': 0,
                                                                         'radio_attempts': 256,
                                                                         'rx_stream_bytes': 0,
                                                                         'tx_stream_bytes': 0},
                                                                'digest': 'f2f96f0cce808f65cb3812f62fd1a54ad133b9ba7101a5abfe5568d2fc2a82e8',
                                                                'metadata': {'application_expires_epoch_us': 1792218477458185,
                                                                             'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                                                                             'cipher_frame_limit': 512,
                                                                             'format': 'MR1.response',
                                                                             'grant_digest': 'e7817f8783c250af5e3745c613d8091382db004bc722d207ed023ab44943431f',
                                                                             'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                                                                             'object': {'bytes': 8192,
                                                                                        'sha256': 'b3d5819d794eae7a6781f2b57be166a348fa4df54fd844733e7c85a1ccf6c740'},
                                                                             'parent_id': '291fcf787d7b336134735a239a6e334d',
                                                                             'plan_sha256': '9e3bd31bf8a32a27185dc2b064eaf1a3a10e54dffe5d8c28d5626cefdc0462b2',
                                                                             'policy_digest': '6ec91261a76631d1798d2a5b0ded4be4c142b8885d9447ff81d6da5462c01e35',
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
                                                                             'receiver': 'unit-a',
                                                                             'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                                                                             'run_id': '891a03b0fd31296af6956eee7d09dc2f',
                                                                             'secret_sha256': '50252327759d545331f1986089416b73236d9809694ffc9f591d783a12195cd6',
                                                                             'sender': 'unit-c',
                                                                             'sends': False,
                                                                             'source_run_id': None,
                                                                             'transport': 'lora',
                                                                             'unit': 'unit-a'}},
                                                 'controller_allocation_id': '3055d18c796c3d68541ec4be25f860939b5d06f4ffa5a791bf68c65523cd547e',
                                                 'grant_context_sha256': 'e7817f8783c250af5e3745c613d8091382db004bc722d207ed023ab44943431f',
                                                 'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                                                 'native_claim_id': 'ff967c2fa969d11a92ba1f04adf76ba96f53c80e2322f6ee44340e96a0501711',
                                                 'native_plan_sha256': '32c56d51108b6379603f1cf13effe9f30308519244b6f776203ad4bf22ff4dd6',
                                                 'original_plan_sha256': '9e3bd31bf8a32a27185dc2b064eaf1a3a10e54dffe5d8c28d5626cefdc0462b2',
                                                 'parent_id': '291fcf787d7b336134735a239a6e334d',
                                                 'policy_digest': '6ec91261a76631d1798d2a5b0ded4be4c142b8885d9447ff81d6da5462c01e35',
                                                 'prepared': True,
                                                 'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                                                 'run_id': '891a03b0fd31296af6956eee7d09dc2f',
                                                 'schema': 'meshticulum.native-parent-claim.v1',
                                                 'secret_sha256': '50252327759d545331f1986089416b73236d9809694ffc9f591d783a12195cd6',
                                                 'unit': 'unit-a'},
            'caca7959819077eaf5d1456b4ea31500': {'application_deadline_monotonic_us': 677830165812,
                                                 'application_expires_epoch_us': 1792218477458185,
                                                 'child_caps': {'cost': {'estimated_airtime_us': 60000000,
                                                                         'ip_frames': 0,
                                                                         'radio_attempts': 256,
                                                                         'rx_stream_bytes': 0,
                                                                         'tx_stream_bytes': 0},
                                                                'digest': '262a7efc67e4839206105691aaeb0935c395a60d69c722835032ffd88b97b8b0',
                                                                'metadata': {'application_expires_epoch_us': 1792218477458185,
                                                                             'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                                                                             'cipher_frame_limit': 512,
                                                                             'format': 'MR1.request',
                                                                             'grant_digest': 'e9c6af9ae71f836eceba9ec59635f03969465c972dde772173a35dd8a97abe0e',
                                                                             'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                                                                             'object': {'bytes': 74,
                                                                                        'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                                                                             'parent_id': '291fcf787d7b336134735a239a6e334d',
                                                                             'plan_sha256': '8c03256391a09d98c6c35b520a6cab117fb2a72b65f3737265926342adfc0f49',
                                                                             'policy_digest': '6ec91261a76631d1798d2a5b0ded4be4c142b8885d9447ff81d6da5462c01e35',
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
                                                                             'run_id': 'caca7959819077eaf5d1456b4ea31500',
                                                                             'secret_sha256': 'fe169e51dc5f984bcf1217b7f7c39e1ea2ee3ae5c5c148921b16aa4b9c7eca8d',
                                                                             'sender': 'unit-a',
                                                                             'sends': True,
                                                                             'source_run_id': None,
                                                                             'transport': 'lora',
                                                                             'unit': 'unit-a'}},
                                                 'controller_allocation_id': 'fd5d5a0b9b76a2804e4da04197a9d5ef124f21633788d568708f0f3a31464f9d',
                                                 'grant_context_sha256': 'e9c6af9ae71f836eceba9ec59635f03969465c972dde772173a35dd8a97abe0e',
                                                 'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                                                 'native_claim_id': '090560a887a8e99d0a308d15c2e061a1fc2bd15fd419212b8f174e286f2bb14c',
                                                 'native_plan_sha256': '08cc2569ff3a2085e44c2ffdc3be9d3acd9502f553d90f9e5419935dd460f1c4',
                                                 'original_plan_sha256': '8c03256391a09d98c6c35b520a6cab117fb2a72b65f3737265926342adfc0f49',
                                                 'parent_id': '291fcf787d7b336134735a239a6e334d',
                                                 'policy_digest': '6ec91261a76631d1798d2a5b0ded4be4c142b8885d9447ff81d6da5462c01e35',
                                                 'prepared': True,
                                                 'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                                                 'run_id': 'caca7959819077eaf5d1456b4ea31500',
                                                 'schema': 'meshticulum.native-parent-claim.v1',
                                                 'secret_sha256': 'fe169e51dc5f984bcf1217b7f7c39e1ea2ee3ae5c5c148921b16aa4b9c7eca8d',
                                                 'unit': 'unit-a'}},
 'unit-b': {},
 'unit-c': {'891a03b0fd31296af6956eee7d09dc2f': {'application_deadline_monotonic_us': 174172927489,
                                                 'application_expires_epoch_us': 1792218477458185,
                                                 'child_caps': {'cost': {'estimated_airtime_us': 60000000,
                                                                         'ip_frames': 0,
                                                                         'radio_attempts': 256,
                                                                         'rx_stream_bytes': 0,
                                                                         'tx_stream_bytes': 0},
                                                                'digest': '21d82d5a109e7ae3199c1f6836731250f7537e0f4d7ae46ca768acb01f4a6451',
                                                                'metadata': {'application_expires_epoch_us': 1792218477458185,
                                                                             'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                                                                             'cipher_frame_limit': 512,
                                                                             'format': 'MR1.response',
                                                                             'grant_digest': 'e7817f8783c250af5e3745c613d8091382db004bc722d207ed023ab44943431f',
                                                                             'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                                                                             'object': {'bytes': 8192,
                                                                                        'sha256': 'b3d5819d794eae7a6781f2b57be166a348fa4df54fd844733e7c85a1ccf6c740'},
                                                                             'parent_id': '291fcf787d7b336134735a239a6e334d',
                                                                             'plan_sha256': 'edf009c8126eef8b6722fdfebf92be5a245dd9739372861ee122c0c42e81214e',
                                                                             'policy_digest': '6ec91261a76631d1798d2a5b0ded4be4c142b8885d9447ff81d6da5462c01e35',
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
                                                                             'receiver': 'unit-a',
                                                                             'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                                                                             'run_id': '891a03b0fd31296af6956eee7d09dc2f',
                                                                             'secret_sha256': '50252327759d545331f1986089416b73236d9809694ffc9f591d783a12195cd6',
                                                                             'sender': 'unit-c',
                                                                             'sends': True,
                                                                             'source_run_id': {'kind': 'served_public_chunk',
                                                                                               'request_run_id': 'caca7959819077eaf5d1456b4ea31500'},
                                                                             'transport': 'lora',
                                                                             'unit': 'unit-c'}},
                                                 'controller_allocation_id': '3055d18c796c3d68541ec4be25f860939b5d06f4ffa5a791bf68c65523cd547e',
                                                 'grant_context_sha256': 'e7817f8783c250af5e3745c613d8091382db004bc722d207ed023ab44943431f',
                                                 'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                                                 'native_claim_id': 'fab141eeb14b341caa674d3b92d5e8b85fd66fbf0fa50abbd1ade37059195563',
                                                 'native_plan_sha256': '733d56b520dc898b93103c80bf5f6e13b13f3003ea2f3c7ed48687c2a146fa8c',
                                                 'original_plan_sha256': 'edf009c8126eef8b6722fdfebf92be5a245dd9739372861ee122c0c42e81214e',
                                                 'parent_id': '291fcf787d7b336134735a239a6e334d',
                                                 'policy_digest': '6ec91261a76631d1798d2a5b0ded4be4c142b8885d9447ff81d6da5462c01e35',
                                                 'prepared': True,
                                                 'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                                                 'run_id': '891a03b0fd31296af6956eee7d09dc2f',
                                                 'schema': 'meshticulum.native-parent-claim.v1',
                                                 'secret_sha256': '50252327759d545331f1986089416b73236d9809694ffc9f591d783a12195cd6',
                                                 'unit': 'unit-c'},
            'caca7959819077eaf5d1456b4ea31500': {'application_deadline_monotonic_us': 174172927489,
                                                 'application_expires_epoch_us': 1792218477458185,
                                                 'child_caps': {'cost': {'estimated_airtime_us': 60000000,
                                                                         'ip_frames': 0,
                                                                         'radio_attempts': 256,
                                                                         'rx_stream_bytes': 0,
                                                                         'tx_stream_bytes': 0},
                                                                'digest': 'cb297ddcbfc77e428cd7e9df9bfbd64764fb66fcb89358d4eb82b48f5d8b2262',
                                                                'metadata': {'application_expires_epoch_us': 1792218477458185,
                                                                             'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                                                                             'cipher_frame_limit': 512,
                                                                             'format': 'MR1.request',
                                                                             'grant_digest': 'e9c6af9ae71f836eceba9ec59635f03969465c972dde772173a35dd8a97abe0e',
                                                                             'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                                                                             'object': {'bytes': 74,
                                                                                        'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                                                                             'parent_id': '291fcf787d7b336134735a239a6e334d',
                                                                             'plan_sha256': '87c0ecee099e8da51db1b9a61684c29d51d8666af03baec076aee76e759e93d8',
                                                                             'policy_digest': '6ec91261a76631d1798d2a5b0ded4be4c142b8885d9447ff81d6da5462c01e35',
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
                                                                             'run_id': 'caca7959819077eaf5d1456b4ea31500',
                                                                             'secret_sha256': 'fe169e51dc5f984bcf1217b7f7c39e1ea2ee3ae5c5c148921b16aa4b9c7eca8d',
                                                                             'sender': 'unit-a',
                                                                             'sends': False,
                                                                             'source_run_id': None,
                                                                             'transport': 'lora',
                                                                             'unit': 'unit-c'}},
                                                 'controller_allocation_id': 'fd5d5a0b9b76a2804e4da04197a9d5ef124f21633788d568708f0f3a31464f9d',
                                                 'grant_context_sha256': 'e9c6af9ae71f836eceba9ec59635f03969465c972dde772173a35dd8a97abe0e',
                                                 'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                                                 'native_claim_id': '89f696415fd1d1eda5660bd6bf538470594bb1e13256444c61f350f431c713a4',
                                                 'native_plan_sha256': 'b4194fb281d172cf0833df7dd45cb77d558f8c331c7768d6d61b86cb958b95f3',
                                                 'original_plan_sha256': '87c0ecee099e8da51db1b9a61684c29d51d8666af03baec076aee76e759e93d8',
                                                 'parent_id': '291fcf787d7b336134735a239a6e334d',
                                                 'policy_digest': '6ec91261a76631d1798d2a5b0ded4be4c142b8885d9447ff81d6da5462c01e35',
                                                 'prepared': True,
                                                 'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                                                 'run_id': 'caca7959819077eaf5d1456b4ea31500',
                                                 'schema': 'meshticulum.native-parent-claim.v1',
                                                 'secret_sha256': 'fe169e51dc5f984bcf1217b7f7c39e1ea2ee3ae5c5c148921b16aa4b9c7eca8d',
                                                 'unit': 'unit-c'}}}
OUTCOME_SHA256 = {'unit-a': {'891a03b0fd31296af6956eee7d09dc2f': '373b09bb15b4b300ddc148b4a710d384ff5b25b8915bc9391ea2f2e30a170305',
            'caca7959819077eaf5d1456b4ea31500': '23ae8ebab50a665c9c7404c3f0265543e784c1d3cbaec8b0815214054673d167'},
 'unit-b': {},
 'unit-c': {'891a03b0fd31296af6956eee7d09dc2f': 'b0146a73f976b9d3572621b64da85b720e643789a04cc89452ee18c7300468ec',
            'caca7959819077eaf5d1456b4ea31500': '738184c1d1d4c152388b79059f0e898c15ec1cbf01fa44291a730515f495af2d'}}
NATIVE_CLAIMS = {'unit-a': [{'application_deadline_monotonic_us': 677830165812,
             'child': {'application_expires_epoch_us': 1792218477458185,
                       'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                       'child_digest': '262a7efc67e4839206105691aaeb0935c395a60d69c722835032ffd88b97b8b0',
                       'cipher_frame_limit': 512,
                       'format': 'MR1.request',
                       'full_caps': {'estimated_airtime_us': 60000000,
                                     'ip_frames': 0,
                                     'radio_attempts': 256,
                                     'rx_stream_bytes': 0,
                                     'tx_stream_bytes': 0},
                       'grant_digest': 'e9c6af9ae71f836eceba9ec59635f03969465c972dde772173a35dd8a97abe0e',
                       'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                       'object': {'bytes': 74,
                                  'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                       'plan_sha256': '8c03256391a09d98c6c35b520a6cab117fb2a72b65f3737265926342adfc0f49',
                       'receiver': 'unit-c',
                       'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                       'run_id': 'caca7959819077eaf5d1456b4ea31500',
                       'secret_sha256': 'fe169e51dc5f984bcf1217b7f7c39e1ea2ee3ae5c5c148921b16aa4b9c7eca8d',
                       'sender': 'unit-a',
                       'sends': True,
                       'transport': 'lora',
                       'unit': 'unit-a'},
             'controller_allocation_id': 'fd5d5a0b9b76a2804e4da04197a9d5ef124f21633788d568708f0f3a31464f9d',
             'native_claim_id': '090560a887a8e99d0a308d15c2e061a1fc2bd15fd419212b8f174e286f2bb14c',
             'native_plan_sha256': '08cc2569ff3a2085e44c2ffdc3be9d3acd9502f553d90f9e5419935dd460f1c4',
             'prepared': True,
             'prepared_row_sha256': 'bc39f56e58394142cdc28fb74ce5a892b8df8ab006360e9ebafbc86de7a2d106',
             'resource_id': 'ed2fce654c5c1f69515c7429df418e3a7ad7d66f41aa4395d642c8013e7a9441',
             'row_sha256': '8df57c8f645f97de34dcffa93e16d5d17e7611a71e0d113bf7afd93419de6517',
             'start_row_sha256': 'dfc9858c1b3dbe7ec162d5874bf2c01b4a1397edbbe62cda4ae988f7444c7e7f',
             'started': True},
            {'application_deadline_monotonic_us': 677830165812,
             'child': {'application_expires_epoch_us': 1792218477458185,
                       'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                       'child_digest': 'f2f96f0cce808f65cb3812f62fd1a54ad133b9ba7101a5abfe5568d2fc2a82e8',
                       'cipher_frame_limit': 512,
                       'format': 'MR1.response',
                       'full_caps': {'estimated_airtime_us': 60000000,
                                     'ip_frames': 0,
                                     'radio_attempts': 256,
                                     'rx_stream_bytes': 0,
                                     'tx_stream_bytes': 0},
                       'grant_digest': 'e7817f8783c250af5e3745c613d8091382db004bc722d207ed023ab44943431f',
                       'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                       'object': {'bytes': 8192,
                                  'sha256': 'b3d5819d794eae7a6781f2b57be166a348fa4df54fd844733e7c85a1ccf6c740'},
                       'plan_sha256': '9e3bd31bf8a32a27185dc2b064eaf1a3a10e54dffe5d8c28d5626cefdc0462b2',
                       'receiver': 'unit-a',
                       'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                       'run_id': '891a03b0fd31296af6956eee7d09dc2f',
                       'secret_sha256': '50252327759d545331f1986089416b73236d9809694ffc9f591d783a12195cd6',
                       'sender': 'unit-c',
                       'sends': False,
                       'transport': 'lora',
                       'unit': 'unit-a'},
             'controller_allocation_id': '3055d18c796c3d68541ec4be25f860939b5d06f4ffa5a791bf68c65523cd547e',
             'native_claim_id': 'ff967c2fa969d11a92ba1f04adf76ba96f53c80e2322f6ee44340e96a0501711',
             'native_plan_sha256': '32c56d51108b6379603f1cf13effe9f30308519244b6f776203ad4bf22ff4dd6',
             'prepared': True,
             'prepared_row_sha256': '0fcc2a5c90c6f3840ceba24a9e23954e744ad5fcfe14354d3bca47d4f0269e65',
             'resource_id': 'ed2fce654c5c1f69515c7429df418e3a7ad7d66f41aa4395d642c8013e7a9441',
             'row_sha256': '77f65fdd895e29d1f0835fce20227bda49d49963fe0f314384991ae9973a2834',
             'start_row_sha256': '98d306134280c94d84a1b3a57d6336c1edd280ee1718181b56e3560c649c176e',
             'started': True}],
 'unit-b': [],
 'unit-c': [{'application_deadline_monotonic_us': 174172927489,
             'child': {'application_expires_epoch_us': 1792218477458185,
                       'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                       'child_digest': 'cb297ddcbfc77e428cd7e9df9bfbd64764fb66fcb89358d4eb82b48f5d8b2262',
                       'cipher_frame_limit': 512,
                       'format': 'MR1.request',
                       'full_caps': {'estimated_airtime_us': 60000000,
                                     'ip_frames': 0,
                                     'radio_attempts': 256,
                                     'rx_stream_bytes': 0,
                                     'tx_stream_bytes': 0},
                       'grant_digest': 'e9c6af9ae71f836eceba9ec59635f03969465c972dde772173a35dd8a97abe0e',
                       'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                       'object': {'bytes': 74,
                                  'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                       'plan_sha256': '87c0ecee099e8da51db1b9a61684c29d51d8666af03baec076aee76e759e93d8',
                       'receiver': 'unit-c',
                       'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                       'run_id': 'caca7959819077eaf5d1456b4ea31500',
                       'secret_sha256': 'fe169e51dc5f984bcf1217b7f7c39e1ea2ee3ae5c5c148921b16aa4b9c7eca8d',
                       'sender': 'unit-a',
                       'sends': False,
                       'transport': 'lora',
                       'unit': 'unit-c'},
             'controller_allocation_id': 'fd5d5a0b9b76a2804e4da04197a9d5ef124f21633788d568708f0f3a31464f9d',
             'native_claim_id': '89f696415fd1d1eda5660bd6bf538470594bb1e13256444c61f350f431c713a4',
             'native_plan_sha256': 'b4194fb281d172cf0833df7dd45cb77d558f8c331c7768d6d61b86cb958b95f3',
             'prepared': True,
             'prepared_row_sha256': '263f9958a460fcaae0d44d74bb357ba29c16e1a953282dcbbe1f8fab23d30d0a',
             'resource_id': 'e2972483d3fddaa1d8459d1ecf1f2da9dfa0aeb5d2e2f7a5eb8786a96bbed438',
             'row_sha256': 'e613ec0cbb6595290a6010542cc115468ac33680da76bc6050f38c37e7ae3afb',
             'start_row_sha256': '3753eb9b3f8447074e0fe4f1330fa93a43bdc2333937e7b8377099d3c69c26bc',
             'started': True},
            {'application_deadline_monotonic_us': 174172927489,
             'child': {'application_expires_epoch_us': 1792218477458185,
                       'campaign_id': '10c5de7ff0b358b94ca8a686706ca9bd',
                       'child_digest': '21d82d5a109e7ae3199c1f6836731250f7537e0f4d7ae46ca768acb01f4a6451',
                       'cipher_frame_limit': 512,
                       'format': 'MR1.response',
                       'full_caps': {'estimated_airtime_us': 60000000,
                                     'ip_frames': 0,
                                     'radio_attempts': 256,
                                     'rx_stream_bytes': 0,
                                     'tx_stream_bytes': 0},
                       'grant_digest': 'e7817f8783c250af5e3745c613d8091382db004bc722d207ed023ab44943431f',
                       'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                       'object': {'bytes': 8192,
                                  'sha256': 'b3d5819d794eae7a6781f2b57be166a348fa4df54fd844733e7c85a1ccf6c740'},
                       'plan_sha256': 'edf009c8126eef8b6722fdfebf92be5a245dd9739372861ee122c0c42e81214e',
                       'receiver': 'unit-a',
                       'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                       'run_id': '891a03b0fd31296af6956eee7d09dc2f',
                       'secret_sha256': '50252327759d545331f1986089416b73236d9809694ffc9f591d783a12195cd6',
                       'sender': 'unit-c',
                       'sends': True,
                       'transport': 'lora',
                       'unit': 'unit-c'},
             'controller_allocation_id': '3055d18c796c3d68541ec4be25f860939b5d06f4ffa5a791bf68c65523cd547e',
             'native_claim_id': 'fab141eeb14b341caa674d3b92d5e8b85fd66fbf0fa50abbd1ade37059195563',
             'native_plan_sha256': '733d56b520dc898b93103c80bf5f6e13b13f3003ea2f3c7ed48687c2a146fa8c',
             'prepared': True,
             'prepared_row_sha256': 'e6f2c8168b606207eedc9c7c5afbc1db519aed96cbe462cee979f8015e539734',
             'resource_id': 'e2972483d3fddaa1d8459d1ecf1f2da9dfa0aeb5d2e2f7a5eb8786a96bbed438',
             'row_sha256': 'c7d2259e620cb64581f7cfecffe59eb8409b7e273426b48971c7b6810b589036',
             'start_row_sha256': '5793e40c43add88714a977cd6176f261b5f0f96631898560d8c995a47deef13f',
             'started': True}]}
NATIVE_RESOURCES = {'unit-a': [{'application_deadline_monotonic_us': 677830165815,
             'application_expires_epoch_us': 1792218477458185,
             'original_epoch_us': 1792218306999181,
             'original_monotonic_us': 677659706811,
             'resource_id': 'ed2fce654c5c1f69515c7429df418e3a7ad7d66f41aa4395d642c8013e7a9441',
             'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
             'row_sha256': '4a0ee9e9cdaf99a3032cb4b4e472a119e88f389d8a70b4686c7b1f7226922013'}],
 'unit-b': [],
 'unit-c': [{'application_deadline_monotonic_us': 174172927490,
             'application_expires_epoch_us': 1792218477458185,
             'original_epoch_us': 1792218307424837,
             'original_monotonic_us': 174002894142,
             'resource_id': 'e2972483d3fddaa1d8459d1ecf1f2da9dfa0aeb5d2e2f7a5eb8786a96bbed438',
             'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
             'row_sha256': 'e1c0b16e205878e0100872b9b2b331e6542e8a69ae06c3c6daf06f72c1971c77'}]}
DIRECTORY_NAMES = {'unit-a': {'parents/291fcf787d7b336134735a239a6e334d': ['capture.json',
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
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities': ['891a03b0fd31296af6956eee7d09dc2f.json',
                                                                             'caca7959819077eaf5d1456b4ea31500.json'],
            'runs/891a03b0fd31296af6956eee7d09dc2f': ['active',
                                                      'attempts.sqlite',
                                                      'attempts.sqlite.owner',
                                                      'grant.json',
                                                      'job.json',
                                                      'provision.json',
                                                      'radio-ready.json',
                                                      'receiver.bin',
                                                      'result.json',
                                                      'secret.bin',
                                                      'started',
                                                      'verified-piece.json',
                                                      'wire.sqlite'],
            'runs/caca7959819077eaf5d1456b4ea31500': ['active',
                                                      'attempts.sqlite',
                                                      'attempts.sqlite.owner',
                                                      'grant.json',
                                                      'job.json',
                                                      'provision.json',
                                                      'radio-ready.json',
                                                      'result.json',
                                                      'secret.bin',
                                                      'sender.bin',
                                                      'started',
                                                      'wire.sqlite']},
 'unit-b': {'parents/291fcf787d7b336134735a239a6e334d': ['capture.json',
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
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities': []},
 'unit-c': {'parents/291fcf787d7b336134735a239a6e334d': ['capture.json',
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
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities': ['891a03b0fd31296af6956eee7d09dc2f.json',
                                                                             'caca7959819077eaf5d1456b4ea31500.json'],
            'runs/891a03b0fd31296af6956eee7d09dc2f': ['active',
                                                      'attempts.sqlite',
                                                      'attempts.sqlite.owner',
                                                      'grant.json',
                                                      'job.json',
                                                      'provision.json',
                                                      'radio-ready.json',
                                                      'result.json',
                                                      'secret.bin',
                                                      'sender.bin',
                                                      'started',
                                                      'wire.sqlite'],
            'runs/caca7959819077eaf5d1456b4ea31500': ['active',
                                                      'attempts.sqlite',
                                                      'attempts.sqlite.owner',
                                                      'grant.json',
                                                      'job.json',
                                                      'provision.json',
                                                      'radio-ready.json',
                                                      'receiver.bin',
                                                      'result.json',
                                                      'secret.bin',
                                                      'started',
                                                      'wire.sqlite']}}
DIRECTORY_IDENTITIES = {'unit-a': {'parents/291fcf787d7b336134735a239a6e334d': [66307, 6415177, 16832, 0],
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities': [66307, 6415178, 16832, 0],
            'runs/891a03b0fd31296af6956eee7d09dc2f': [66307, 6415205, 16832, 0],
            'runs/caca7959819077eaf5d1456b4ea31500': [66307, 6415194, 16832, 0]},
 'unit-b': {'parents/291fcf787d7b336134735a239a6e334d': [66307, 3779233, 16832, 0],
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities': [66307, 3779234, 16832, 0]},
 'unit-c': {'parents/291fcf787d7b336134735a239a6e334d': [66305, 11024985, 16832, 0],
            'parents/291fcf787d7b336134735a239a6e334d/prepared-identities': [66305, 11030258, 16832, 0],
            'runs/891a03b0fd31296af6956eee7d09dc2f': [66305, 11030357, 16832, 0],
            'runs/caca7959819077eaf5d1456b4ea31500': [66305, 11030346, 16832, 0]}}
OPAQUE_LEAF_STATS = {'unit-a': {'runs/891a03b0fd31296af6956eee7d09dc2f/receiver.bin': [66307,
                                                                   6415224,
                                                                   33152,
                                                                   0,
                                                                   0,
                                                                   1,
                                                                   8192,
                                                                   1792218408463193557,
                                                                   1792218408463193557],
            'runs/891a03b0fd31296af6956eee7d09dc2f/secret.bin': [66307,
                                                                 6415209,
                                                                 33152,
                                                                 0,
                                                                 0,
                                                                 1,
                                                                 32,
                                                                 1792218358098076114,
                                                                 1792218358098076114],
            'runs/caca7959819077eaf5d1456b4ea31500/secret.bin': [66307,
                                                                 6415196,
                                                                 33152,
                                                                 0,
                                                                 0,
                                                                 1,
                                                                 32,
                                                                 1792218307196946979,
                                                                 1792218307196946979],
            'runs/caca7959819077eaf5d1456b4ea31500/sender.bin': [66307,
                                                                 6415198,
                                                                 33152,
                                                                 0,
                                                                 0,
                                                                 1,
                                                                 74,
                                                                 1792218307240947955,
                                                                 1792218307240947955]},
 'unit-b': {},
 'unit-c': {'runs/891a03b0fd31296af6956eee7d09dc2f/secret.bin': [66305,
                                                                 11030362,
                                                                 33152,
                                                                 0,
                                                                 0,
                                                                 1,
                                                                 32,
                                                                 1792218358810384331,
                                                                 1792218358810384331],
            'runs/891a03b0fd31296af6956eee7d09dc2f/sender.bin': [66305,
                                                                 11030366,
                                                                 33152,
                                                                 0,
                                                                 0,
                                                                 1,
                                                                 8192,
                                                                 1792218358837050913,
                                                                 1792218358837050913],
            'runs/caca7959819077eaf5d1456b4ea31500/receiver.bin': [66305,
                                                                   11030364,
                                                                   33152,
                                                                   0,
                                                                   0,
                                                                   1,
                                                                   74,
                                                                   1792218323177164979,
                                                                   1792218323177164979],
            'runs/caca7959819077eaf5d1456b4ea31500/secret.bin': [66305,
                                                                 11030350,
                                                                 33152,
                                                                 0,
                                                                 0,
                                                                 1,
                                                                 32,
                                                                 1792218307563881589,
                                                                 1792218307563881589]}}
NATIVE_PROJECTION_SHA256 = {'unit-a': '17a82e360c6c57d5899d6472b5fbeebe2371212d625434c7a28a21383c5d927d',
 'unit-b': '9df244365d6198a6032a72db962f6b5d9b99c4331c0fd21ec2f75ce26dcc11aa',
 'unit-c': 'bafe831aec3a399cbd2c76fa12e2e4d396ecf779cfeca6864a3166fb39451af3'}
H49_HISTORY_SHA256 = {'unit-a': 'f273eecd96c77a59d5300f0c3f02cfca33d56c1899a1438a7487574e43e9e358',
 'unit-b': '1671e7be3f716b25d216d2897f11a101a1825252bd69bb21ce9176f675ae504a',
 'unit-c': '0e6595ecc8056e2c8f24df0edb1adc16feae720b6d22f9a354b0548d281c4686'}
PROJECTION = ('status', 'scoped_rows_complete', 'pending_unknown', 'policy_row_sha256', 'prior_lineage', 'detail', 'files')
H49_PREFIX = previous.H48_PREFIX+(previous.PARENT,)
OWNER_SCHEMA = previous.OWNER_SCHEMA
KIND = previous.KIND
STAT_FIELDS = ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def declared_partition(policy):
    require(policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and digest(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==KIND,
        '291fcf_original_policy_changed')
    for local in policy.units:
        require(local.prior_debt.plus(Cost()if local.unit=='unit-b'else Cost(1024,240000000))==local.limits,
            '291fcf_whole_parent_reservation_changed')
    return True


def owner_view(record,policy,*,unit):
    declared_partition(policy)
    require(unit in OWNER_SHA256 and type(record)is dict and digest(record)==OWNER_SHA256[unit],
        '291fcf_raw_experiment_owner_changed')
    require(record['schema']==OWNER_SCHEMA and record['policy_digest']==POLICY_DIGEST
        and record['qualification_kind']==KIND and record['comparison_scope_sha256']==ORIGINAL_SCOPE_SHA256
        and record['history_receipt_sha256']==ORIGINAL_HISTORY_RECEIPT_SHA256
        and digest(record['lora_mcu_artifact'])==ORIGINAL_MCU_ARTIFACT_SHA256
        and record['runtime_sha256']==ORIGINAL_RUNTIME_SHA256 and record['manifest_sha256']==ORIGINAL_MANIFEST_SHA256
        and record['actual_cost']is None and record['complete_global_debt']is False
        and all(record[k]is False for k in ('alpha_qualified','full_source_green','private_production_enabled',
            'physical_qualification','rf_qualified')),'291fcf_historical_experiment_binding_changed')
    return record


def endpoint_rows(unit):
    """Two materialized runs from the closed attempt, without renewed authority."""
    require(unit in OWNER_SHA256,'291fcf_unit_changed')
    return ([] if unit=='unit-b' else [dict(run_id=run,
        grant_sha256=PUBLIC_FILES[unit]['runs/'+run+'/grant.json']['sha256']) for run in RUN_IDS])


def row_for(unit,policy,owner,prior):
    owner_view(owner,policy,unit=unit);local=policy.unit(unit)
    require(type(prior)is dict and digest(prior)==PRIOR_SHA256[unit]
        and prior['previous_parent_ids']==list(H49_PREFIX) and prior['full_caps']==asdict(local.prior_debt)
        and prior['actual_cost']is None and prior['complete_global_debt']is False and prior['refunds']==0,
        '291fcf_original_prior_changed')
    allocated=Cost()if unit=='unit-b'else Cost(512,120000000)
    unused=Cost()if unit=='unit-b'else Cost(512,120000000)
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
        reporter_incomplete=dict(schema='meshticulum.retained-public-comparison-reporter-incomplete.v1',
            unit=unit,known_run_scopes=[r['run_id']for r in endpoint_rows(unit)],
            actual_claim_resource_prepared_start_counts=NATIVE_COUNTS[unit],
            prepared_identity_count=len(DIRECTORY_NAMES[unit]['parents/'+PARENT+'/prepared-identities']),
            actual_cost=None,no_refund=True,physical_quiescence_observed=False,callback_terminal_proved=False,
            original_native_final_audit_missing=True, original_safe_handoff_proven=False,
            raw_intake_complete_sha256=RAW_INTAKE_COMPLETE_SHA256,raw_unit_sha256=RAW_UNIT_SHA256[unit],
            original_result_sha256=FINAL_RESULT_SHA256,original_operation_sha256=ORIGINAL_OPERATION_SHA256,
            original_window_expiry_sha256=ORIGINAL_WINDOW_EXPIRY_SHA256))


def qualify_row(unit,policy,value):
    require(type(value)is dict and canonical(value)==canonical(row_for(unit,policy,value['owner'],value['prior_metadata'])),
        '291fcf_failed_endpoint_or_whole_paid_debt_changed')
    return value['paid_caps']


def require_final_bindings():
    require(type(NEXT_HISTORY_RECEIPT_SHA256)is str and re.fullmatch('[0-9a-f]{64}',NEXT_HISTORY_RECEIPT_SHA256),
        'pending_291fcf_H50_history_receipt')
    return True


def verify_public_bytes(unit,policy,value,raws,previous_history,native):
    """Bind actual public files and real native rows; opaque bytes are excluded."""
    qualify_row(unit,policy,value);owner=owner_view(value['owner'],policy,unit=unit)
    expected=PUBLIC_FILES[unit];prefix='parents/'+PARENT+'/'
    require(set(raws)==set(expected) and all(type(raws[k])is bytes and len(raws[k])==pin['bytes']
        and hashlib.sha256(raws[k]).hexdigest()==pin['sha256']for k,pin in expected.items()),
        '291fcf_actual_public_bytes_changed')
    require(raws[prefix+'policy.json']==canonical(value['policy'])
        and raws[prefix+'owner.json']==canonical(value['owner'])
        and raws[prefix+'prior.json']==canonical(value['prior_metadata']),'291fcf_original_parent_input_changed')
    capture=json.loads(capture_storage.decode(raws[prefix+'capture.json']))
    require(capture['schema']=='meshticulum.private-resume-actual-capture.v1'
        and capture['parent_id']==PARENT and capture['unit']==unit and capture['actual_history_verified']is True
        and capture['global_debt_complete']is False and capture['original_unknown_actual_cost']is None and capture['refunds']==0
        and digest(capture['history'])==H49_HISTORY_SHA256[unit]
        and canonical(capture['history'])==canonical(previous_history)
        and tuple(v['policy']['parent_id']for v in capture['history']['previous'])==H49_PREFIX
        and capture['native_phase']==owner['native_phase'][unit]
        and capture['history_binding_sha256']==owner['history_binding_sha256'],'291fcf_original_capture_or_H49_prefix_changed')
    require(native['role']=='native'and native['unit']==unit and native['parent_id']==PARENT
        and native['policy_digest']==policy.digest and native['status']=='scoped_rows_observed'
        and native['scoped_rows_complete']is native['policy_expired_at_observation']is True
        and native['pending_unknown']is native['database_writes']is False
        and native['expected_database_sha256']==value['database_sha256']
        and digest({k:native[k]for k in PROJECTION})==NATIVE_PROJECTION_SHA256[unit],
        '291fcf_actual_native_rows_changed')
    detail=native['detail'];observed={k:detail[k]for k in NATIVE_COUNTS[unit]}
    require(observed==NATIVE_COUNTS[unit]and all(type(v)is int for v in observed.values())
        and detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']
        and detail['limit']==value['paid_caps'],'291fcf_actual_counts_or_unsummed_debt_changed')
    require(detail['claims']==NATIVE_CLAIMS[unit] and detail['resources']==NATIVE_RESOURCES[unit]
        and detail['approval_bytes_match_enrollment']is True
        and detail['underlying_native_plan_correspondence_verified']is False,
        '291fcf_actual_native_projection_changed')
    require(DIRECTORY_NAMES[unit]['parents/'+PARENT+'/prepared-identities']==
        ([] if unit=='unit-b' else sorted(run+'.json' for run in RUN_IDS)),
        '291fcf_actual_prepared_namespace_changed')
    if unit!='unit-b':
        claims={v['child']['run_id']:v for v in detail['claims']}
        require(set(claims)==set(RUN_IDS),'291fcf_actual_run_scopes_changed')
        for run in RUN_IDS:
            run_prefix='runs/'+run+'/'
            grant=json.loads(raws[run_prefix+'grant.json'])
            provision=json.loads(raws[run_prefix+'provision.json'])
            receipt=provision['parent'];claim=claims[run]
            identity=json.loads(raws[prefix+'prepared-identities/'+run+'.json'])
            require(provision['schema']=='meshticulum.provisioned-public-pilot.v2'
                and receipt==PREPARED_CLAIMS[unit][run] and provision['unit']==unit and provision['run_id']==run
                and digest(grant)==receipt['native_plan_sha256']==claim['native_plan_sha256']
                and receipt['native_claim_id']==claim['native_claim_id']
                and receipt['prepared']is claim['prepared']is claim['started']is True
                and digest(receipt)==claim['prepared_row_sha256']
                and receipt['controller_allocation_id']==claim['controller_allocation_id']
                and grant['host_boot_id']==policy.unit(unit).host_boot_id and grant['unit']==unit
                and grant['session_id']==run and grant['service']['format']==RUN_FORMATS[run]
                and grant['service']['application_expires_epoch_us']==receipt['application_expires_epoch_us']
                and raws[run_prefix+'active']==b'retired\n','291fcf_prepared_native_plan_or_retirement_changed')
            require(identity['schema']=='meshticulum.private-resume-prepared-identity.v2'
                and identity['parent_id']==PARENT and identity['policy_digest']==policy.digest and identity['unit']==unit
                and identity['run_id']==run and identity['directory_identity']==DIRECTORY_IDENTITIES[unit]['runs/'+run]
                and identity['prepared_sha256']==digest(receipt)
                and identity['provision_sha256']==hashlib.sha256(raws[run_prefix+'provision.json']).hexdigest(),
                '291fcf_actual_prepared_identity_changed')
            outcome=json.loads(raws[run_prefix+'result.json'])
            require(digest(outcome)==OUTCOME_SHA256[unit][run]
                and outcome['schema']=='meshticulum.physical-service-run.v1'
                and outcome['unit']==unit and outcome['host_boot_id']==policy.unit(unit).host_boot_id
                and outcome['complete']is outcome['disarmed_on_exit']is True
                and outcome['parent']['native_claim_id']==receipt['native_claim_id']
                and outcome['parent']['started']is True
                and digest(outcome['parent'])==claim['start_row_sha256'],
                '291fcf_original_completed_endpoint_changed')
    return True


def _names(reader,path):
    reader.directory(path)
    fd=os.open('.',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=reader._nodes[path][0])
    try:return sorted(os.listdir(fd))
    finally:os.close(fd);reader.directory(path)


def metadata_files(reader,state,unit,value,*,guard,clock,current_boot,previous_history):
    """Fresh exact metadata, complete native snapshot and stat-only opaque leaves."""
    from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,boot
    from lantern_meshticulum.bench.pilot_parent_snapshot import Scope,collect
    policy=ParentPolicy.from_dict(value['policy']);qualify_row(unit,policy,value)
    owner=owner_view(value['owner'],policy,unit=unit)
    def current():
        guard();sample=clock();now_boot=current_boot();boot(now_boot)
        end=owner['native_phase'][unit]['deadline_monotonic_us']+ORIGINAL_ABSOLUTE_END-ORIGINAL_PHASE_END
        require(type(sample)is tuple and len(sample)==2 and all(type(v)is int and v>0 for v in sample)
            and sample[0]>ORIGINAL_ABSOLUTE_END
            and (now_boot!=policy.unit(unit).host_boot_id or sample[1]>end),'291fcf_original_clocks_not_expired')
        return sample
    def names():
        for relative,expected in DIRECTORY_NAMES[unit].items():
            current();path=state/relative
            require(_names(reader,path)==expected and reader.directories[path]==DIRECTORY_IDENTITIES[unit][relative],
                '291fcf_actual_directory_or_identity_changed')
        for relative,expected in OPAQUE_LEAF_STATS[unit].items():
            path=state/relative;reader.directory(path.parent)
            info=os.stat(path.name,dir_fd=reader._nodes[path.parent][0],follow_symlinks=False)
            actual=[getattr(info,k)for k in STAT_FIELDS]
            require(actual==expected,'291fcf_opaque_leaf_stat_changed')
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
    from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy
    policy=ParentPolicy.from_dict(value['policy']);qualify_row(unit,policy,value)
    detail=observation['detail'];sample=(observation['observation_original_epoch_us'],observation['observation_original_monotonic_us'])
    require(observation['role']=='controller'and observation['parent_id']==PARENT
        and observation['policy_digest']==POLICY_DIGEST and observation['expected_database_sha256']==CONTROLLER_DATABASE_SHA256
        and observation['status']=='scoped_rows_observed'and observation['scoped_rows_complete']is True
        and observation['policy_expired_at_observation']is True and observation['pending_unknown']is False
        and observation['database_writes']is False and type(detail['allocation_count'])is int and detail['allocation_count']==2
        and detail['allocations']==CONTROLLER_ALLOCATIONS
        and all(type(v)is int and v>0 for v in sample)and sample[0]>ORIGINAL_ABSOLUTE_END
        and (observation['current_host_boot_id']!=policy.controller_boot_id or sample[1]>ORIGINAL_OUTER_MONOTONIC_END),
        '291fcf_controller_allocation_or_expiry_changed')
    require(len(detail['units'])==3 and {v['unit']for v in detail['units']}==set(OWNER_SHA256)
        and all(canonical(v['limit'])==canonical(asdict(policy.unit(v['unit']).limits))
            and canonical(v['prior_plus_prepaid_caps'])==canonical(asdict(policy.unit(v['unit']).prior_debt.plus(Cost()if v['unit']=='unit-b'else Cost(512,120000000))))
            and canonical(v['full_prepaid_caps'])==canonical(asdict(Cost()if v['unit']=='unit-b'else Cost(512,120000000)))
            for v in detail['units']),'291fcf_controller_unsummed_whole_reservation_changed')
    return value['paid_caps']
