"""Exact expired 1a02 management failure; full paid debt, no permission/refund."""
from dataclasses import asdict
import hashlib
import json
import os
import re
from meshticulum.bench.wire import require
from .pilot_parent_caps import Cost,canonical
from . import capture_storage,lora_endpoint_incomplete_history as previous

CONTROLLER_DATABASE_SHA256 = '45c99e587cf3cd3c99f1030136551a98ed0961e96100242a48575be44fe51d3e'
FINAL_RESULT_SHA256 = 'ac30be916b612e6376a42e46b31329b3ed2d61d1bba432790b9e9b2badd64c80'
NATIVE_COUNTS = {'unit-a': {'claim_count': 1, 'prepared_count': 0, 'resource_count': 1, 'start_count': 0},
 'unit-b': {'claim_count': 0, 'prepared_count': 0, 'resource_count': 0, 'start_count': 0},
 'unit-c': {'claim_count': 1, 'prepared_count': 0, 'resource_count': 1, 'start_count': 0}}
NEXT_HISTORY_RECEIPT_SHA256 = '20550444f2a434ad64f40a3d1c019df4066572823e1c797529e5f5e11074dfd0'
ORIGINAL_ABSOLUTE_END = 1792096030744917
ORIGINAL_HISTORY_RECEIPT_SHA256 = '4a17d04dd7c3cc2f61ac62aa027909203ee3a14ef2c55abf4851aafff908b08e'
ORIGINAL_MANIFEST_SHA256 = '7aa80f1ea00495e93cc431f78f2ac96d9d0e9b5410dd306c856c160fdf7689b0'
ORIGINAL_MCU_ARTIFACT_SHA256 = '4bb48e8de4fe1b70eccb6f644250018e98646a0e2642f28949166abd1b42fcb7'
ORIGINAL_OPERATION_SHA256 = 'ab0612de36831366fc6cbe08841b02b09a516d4ca4b21a1c592c84084ad81072'
ORIGINAL_OUTER_MONOTONIC_END = 1184124551037
ORIGINAL_PHASE_END = 1792095925297401
ORIGINAL_PHASE_START = 1792095325297401
ORIGINAL_RUNTIME_SHA256 = 'dc0535ab2835d2e99c70edde9bb1acf2960e07b376f53ab6dc0e67bcdbb4a731'
ORIGINAL_SCOPE_SHA256 = 'b1c59d21937cd8864499f17dcf820bfdf3b550c280828c9d34e2ddbfd1cf18de'
ORIGINAL_WINDOW_EXPIRY_SHA256 = '82543e88d36901eff99c36d2f386a62e0ffcfb4ff64b840449167c4c715df443'
OWNER_SHA256 = {'unit-a': '95e8277394ece6ce3aa2b9edeab7104c3534f9df5f67a1af4043266028b1b324',
 'unit-b': 'fc0d583a7cdbb915071817b14ac29c8e096cce06fa328da18fd9c8dcfd2e73ab',
 'unit-c': '89900538e334cea0dcf12be379c60cd62a6e4eb0929d84cd4bd445b07102dff2'}
PARENT = '1a02ac26e525311caa32e180596d7160'
POLICY_DIGEST = '1765f2cd0e04d69d796af8cd71c56b689f78c0a8bf4ccfe33875914275063c84'
POLICY_SHA256 = 'd1f58a090306f966cacb3cd04c8a149ffcbf506f882ae279a143f04d1fa49b91'
PRIOR_SHA256 = {'unit-a': '0eb1f989bf901c9f72f5b55a2e8c600bb1457ae5f1b96e2870252a5beadf3099',
 'unit-b': '0af22e04f93bc2e8aa172dfc246ba7d02ce346127e434cb085fa4808f14bb529',
 'unit-c': '82846bc6792cf6ee20048d0ba1e5cec9c6315686acde7be1d8a38ac600752b87'}
PUBLIC_FILES = {'unit-a': {'parents/1a02ac26e525311caa32e180596d7160/capture.json': {'bytes': 1935974,
                                                                      'sha256': '158d0dff12d15b8e358207f156d9c9399ae46eeb4e7245a61c6cf4a458f4a73e'},
            'parents/1a02ac26e525311caa32e180596d7160/history-binding.json': {'bytes': 417,
                                                                              'sha256': '52b94932c40fbb702f9278d7123d034c091b15e70ba28192d2af0329f0fa224c'},
            'parents/1a02ac26e525311caa32e180596d7160/history-identity-seal.json': {'bytes': 487366,
                                                                                    'sha256': 'fd14ea85bdaee90536cff94633e1ad2aee019922901a85a8f469335e34b3aa47'},
            'parents/1a02ac26e525311caa32e180596d7160/legacy-grant-seal.json': {'bytes': 67656,
                                                                                'sha256': '0dfaff049fb4321872fc68c2b29460673805d72f728c8e3e8b3133e9a3c2082e'},
            'parents/1a02ac26e525311caa32e180596d7160/native.sqlite': {'bytes': 49152,
                                                                       'sha256': 'c35c7fa927645d347b157597951c78fbdf7538989fe6f2c60e73e31401696d68'},
            'parents/1a02ac26e525311caa32e180596d7160/native.sqlite.approval': {'bytes': 65,
                                                                                'sha256': '32c04f0fea1c3b3cdd4deac7756665cc51f9ef958f3d1d729a95def72fbc1114'},
            'parents/1a02ac26e525311caa32e180596d7160/native.sqlite.owner': {'bytes': 0,
                                                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'parents/1a02ac26e525311caa32e180596d7160/owner.json': {'bytes': 10522,
                                                                    'sha256': '95e8277394ece6ce3aa2b9edeab7104c3534f9df5f67a1af4043266028b1b324'},
            'parents/1a02ac26e525311caa32e180596d7160/policy.json': {'bytes': 2293,
                                                                     'sha256': 'd1f58a090306f966cacb3cd04c8a149ffcbf506f882ae279a143f04d1fa49b91'},
            'parents/1a02ac26e525311caa32e180596d7160/prior.json': {'bytes': 23345,
                                                                    'sha256': '0eb1f989bf901c9f72f5b55a2e8c600bb1457ae5f1b96e2870252a5beadf3099'}},
 'unit-b': {'parents/1a02ac26e525311caa32e180596d7160/capture.json': {'bytes': 2294763,
                                                                      'sha256': '7a6c18e140153e9c5ffcbd314308850973f46203ad97f5f39ad95ac68be41a7e'},
            'parents/1a02ac26e525311caa32e180596d7160/history-binding.json': {'bytes': 417,
                                                                              'sha256': 'bf6aa05400939dc38d0c12b0a94cb42bb599c2403a05a1ebce19d896a34bfdb8'},
            'parents/1a02ac26e525311caa32e180596d7160/history-identity-seal.json': {'bytes': 212685,
                                                                                    'sha256': '2191993b475c1efc0955aec801722c0adc784b36b87acedfc0fcdc16cf66316d'},
            'parents/1a02ac26e525311caa32e180596d7160/legacy-grant-seal.json': {'bytes': 102543,
                                                                                'sha256': '95539e6026afa5b68d01686e15c50661b2d3d1cca5079957d3c682e69f05859a'},
            'parents/1a02ac26e525311caa32e180596d7160/native.sqlite': {'bytes': 49152,
                                                                       'sha256': 'ac07f96807ecb5a611486b3f0e4a017529db84ce671e724a2ad43da663270a1e'},
            'parents/1a02ac26e525311caa32e180596d7160/native.sqlite.approval': {'bytes': 65,
                                                                                'sha256': '42dab38b9a952f93e9316873430f80ca2d8fd1721e4ebb33cf5ff35e3d924ab1'},
            'parents/1a02ac26e525311caa32e180596d7160/native.sqlite.owner': {'bytes': 0,
                                                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'parents/1a02ac26e525311caa32e180596d7160/owner.json': {'bytes': 10522,
                                                                    'sha256': 'fc0d583a7cdbb915071817b14ac29c8e096cce06fa328da18fd9c8dcfd2e73ab'},
            'parents/1a02ac26e525311caa32e180596d7160/policy.json': {'bytes': 2293,
                                                                     'sha256': 'd1f58a090306f966cacb3cd04c8a149ffcbf506f882ae279a143f04d1fa49b91'},
            'parents/1a02ac26e525311caa32e180596d7160/prior.json': {'bytes': 35184,
                                                                    'sha256': '0af22e04f93bc2e8aa172dfc246ba7d02ce346127e434cb085fa4808f14bb529'}},
 'unit-c': {'parents/1a02ac26e525311caa32e180596d7160/capture.json': {'bytes': 2482003,
                                                                      'sha256': 'e70bf157171ef7ee58a63f2fc6d861faaf87e3ef7c350ad055a01fba81ca5893'},
            'parents/1a02ac26e525311caa32e180596d7160/history-binding.json': {'bytes': 418,
                                                                              'sha256': '64e91ff2041c435caf3c8b7fe6a4da38415f31f9b7fe1e38f474c2676c27b458'},
            'parents/1a02ac26e525311caa32e180596d7160/history-identity-seal.json': {'bytes': 534086,
                                                                                    'sha256': '07dc592d1d5c8725814f8bf5f4c712655cdc7fea170bf47e6b5b8d7fc314b505'},
            'parents/1a02ac26e525311caa32e180596d7160/legacy-grant-seal.json': {'bytes': 104449,
                                                                                'sha256': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc'},
            'parents/1a02ac26e525311caa32e180596d7160/native.sqlite': {'bytes': 49152,
                                                                       'sha256': '70c6a62efb7cba98ca4757d91fd7c30b9ff9da595256417549df67218657fcf5'},
            'parents/1a02ac26e525311caa32e180596d7160/native.sqlite.approval': {'bytes': 65,
                                                                                'sha256': '81e842c4aa2872c31bb1c7073dd6ae5c01535aa9f0355145791862749e19da94'},
            'parents/1a02ac26e525311caa32e180596d7160/native.sqlite.owner': {'bytes': 0,
                                                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
            'parents/1a02ac26e525311caa32e180596d7160/owner.json': {'bytes': 10522,
                                                                    'sha256': '89900538e334cea0dcf12be379c60cd62a6e4eb0929d84cd4bd445b07102dff2'},
            'parents/1a02ac26e525311caa32e180596d7160/policy.json': {'bytes': 2293,
                                                                     'sha256': 'd1f58a090306f966cacb3cd04c8a149ffcbf506f882ae279a143f04d1fa49b91'},
            'parents/1a02ac26e525311caa32e180596d7160/prior.json': {'bytes': 34661,
                                                                    'sha256': '82846bc6792cf6ee20048d0ba1e5cec9c6315686acde7be1d8a38ac600752b87'}}}
RAW_INTAKE_COMPLETE_SHA256 = 'e130cff1903c41f490303f3b521394848977c02ae452aa6517c37e6ea28118f2'
RAW_UNIT_SHA256 = {'unit-a': 'e0380f8425c6a90a6e73df44b664c4d5326c1becbc4d232140308cf621bf4028',
 'unit-b': '5357a28aadee06f0a73ff5d18ab447aa737041306cd9394f4ca79618c04512f1',
 'unit-c': 'cef7e3297ac86083fd4e9e7533e50735d4a944c0afba203147157be5d81c0ae1'}
RUN_ID = '040c3aa24a6cca01a9caceae7c18edc3'
CONTROLLER_ALLOCATION_ID = 'dd8fa294271bfabc3a6f65acf20d75d7d73811bf52240a1f6526c5596f3fbc23'
NATIVE_CLAIMS = {'unit-a': [{'application_deadline_monotonic_us': None,
             'child': {'application_expires_epoch_us': 1792095751867318,
                       'campaign_id': '3be215ef5c2e613c2809ebecf9676782',
                       'child_digest': 'b19f7d841f05d3f2f3f31b25b8df0ea80a3b89281b35fdb5b65c4747db021eca',
                       'cipher_frame_limit': 512,
                       'format': 'MR1.request',
                       'full_caps': {'estimated_airtime_us': 60000000,
                                     'ip_frames': 0,
                                     'radio_attempts': 256,
                                     'rx_stream_bytes': 0,
                                     'tx_stream_bytes': 0},
                       'grant_digest': '9dc8d3a92bcedc274619312f58873a9c29699d0869af677e802aa55319031a29',
                       'host_boot_id': '34b27358-8e12-44e7-a39f-410af5dbc491',
                       'object': {'bytes': 74,
                                  'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                       'plan_sha256': '4fe44b885811ebb1090e6ccfd4a165eb9a92da23d1f4442354fa6602350c72a7',
                       'receiver': 'unit-c',
                       'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                       'run_id': '040c3aa24a6cca01a9caceae7c18edc3',
                       'secret_sha256': '3d33164c2817bed70eb578d78bf27ac65bb768c48115f91602c77c7d4731d075',
                       'sender': 'unit-a',
                       'sends': True,
                       'transport': 'lora',
                       'unit': 'unit-a'},
             'controller_allocation_id': 'dd8fa294271bfabc3a6f65acf20d75d7d73811bf52240a1f6526c5596f3fbc23',
             'native_claim_id': 'b18e2096f5ac0021750fe78effea357da376bd9e3ba203f6756e301b339a3ca6',
             'native_plan_sha256': None,
             'prepared': False,
             'prepared_row_sha256': None,
             'resource_id': 'd1265de3a470e8acc8a6839b3a966c825ca19daf64fbbdc8ebb04799388d3645',
             'row_sha256': '58429fb02f9cf2bc46abbb1a5733417739e74f4864639ec434120cf53f6d66fb',
             'start_row_sha256': None,
             'started': False}],
 'unit-b': [],
 'unit-c': [{'application_deadline_monotonic_us': None,
             'child': {'application_expires_epoch_us': 1792095751867318,
                       'campaign_id': '3be215ef5c2e613c2809ebecf9676782',
                       'child_digest': 'c548b35c2eba17491dd3e6cf265a6ac44a0009a0fb606da9c3ff825d43d25283',
                       'cipher_frame_limit': 512,
                       'format': 'MR1.request',
                       'full_caps': {'estimated_airtime_us': 60000000,
                                     'ip_frames': 0,
                                     'radio_attempts': 256,
                                     'rx_stream_bytes': 0,
                                     'tx_stream_bytes': 0},
                       'grant_digest': '9dc8d3a92bcedc274619312f58873a9c29699d0869af677e802aa55319031a29',
                       'host_boot_id': 'a4a3fe67-7897-4d69-bb94-a43cdda352f4',
                       'object': {'bytes': 74,
                                  'sha256': '799b34146a819f8bf667859fa4435c9a92a12b71d27368a6f2664583196c1353'},
                       'plan_sha256': 'ec89f5b5b11645ce365737897ed14b66954e194dacc8a53e0b9e72344f3b02b2',
                       'receiver': 'unit-c',
                       'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
                       'run_id': '040c3aa24a6cca01a9caceae7c18edc3',
                       'secret_sha256': '3d33164c2817bed70eb578d78bf27ac65bb768c48115f91602c77c7d4731d075',
                       'sender': 'unit-a',
                       'sends': False,
                       'transport': 'lora',
                       'unit': 'unit-c'},
             'controller_allocation_id': 'dd8fa294271bfabc3a6f65acf20d75d7d73811bf52240a1f6526c5596f3fbc23',
             'native_claim_id': 'e349b8427ff279701b6d38c3fde3272cad3e1efcacbb42fb827888413cd27df3',
             'native_plan_sha256': None,
             'prepared': False,
             'prepared_row_sha256': None,
             'resource_id': '682aadba2a1d626b476224fb01714ce3361542c289552f00b5dee7270328eed7',
             'row_sha256': '91c503dfeb20b78a0122d08400abdba69589180d103ea34c433f603821f3f288',
             'start_row_sha256': None,
             'started': False}]}
NATIVE_RESOURCES = {'unit-a': [{'application_deadline_monotonic_us': 555104574946,
             'application_expires_epoch_us': 1792095751867318,
             'original_epoch_us': 1792095580811824,
             'original_monotonic_us': 554933519452,
             'resource_id': 'd1265de3a470e8acc8a6839b3a966c825ca19daf64fbbdc8ebb04799388d3645',
             'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
             'row_sha256': 'ccfe2f22ea1451b8472fbc98560febec6dbe1260f4143a453a869e32d65416e9'}],
 'unit-b': [],
 'unit-c': [{'application_deadline_monotonic_us': 51447336625,
             'application_expires_epoch_us': 1792095751867318,
             'original_epoch_us': 1792095581326071,
             'original_monotonic_us': 51276795378,
             'resource_id': '682aadba2a1d626b476224fb01714ce3361542c289552f00b5dee7270328eed7',
             'resource_sha256': 'df7ebc347a7c26142f9fd1d16439aa39c1e518bc5f9be7415cf646c73cb69ff6',
             'row_sha256': 'b477a9d1e6e5ced8c268de949e3361a5697175a1d0582401073e356a3195f45d'}]}
DIRECTORY_NAMES = {'unit-a': {'parents/1a02ac26e525311caa32e180596d7160': ['capture.json',
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
            'parents/1a02ac26e525311caa32e180596d7160/prepared-identities': []},
 'unit-b': {'parents/1a02ac26e525311caa32e180596d7160': ['capture.json',
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
            'parents/1a02ac26e525311caa32e180596d7160/prepared-identities': []},
 'unit-c': {'parents/1a02ac26e525311caa32e180596d7160': ['capture.json',
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
            'parents/1a02ac26e525311caa32e180596d7160/prepared-identities': []}}
DIRECTORY_IDENTITIES = {'unit-a': {'parents/1a02ac26e525311caa32e180596d7160': [66307, 6415109, 16832, 0],
            'parents/1a02ac26e525311caa32e180596d7160/prepared-identities': [66307, 6415110, 16832, 0]},
 'unit-b': {'parents/1a02ac26e525311caa32e180596d7160': [66307, 3779179, 16832, 0],
            'parents/1a02ac26e525311caa32e180596d7160/prepared-identities': [66307, 3779180, 16832, 0]},
 'unit-c': {'parents/1a02ac26e525311caa32e180596d7160': [66305, 11030305, 16832, 0],
            'parents/1a02ac26e525311caa32e180596d7160/prepared-identities': [66305, 11030307, 16832, 0]}}
OPAQUE_LEAF_STATS = {'unit-a': {}, 'unit-b': {}, 'unit-c': {}}
NATIVE_PROJECTION_SHA256 = {'unit-a': '9d9752610aeb8badb8af9605de5a76a991d1286bdf11ef6dc909dfa1529b0222',
 'unit-b': '25cc0cb89096ca0664c61350f6043f4c72592260477e9d920bff3d23da7d3249',
 'unit-c': '8d4459eb12c2923190d7f34942f7ba0983b500ddd902c0d43a0c22fc5d46596d'}
H48_HISTORY_SHA256 = {'unit-a': 'a96f0b5cd2fc4dab2336e0fdda90e2975b6eef21fec49c935e1735e1440c89ea',
 'unit-b': 'dccbfd24a1693db5701e4b1c3deba6fc91dab197361af5e6d56090adb9dbfa17',
 'unit-c': 'd821ea251805260c2705030b4a9ef0ba8152ddcb6255c703da7cd2ddc7348bf4'}
PROJECTION = ('status', 'scoped_rows_complete', 'pending_unknown', 'policy_row_sha256', 'prior_lineage', 'detail', 'files')
H48_PREFIX = previous.H47_PREFIX+(previous.PARENT,)
OWNER_SCHEMA = previous.OWNER_SCHEMA
KIND = previous.KIND
STAT_FIELDS = ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def declared_partition(policy):
    require(policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and digest(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==KIND,
        '1a02_original_policy_changed')
    for local in policy.units:
        require(local.prior_debt.plus(Cost()if local.unit=='unit-b'else Cost(1024,240000000))==local.limits,
            '1a02_whole_parent_reservation_changed')
    return True


def owner_view(record,policy,*,unit):
    declared_partition(policy)
    require(unit in OWNER_SHA256 and type(record)is dict and digest(record)==OWNER_SHA256[unit],
        '1a02_raw_experiment_owner_changed')
    require(record['schema']==OWNER_SCHEMA and record['policy_digest']==POLICY_DIGEST
        and record['qualification_kind']==KIND and record['comparison_scope_sha256']==ORIGINAL_SCOPE_SHA256
        and record['history_receipt_sha256']==ORIGINAL_HISTORY_RECEIPT_SHA256
        and digest(record['lora_mcu_artifact'])==ORIGINAL_MCU_ARTIFACT_SHA256
        and record['runtime_sha256']==ORIGINAL_RUNTIME_SHA256 and record['manifest_sha256']==ORIGINAL_MANIFEST_SHA256
        and record['actual_cost']is None and record['complete_global_debt']is False
        and all(record[k]is False for k in ('alpha_qualified','full_source_green','private_production_enabled',
            'physical_qualification','rf_qualified')),'1a02_historical_experiment_binding_changed')
    return record


def endpoint_rows(unit):
    """No run namespace was materialized; native claims remain separately paid."""
    require(unit in OWNER_SHA256,'1a02_unit_changed')
    return []


def row_for(unit,policy,owner,prior):
    owner_view(owner,policy,unit=unit);local=policy.unit(unit)
    require(type(prior)is dict and digest(prior)==PRIOR_SHA256[unit]
        and prior['previous_parent_ids']==list(H48_PREFIX) and prior['full_caps']==asdict(local.prior_debt)
        and prior['actual_cost']is None and prior['complete_global_debt']is False and prior['refunds']==0,
        '1a02_original_prior_changed')
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
        management_incomplete=dict(schema='meshticulum.retained-public-comparison-management-incomplete.v1',
            unit=unit,known_run_scopes=[],claimed_request_run_scopes=[]if unit=='unit-b'else[RUN_ID],
            actual_claim_resource_prepared_start_counts=NATIVE_COUNTS[unit],
            prepared_identity_count=len(DIRECTORY_NAMES[unit]['parents/'+PARENT+'/prepared-identities']),
            actual_cost=None,no_refund=True,physical_quiescence_observed=False,callback_terminal_proved=False,
            raw_intake_complete_sha256=RAW_INTAKE_COMPLETE_SHA256,raw_unit_sha256=RAW_UNIT_SHA256[unit],
            original_result_sha256=FINAL_RESULT_SHA256,original_operation_sha256=ORIGINAL_OPERATION_SHA256,
            original_window_expiry_sha256=ORIGINAL_WINDOW_EXPIRY_SHA256))


def qualify_row(unit,policy,value):
    require(type(value)is dict and canonical(value)==canonical(row_for(unit,policy,value['owner'],value['prior_metadata'])),
        '1a02_failed_endpoint_or_whole_paid_debt_changed')
    return value['paid_caps']


def require_final_bindings():
    require(type(NEXT_HISTORY_RECEIPT_SHA256)is str and re.fullmatch('[0-9a-f]{64}',NEXT_HISTORY_RECEIPT_SHA256),
        'pending_1a02_H49_history_receipt')
    return True


def verify_public_bytes(unit,policy,value,raws,previous_history,native):
    """Bind actual public files and real native rows; opaque bytes are excluded."""
    qualify_row(unit,policy,value);owner=owner_view(value['owner'],policy,unit=unit)
    expected=PUBLIC_FILES[unit];prefix='parents/'+PARENT+'/'
    require(set(raws)==set(expected) and all(type(raws[k])is bytes and len(raws[k])==pin['bytes']
        and hashlib.sha256(raws[k]).hexdigest()==pin['sha256']for k,pin in expected.items()),
        '1a02_actual_public_bytes_changed')
    require(raws[prefix+'policy.json']==canonical(value['policy'])
        and raws[prefix+'owner.json']==canonical(value['owner'])
        and raws[prefix+'prior.json']==canonical(value['prior_metadata']),'1a02_original_parent_input_changed')
    capture=json.loads(capture_storage.decode(raws[prefix+'capture.json']))
    require(capture['schema']=='meshticulum.private-resume-actual-capture.v1'
        and capture['parent_id']==PARENT and capture['unit']==unit and capture['actual_history_verified']is True
        and capture['global_debt_complete']is False and capture['original_unknown_actual_cost']is None and capture['refunds']==0
        and digest(capture['history'])==H48_HISTORY_SHA256[unit]
        and canonical(capture['history'])==canonical(previous_history)
        and tuple(v['policy']['parent_id']for v in capture['history']['previous'])==H48_PREFIX
        and capture['native_phase']==owner['native_phase'][unit]
        and capture['history_binding_sha256']==owner['history_binding_sha256'],'1a02_original_capture_or_H48_prefix_changed')
    require(native['role']=='native'and native['unit']==unit and native['parent_id']==PARENT
        and native['policy_digest']==policy.digest and native['status']=='scoped_rows_observed'
        and native['scoped_rows_complete']is native['policy_expired_at_observation']is True
        and native['pending_unknown']is native['database_writes']is False
        and native['expected_database_sha256']==value['database_sha256']
        and digest({k:native[k]for k in PROJECTION})==NATIVE_PROJECTION_SHA256[unit],
        '1a02_actual_native_rows_changed')
    detail=native['detail'];observed={k:detail[k]for k in NATIVE_COUNTS[unit]}
    require(observed==NATIVE_COUNTS[unit]and all(type(v)is int for v in observed.values())
        and detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']
        and detail['limit']==value['paid_caps'],'1a02_actual_counts_or_unsummed_debt_changed')
    require(detail['claims']==NATIVE_CLAIMS[unit] and detail['resources']==NATIVE_RESOURCES[unit]
        and detail['approval_bytes_match_enrollment']is True
        and detail['underlying_native_plan_correspondence_verified']is False,
        '1a02_actual_unprepared_claim_projection_changed')
    if unit!='unit-b':
        claim=detail['claims'][0];resource=detail['resources'][0];child=claim['child']
        require(claim['prepared']is claim['started']is False
            and claim['application_deadline_monotonic_us']is claim['native_plan_sha256']is None
            and claim['prepared_row_sha256']is claim['start_row_sha256']is None
            and claim['controller_allocation_id']==CONTROLLER_ALLOCATION_ID
            and claim['resource_id']==resource['resource_id']
            and child['resource_sha256']==resource['resource_sha256']
            and child['application_expires_epoch_us']==resource['application_expires_epoch_us']
            and child['unit']==unit and child['host_boot_id']==policy.unit(unit).host_boot_id
            and child['run_id']==RUN_ID and child['sender']=='unit-a' and child['receiver']=='unit-c'
            and child['transport']=='lora' and child['format']=='MR1.request'
            and child['sends']is(unit=='unit-a') and child['full_caps']==asdict(Cost(256,60000000)),
            '1a02_claim_is_not_prepared_or_started')
    require(DIRECTORY_NAMES[unit]['parents/'+PARENT+'/prepared-identities']==[]
        and not any(k.startswith('runs/')for k in raws),'1a02_unmaterialized_run_namespace_changed')
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
            and (now_boot!=policy.unit(unit).host_boot_id or sample[1]>end),'1a02_original_clocks_not_expired')
        return sample
    def names():
        for relative,expected in DIRECTORY_NAMES[unit].items():
            current();path=state/relative
            require(_names(reader,path)==expected and reader.directories[path]==DIRECTORY_IDENTITIES[unit][relative],
                '1a02_actual_directory_or_identity_changed')
        for relative,expected in OPAQUE_LEAF_STATS[unit].items():
            path=state/relative;reader.directory(path.parent)
            info=os.stat(path.name,dir_fd=reader._nodes[path.parent][0],follow_symlinks=False)
            actual=[getattr(info,k)for k in STAT_FIELDS]
            require(actual==expected,'1a02_opaque_leaf_stat_changed')
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
        '1a02_controller_allocation_or_expiry_changed')
    require(len(detail['units'])==3 and {v['unit']for v in detail['units']}==set(OWNER_SHA256)
        and all(canonical(v['limit'])==canonical(asdict(policy.unit(v['unit']).limits))
            and canonical(v['prior_plus_prepaid_caps'])==canonical(asdict(policy.unit(v['unit']).prior_debt.plus(Cost()if v['unit']=='unit-b'else Cost(256,60000000))))
            and canonical(v['full_prepaid_caps'])==canonical(asdict(Cost()if v['unit']=='unit-b'else Cost(256,60000000)))
            for v in detail['units']),'1a02_controller_unsummed_whole_reservation_changed')
    return value['paid_caps']
