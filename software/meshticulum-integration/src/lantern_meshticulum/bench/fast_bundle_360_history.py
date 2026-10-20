"""Completed3605 retention from actual H46 expired evidence; never live authority."""
import hashlib
import sys
from functools import partial
from meshticulum.bench.wire import require
from . import gfsk250_mb1_partial_history as shared,fast_bundle_complete_history as complete
from . import fast_bundle_d1fd_history as d1fd,fast_bundle_unstarted_history as unstarted
from .mcu_artifact import decode_owner
from .pilot_parent_caps import canonical
from .profiles import GFSK250_FAST_BUNDLE_KIND as QUALIFICATION_KIND

PARENT='360514104bf80fa187276f24d6aa9d9c'
POLICY_DIGEST='5f460bad40ea0b900ac87f26c366c5aa3764723b0050bdeb0580d04eae7764f7'
POLICY_SHA256='7bdf251b4b7a9b1fd06e76c6f38e82dbd980a08729a9adc48a9fcdb3d9645ae1'
OWNER_SHA256={'unit-a': 'a080c34564e109f9ae99c73d8ce19bdce090042535688ad24ab3eb2015fec238', 'unit-b': 'e491664e33d776ca6369f91c6a5eac5e56b1d04330f1ce1501bdbe176e662272', 'unit-c': '702a59b9e7d025b6f332b292c1f3970d00f49e3fcd3fa12bd3fa4b094053676c'}
RAW_OWNER_SHA256={'unit-a': '76b16e7a62b97a16162949a3cdff5c09dc11e6cc867b772d76c710f74514e549', 'unit-b': 'd5bd7ca79385b33e5d78158c01cf6a3f1bdd4edb44297447c81fef54a3b7dfec', 'unit-c': 'b69d4692999c0fd0299daf52d2f37a23dae1b425ad8c4beb7ff448ba065ef733'}
ORIGINAL_MCU_ARTIFACT_SHA256='9e478abcb07234a2f42ed70966b3ac4d82c303bafd8e20a8c5b08eaf681a1f57'
ORIGINAL_HISTORY_RECEIPT_SHA256='00d22791b573cfa525e105a8ee69d41f80fb134790ceff0d599c9fe425be822a'
ORIGINAL_SCOPE_SHA256='61506fa5bb43deed513da9ddc3032d3a8362241a02947ff6dbe9e83fed5f438e'
ORIGINAL_RUNTIME_SHA256='171800a1ea00ecf45830889beac5a2229e11d0a902cf621c3da0b90ba3ac631a'
ORIGINAL_MANIFEST_SHA256='7015f4b775db7c8bcb16577e1eefd1953f594421a6b37c53842dd0c84747ceb7'
ORIGINAL_PHASE_END=1792036437075173
ORIGINAL_ABSOLUTE_END=1792036550178856
FINAL_RESULT_SHA256='4cf9b6b70fa2854ccc18b7637008c0986b1826b0c67744e86b4ea8ea762e0236'
EXPIRY_COMPLETION_SHA256='6491801233dbdc7197d9ca1982223dd88ed7ddeeb5b3aff892e05dafd0e6a039'
NEXT_HISTORY_RECEIPT_SHA256='cf01a817a18458f0ca2b91dca012afcc29044a9c65bb426ee9316e003cb32826'
NATIVE_DATABASE_SHA256={'unit-a': '6b9907e45eb0335c38b31ae278f4eb8065bb1d04acb41b981c59cf653647264d', 'unit-b': 'b38f381408d1307c6b99e15db064326e7796d5099afb9393523e2afdcae50e2c', 'unit-c': 'd362b089fe0665fedb902080705345c98544ecb1ed9ff0c48ae22c989ccc16a3'}
NATIVE_OUTCOME_SHA256={'unit-a': {'03b4d3e0d90524a90fe259e1027afbc2': '5fe03d20f0d56003dc8c8fd00c26ab2c8a4714d98253514207d271f33287cb68', '0e34a16e41c4fb3a7f1bb1049cac4238': 'e9992582abaa5f8c11c1d170adfe76ec667907e8e92bda1593dec036204c41bd', '2ca096c0c7d98fb9f8d93a739f793545': '4517f8ca8794ee2b1401eea8fece6d6ce01bd0d6da46db5ec2ec11f5ec6b7a27', '491aa9491dd854c384247f6c0304a5e5': 'ae6bbe7d057a51bf0c5fc7cc483d8d99ac69629ece18633fb5f9dfd53635b7c5', '4948d379ecf8b0261bf7a8d17819958f': '323b4174d55b8abc4ae4d1872973f6c628078bccf087b683573555806d089a0b', '5b7922c96150b503bba83da9ae973eef': '96ece9e2627211e4cba38cc24ceca12dcc0c6a55f928907671b74868dd48b839', '62c7cef698223a2fcf654401a4266b3c': 'ad8c33e1bf7d392b52a2ceaa466f78bd27d09a51a69e842b968aa047ecbd6418', 'abb596324f4ed292467dce486340460a': 'e5dfcb27e6af349e30de13a8b426929fbf0c7d9520885c41355fba0151858a6f', 'b9fce0c88cba47c798ba4f99a56f2046': '9b470e76395ad0ee6ca08b4cb812d19beeaa44e24fb3c39edc90280df1e4923f', 'dcd4803ff8a5aab7c52d8676449d5abe': 'b6b5705c4626aca8278237f1206ee3d4ed993a1bb09918ea45b07f0e2cabcc77'}, 'unit-b': {}, 'unit-c': {'03b4d3e0d90524a90fe259e1027afbc2': 'eac060b01fee5e30996457948933b266115372105d4a1ab3d4b45760d4bf4dd6', '0e34a16e41c4fb3a7f1bb1049cac4238': '40212e42eb2ba06bfe5afbcb322273fc76cfe3d94162206b2e8c1f8eaec4800b', '2ca096c0c7d98fb9f8d93a739f793545': '4ffe6a5b79643a2bd64098ae8cec31486c813e633fac9ee403d532d58d3fdef3', '491aa9491dd854c384247f6c0304a5e5': '26aace6b362dae7f8d53a413e0801f37ac3d60c1fb9bbd4c22546a98bd8b28b3', '4948d379ecf8b0261bf7a8d17819958f': '725b7303f647f2da4c0e0f4e271c9d3b46bb0ff9f680792c593cdf6646cba7d1', '5b7922c96150b503bba83da9ae973eef': '45d6fd84de3abc9151db5fb20fe50c0b7979785d7fd7e36b117f98f34e09a32f', '62c7cef698223a2fcf654401a4266b3c': '55cdcefa33930f9d84f64c85ac679bfa493e71a15fae2a16c727bc826e5bdf2b', 'abb596324f4ed292467dce486340460a': '3ee415f08a5e20d9c544bc027b8e20a146c0f12ca58b7c8e508c33528f78e20b', 'b9fce0c88cba47c798ba4f99a56f2046': '28e61158107073ddfb6dd669a7d508645ab1905a50ef6c29258bfd8d6053b2a8', 'dcd4803ff8a5aab7c52d8676449d5abe': '3ae5279e7f8e4fd0f8aebaedcb41e294487cbe71a5949beb95e9199da0581cce'}}
FAST_STAGE_SHA256={'unit-a': {'fast-stage-0.json': '6ae167d081c8884a0bdec60b26f78cc8cff0031d4f5067ef2c859d380fa88d0b', 'fast-stage-1.json': 'e08d50d67136b28f20fa7969ef95f77677fd793da21d3dc6d8f42def8ed5c668', 'fast-stage-2.json': '29ddbda04a76b311ad27ae40091e8b84aa9d00bc40c70cd48b22f3f3e4620e68', 'fast-stage-3.json': '6c626f8ee06dae306aa8e2d19900f942966de1574f52155d157fde5b9d6f9824', 'fast-stage-4.json': '6be873aa7916cd62f8b2cc318b4b59c0338b630caf91bc9ba390014072efdbe4'}, 'unit-b': {}, 'unit-c': {'fast-stage-0.json': '9ca32aaebb36579363b6d1d200aa8bd60cb90bb050a6065e5a41c221ea3178f2', 'fast-stage-1.json': 'd349352747ab3349bafc7641034fd65338f6e2846a56b0aa2967f3ce0db3ddf0', 'fast-stage-2.json': '5fe8defa4ce2e8284b1d954c067ae245ed3fd3844e99e73cbaf9652f289bfb5c', 'fast-stage-3.json': '5a24b6c0823168222dee379140038c8ed6fa1e0595cc7818a6cef6cdfe1a2e3d', 'fast-stage-4.json': 'a05147ff70966f035e32f40445409adc84f659a9cb61ca596a3e600cbc5bcec2'}}
FINAL_ACK_SHA256={'unit-a': {'03b4d3e0d90524a90fe259e1027afbc2': '8f78fcff9013b8622af44a150d85ad9da8917cdaacd0e6604fd7bf9e6c732451', '0e34a16e41c4fb3a7f1bb1049cac4238': '384e0a64dfa085c314593581ef09662988d0c2dc92db7f986ffd3ea86bdba74e', '5b7922c96150b503bba83da9ae973eef': '53ea0bea664ed1c19b749469efccf51f9371ec51b334a67389933aad84525aec', '62c7cef698223a2fcf654401a4266b3c': '1ca9ff0b4a4a09532ad5eb62553518b3fe2358c7a88f4217e35a5c543da69b46', 'b9fce0c88cba47c798ba4f99a56f2046': 'b3c55c376f4a9e4be09d06435aa1bef84d823517df7eecef3def1a9261935914'}, 'unit-b': {}, 'unit-c': {'2ca096c0c7d98fb9f8d93a739f793545': 'c81eb2eae68e6409c17ad12bc72f3521489804b0b5faf56260ebf444bd282f2f', '491aa9491dd854c384247f6c0304a5e5': 'd06aec8ac7690ba9c5068aae8c5aaec7392b03c01c6f1f739d5ef2eac15be6b8', '4948d379ecf8b0261bf7a8d17819958f': '7f940683168471b8c0f29a7d0fcf6633e28dd905722cc2e43b7531b0801a4da2', 'abb596324f4ed292467dce486340460a': 'cc8d1a7868d28109a581670408734de1d52175f3aabbe92cfe516189203d04cc', 'dcd4803ff8a5aab7c52d8676449d5abe': 'de1b66b73fa6f80afbd5b7d773d0e34c597433aa01280ec2e9de7e175e55de27'}}
VERIFIED_PIECE_SHA256={'unit-a': {'03b4d3e0d90524a90fe259e1027afbc2': '62c746f3663d3714f9d5d7fbaf3842b0e024172e7587b71849a9d83ecd12f823', '0e34a16e41c4fb3a7f1bb1049cac4238': 'b5d4e1769eabce89c6e22aba5b4acc263b7ea5c82f2df18c7915b45d89bc9d61', '5b7922c96150b503bba83da9ae973eef': '8834720eb0a98dbfd4d613ab950c35f2b9aa99b9082ecd15dc864b90bc29d90c', '62c7cef698223a2fcf654401a4266b3c': 'd94b0df6382e2cb6fe5ac1bfa28b90478fda6ab6e7a5b8b9de7c657e3aaf4d7c', 'b9fce0c88cba47c798ba4f99a56f2046': 'c0dff849b4bb6bf2314abd371c7a1939289ea6f40d2cbe9fc429326c70d3bfc5'}, 'unit-b': {}, 'unit-c': {}}
STAGE_APPLICATION_ENDS={'unit-a': (1792035491278016, 1792035599424733, 1792035708377428, 1792035819582719, 1792035933147815), 'unit-b': (), 'unit-c': (1792035491278016, 1792035599424733, 1792035708377428, 1792035819582719, 1792035933147815)}
STAGE_NATIVE_DEADLINES={'unit-a': (494843985646, 494952132363, 495061085058, 495172290349, 495285855446), 'unit-b': (), 'unit-c': (491783997735, 491892144452, 492001097148, 492112302439, 492225867535)}
ORIGINAL_RUN_COMPLETE=True
ORIGINAL_FINAL_AUDIT_TIMEOUT=None
WHOLE_BYTES=100000
WHOLE_SHA256='2aaa9db69e9f2e9f180ad194c19883cf6969e660f023bdfb456487f885ae6435'
FAILED_STARTED_RUNS={'unit-a': [], 'unit-b': [], 'unit-c': []}

H45_PREFIX=d1fd.H43_PREFIX+(d1fd.PARENT,unstarted.UNMATERIALIZED_PARENT)
declared_partition=partial(shared._declared_partition,sys.modules[__name__])


def owner_view(record,policy,*,unit):
    """Exact original v2 file pin, decoded only into a local historical semantic view."""
    declared_partition(policy)
    require(unit in RAW_OWNER_SHA256 and type(record)is dict
        and hashlib.sha256(canonical(record)).hexdigest()==RAW_OWNER_SHA256[unit],'3605_raw_owner_changed')
    owner,selected=decode_owner(record)
    require(hashlib.sha256(canonical(selected)).hexdigest()==ORIGINAL_MCU_ARTIFACT_SHA256
        and owner['history_receipt_sha256']==ORIGINAL_HISTORY_RECEIPT_SHA256,'3605_original_artifact_or_history_changed')
    shared._original_context(sys.modules[__name__],owner,policy,unit=unit)
    return owner


def original_context(record,policy,*,unit):
    return shared._original_context(sys.modules[__name__],owner_view(record,policy,unit=unit),policy,unit=unit)


def retained_context(record,policy,*,unit,observation):
    return shared._retained_context(sys.modules[__name__],owner_view(record,policy,unit=unit),policy,unit=unit,observation=observation)


def require_final_bindings():
    return complete._require_delivery_bindings(sys.modules[__name__],original_run_complete=True,original_final_audit_timeout=None)


def pending_next_roster():
    from .retained_capture import PAID_HISTORY
    require_final_bindings()
    require(len(PAID_HISTORY)>=46 and PAID_HISTORY[:45]==H45_PREFIX and PAID_HISTORY[45]==PARENT,'3605_H45_prefix_changed')
    return PAID_HISTORY[:46]


def metadata_files(reader,state,unit,value):
    return complete._metadata_files(sys.modules[__name__],reader,state,unit,value)
