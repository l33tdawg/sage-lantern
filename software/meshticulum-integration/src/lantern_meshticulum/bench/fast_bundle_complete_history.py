"""H42: exact 60d9 delivery with its original final-accounting failure retained."""
import hashlib,os,sys
from functools import partial
from meshticulum.bench.wire import require
from . import gfsk250_mb1_partial_history as shared,fast_bundle_partial_history as previous
from .profiles import GFSK250_FAST_BUNDLE_KIND as QUALIFICATION_KIND

PARENT='60d9ca2aa760cb4460d0af637101c997'
POLICY_DIGEST='ae183aeb153cc1c59b60b3641027db5aac3267e6a6813ac610b2f6286b2a77f0'
POLICY_SHA256='b97c8e5e40ad0e0af07b000550975bc49a3f86d82acf851f4185ca82afcbbf9f'
OWNER_SHA256={'unit-a': '118a5b93a3f40b2959bf9df6ed12250be6fcab6357ff672eacb8a0264956d569', 'unit-b': '592d34955fc1efb368b1400325037830ac1443972111cdfef0b4d4307ff97491', 'unit-c': 'd2132e3b76cae9d5aa87598bb4ba5b68adde4b921b987d8302d249fd4634a840'}
ORIGINAL_SCOPE_SHA256='ef309be69f82d6923fef5d6fadc46c98616e53e7ece8419c9fd8cba9415a461a'
ORIGINAL_RUNTIME_SHA256='2672ad58d146a79518a2fd3149867573a81587f5ba101cee6e85181e75fb7e97'
ORIGINAL_MANIFEST_SHA256='a28c03981b4a26c84332748c7ca1606755c69c37954dea8d2a9625579e4d473e'
ORIGINAL_PHASE_END=1791878909991534
ORIGINAL_ABSOLUTE_END=1791879003441739
FINAL_RESULT_SHA256='f0a397e3c857bc5913aedd88d778bb1df122bd80bf561b5218ba25a634e87ea4'
EXPIRY_COMPLETION_SHA256='7091e590e69c82f35f0c1aaa1a7acd2414e04fd392e4c07cddca6e4e2cb246fc'
NEXT_HISTORY_RECEIPT_SHA256='68f7a680452b111ba6f826ff4566dd7a25e8299d7682e7979bd095730e87f489'
NATIVE_DATABASE_SHA256={'unit-a': '9fb43d7336bcf6f8607e7cb75fa8add001f63ebe86b1583ad59e248b5f797db7', 'unit-b': 'eca7e15bca9d5ba6ce6c41d82e55e2e628bb7384b044f015cf2e596f77623399', 'unit-c': 'af5438a7e16b7fb72eb5408857182d819eea56547b9ff72df4f41be583a3c442'}
NATIVE_OUTCOME_SHA256={'unit-a': {'31e9a0aa967542269eaaa2b06dda409d': '184e8ebf79e24d6c285b0535fb14216ae9303cd30447120baea971c11e30bac2', '37c9f409ffad0b25cbf09b7f07d15dc7': '20436d560e7eb0c1f828745e0f5aa395fdf1bde5f414559cfaad01c5f882c22e', '3b23526f8270e0a9794d81c3bcb8c3dd': '5d9897a9097efcd514632c924ad8b18cd13387ef8ef39d0df494350e0661d905', '50c42fa9665e6dfb8c89616ee4fadc43': '5e0942c4c1a7592007faae0ab488e09b556f09e0cdf12a80c75873c35586177b', '601b50cc00b12a3b669d27a87b4a545a': '9b2b2c963122d042147d928a5019a1f575714200fe2d4117ff79596b58b4ac21', '864ad60e2a689f61dcfb7d3522b85c8a': '51d34184768bc3a2c443ef07c7f9af8f2d96806f99232211436d0ec5bb637d4b', '9a4b77d0953a612abc4d9a07ca32286c': 'a7ad930def7552372af18daf40ab96c4e3ef8ee50d4389b66a0331342f3152a6', 'acf01ff8323d811a0b67531862bacab5': '7203a3f13aa36bdfb80c34b630b5b7e8ef7e8bdc44e9ba94fbe2b528b4c687d0', 'ec7f78147b8e8ac09f12509e179267ff': '05ab31a8324fc68a70ace1709b296eb5d6ca5186ef2650e36dff1d4af35909ef', 'f8793aa96ea92a7013d1bb8bca5c6247': 'b484914f5df46c03ef24bd0d0ecc8bcdab57a32b5d73ca6af53b48402502aa7d'}, 'unit-b': {}, 'unit-c': {'31e9a0aa967542269eaaa2b06dda409d': 'c570d4f0e54883db5abf659fbf9f710e3ab2ee8c37cd0bef119d7936a0de3163', '37c9f409ffad0b25cbf09b7f07d15dc7': '0d5fc00e95c635d8c06e8284b467e59061f3aa9377890029a07efa6b94ccfe69', '3b23526f8270e0a9794d81c3bcb8c3dd': '54b24a56e92d8162e83d3e995a9e25a26eb85f4746726b8caaef5665365f80f1', '50c42fa9665e6dfb8c89616ee4fadc43': '90c4f656754d2d3f49a2beef17a887c23d76ccc98cf05fc22749279d89dc3209', '601b50cc00b12a3b669d27a87b4a545a': '1ff8df708e2985e9302523aee821bb6311014ec86a29059a0e5ef808065dc639', '864ad60e2a689f61dcfb7d3522b85c8a': '7f34014cd8305356c53bfc6b39e8816636c16dce3cc3f71b12b21332943ae39c', '9a4b77d0953a612abc4d9a07ca32286c': '4e89f8dcd04f173115d53252ab1fc939fcf96d024a96cbf68fd72e3dc6d9518e', 'acf01ff8323d811a0b67531862bacab5': '19e464f4da7dbb0569b801611b3e80d806f27b17a6bf4d26099179a700f2649b', 'ec7f78147b8e8ac09f12509e179267ff': '8099f22aee0ed5d5d70ba79bf79c33637e2f37c6dc0ee334cca41c7898fbff04', 'f8793aa96ea92a7013d1bb8bca5c6247': '4656647f85dde96244dbc971601a288961ed94196ea5277e6dc5e04f669531d0'}}
FAST_STAGE_SHA256={'unit-a': {'fast-stage-0.json': '4d2e9c348cc501466efc12a68469a4638342b7c124e5243c652e1e1ecc4e1474', 'fast-stage-1.json': 'd9c43da10def3665080a2e196d8f638f1bef1d076e23db221bdd54cd40b34f72', 'fast-stage-2.json': 'f8def22d1f4666657dbb13279e6bc83150198c037525d513228142dab15ab7ce', 'fast-stage-3.json': '1dd719b62641dd83c60a7b724ae566644ae94057c9ba16bc83efa8b5ddad0819', 'fast-stage-4.json': 'dbe9587b3f8c596d40f222ee32084455ab39ee6b3452adf998d3e9695a62b534'}, 'unit-b': {}, 'unit-c': {'fast-stage-0.json': '57e12b5cf783b30248482a00a5e62eea4df88ad9296d4fb4e7e2ccf4a65d792a', 'fast-stage-1.json': '5b8b3b8ee7ebce587e1d6b4120a03705f8a3d5faeea86124dfe911f1218ff2e4', 'fast-stage-2.json': 'a0685b7b71d5ee00dd3e7beca0339f5c1348eb046d724483c7ede2b4a23c39ac', 'fast-stage-3.json': '0c88440af760bdf5827bea230a1881b6d370964107d6f313957debc39630237a', 'fast-stage-4.json': 'c3489d7f752aaa4c1e1c7af9ab56747acc7ffa63f62c00d4d91fad2300f9bd8a'}}
FINAL_ACK_SHA256={'unit-a': {'37c9f409ffad0b25cbf09b7f07d15dc7': 'be2d07e85e428ecaaaadfcd7e695d6f663db41ba0b17f576538a3450a1a5973c', '864ad60e2a689f61dcfb7d3522b85c8a': 'c0c2f94bd67548bac01295f6aaf90fcd83297082ec5a32ed27aeed98e70a94cc', '9a4b77d0953a612abc4d9a07ca32286c': '99c5b08ad90489ab05fcb9badcf6c11e4d1e483e57317236275ae4002e77e059', 'acf01ff8323d811a0b67531862bacab5': 'c27dd52d4d3fb995b08d84321c3650624fb0d98ff732788f5a369ffe3915825a', 'f8793aa96ea92a7013d1bb8bca5c6247': '3e00c2ce206515c4eef4a170f57135d7096af254e7809a365ec6046e17e97e6e'}, 'unit-b': {}, 'unit-c': {'31e9a0aa967542269eaaa2b06dda409d': '127c48b7ed18fb0d200cd45f23bbc25e82d451c8a377a370e8862664049da50d', '3b23526f8270e0a9794d81c3bcb8c3dd': 'b1abf31cccd59f78db506ba3e11efc1654fa5555e7802adc59da02bed65768f4', '50c42fa9665e6dfb8c89616ee4fadc43': '7b7d83cecaaa0cd5e5a3b760c2dd666c215f221d7ca0961d2f1341601716e0ee', '601b50cc00b12a3b669d27a87b4a545a': 'f35949c3b3dcb2581fc965873b08132935bf0a5084f3e27c259bdd81f74d2a46', 'ec7f78147b8e8ac09f12509e179267ff': 'a1a1f095a36a5794d2c42c114da4039a38512387be4f12504c189607b001434e'}}
VERIFIED_PIECE_SHA256={'unit-a': {'37c9f409ffad0b25cbf09b7f07d15dc7': '1e9d719993ff7d48fa1f861f1c469c84fa1460e0dd81684ed347e496371ad455', '864ad60e2a689f61dcfb7d3522b85c8a': '920ba0ad5edb6773e5aab23f0f19ff4db8f13fef860f6d8b3bc351bb2bfc0bf3', '9a4b77d0953a612abc4d9a07ca32286c': 'b3393adc13a52a5a9a5b026757c081daae2fd8305a74588c46c2ebce8dc95303', 'acf01ff8323d811a0b67531862bacab5': 'ef00c77b3995787065d8fb7bc3a515c823ebee6f1dfd6a4a75acb253d63c7912', 'f8793aa96ea92a7013d1bb8bca5c6247': '95c0164216ad5906edab6d6c97b1e13fa47dc790806d32fe3979c108a0b58fa5'}, 'unit-b': {}, 'unit-c': {}}
STAGE_APPLICATION_ENDS={'unit-a': (1791877934734941, 1791878035187620, 1791878137893013, 1791878247465680, 1791878358156315), 'unit-b': (), 'unit-c': (1791877934734941, 1791878035187620, 1791878137893013, 1791878247465680, 1791878358156315)}
STAGE_NATIVE_DEADLINES={'unit-a': (337287442571, 337387895250, 337490600643, 337600173310, 337710863945), 'unit-b': (), 'unit-c': (334227454662, 334327907338, 334430612735, 334540185401, 334650876035)}
ORIGINAL_RUN_COMPLETE=False
ORIGINAL_FINAL_AUDIT_TIMEOUT=124
WHOLE_BYTES=100000
WHOLE_SHA256='2aaa9db69e9f2e9f180ad194c19883cf6969e660f023bdfb456487f885ae6435'
FAILED_STARTED_RUNS={'unit-a': [], 'unit-b': [], 'unit-c': []}
H41_PREFIX=previous.H40_PREFIX+(previous.PARENT,)
declared_partition=partial(shared._declared_partition,sys.modules[__name__])
original_context=partial(shared._original_context,sys.modules[__name__])
retained_context=partial(shared._retained_context,sys.modules[__name__])


def _require_delivery_bindings(c, *, original_run_complete, original_final_audit_timeout):
    shared._require_final_bindings(c)
    def pin(v):return type(v) is str and len(v)==64 and all(c in '0123456789abcdef' for c in v)
    units={'unit-a','unit-b','unit-c'}
    require(c.ORIGINAL_RUN_COMPLETE is original_run_complete and c.ORIGINAL_FINAL_AUDIT_TIMEOUT==original_final_audit_timeout and c.WHOLE_BYTES==100000 and pin(c.WHOLE_SHA256)
        and all(set(table)==units for table in (c.FAST_STAGE_SHA256,c.FINAL_ACK_SHA256,c.VERIFIED_PIECE_SHA256,c.STAGE_APPLICATION_ENDS,c.STAGE_NATIVE_DEADLINES)),
        f'pending_{c.PARENT[:4]}_delivery_and_original_result')
    for unit in units:
        count=0 if unit=='unit-b' else 5
        outcomes=c.NATIVE_OUTCOME_SHA256[unit]
        require(len(outcomes)==2*count and type(c.FAST_STAGE_SHA256[unit]) is dict
            and set(c.FAST_STAGE_SHA256[unit])=={'fast-stage-'+str(i)+'.json' for i in range(count)}
            and type(c.FINAL_ACK_SHA256[unit]) is dict and len(c.FINAL_ACK_SHA256[unit])==count
            and type(c.VERIFIED_PIECE_SHA256[unit]) is dict and len(c.VERIFIED_PIECE_SHA256[unit])==(5 if unit=='unit-a' else 0)
            and set(c.FINAL_ACK_SHA256[unit])<=set(outcomes) and set(c.VERIFIED_PIECE_SHA256[unit])<=set(outcomes)
            and (unit!='unit-a' or set(c.VERIFIED_PIECE_SHA256[unit])==set(c.FINAL_ACK_SHA256[unit]))
            and all(pin(p) for table in (c.FAST_STAGE_SHA256,c.FINAL_ACK_SHA256,c.VERIFIED_PIECE_SHA256) for p in table[unit].values())
            and type(c.STAGE_APPLICATION_ENDS[unit]) is tuple and type(c.STAGE_NATIVE_DEADLINES[unit]) is tuple
            and len(c.STAGE_APPLICATION_ENDS[unit])==len(c.STAGE_NATIVE_DEADLINES[unit])==count
            and all(type(v) is int and v>0 for table in (c.STAGE_APPLICATION_ENDS,c.STAGE_NATIVE_DEADLINES) for v in table[unit]),
            f'pending_{c.PARENT[:4]}_native_stage_and_delivery_metadata')
    return True


def require_final_bindings():
    return _require_delivery_bindings(sys.modules[__name__],original_run_complete=False,original_final_audit_timeout=124)

def pending_next_roster():
    from .retained_capture import PAID_HISTORY
    require_final_bindings()
    require(len(PAID_HISTORY)>=42 and PAID_HISTORY[:41]==H41_PREFIX and PAID_HISTORY[41]==PARENT,'60d9_H41_prefix_changed')
    return PAID_HISTORY[:42]


def _metadata_files(c,reader,state,unit,value):
    c.require_final_bindings();parent=state/'parents'/c.PARENT;reader.directory(parent);files={}
    require(value['fast_stage_sha256']==c.FAST_STAGE_SHA256[unit]
        and {name for name in os.listdir(parent) if name.startswith('fast-stage-')}==set(c.FAST_STAGE_SHA256[unit]),f'{c.PARENT[:4]}_stage_namespace_changed')
    for name,pin in c.FAST_STAGE_SHA256[unit].items():
        raw=reader.read(parent/name,8192);require(hashlib.sha256(raw).hexdigest()==pin,f'{c.PARENT[:4]}_stage_bytes_changed')
        files[str((parent/name).relative_to(state))]=pin
    for field,pins,name in (('fast_final_ack_sha256',c.FINAL_ACK_SHA256,'fast-final-ack.json'),
                            ('verified_piece_sha256',c.VERIFIED_PIECE_SHA256,'verified-piece.json')):
        require(value[field]==pins[unit],f'{c.PARENT[:4]}_public_metadata_scope_changed')
        for run in c.NATIVE_OUTCOME_SHA256[unit]:
            path=state/'runs'/run/name
            if run not in pins[unit]:require(not os.path.lexists(path),f'{c.PARENT[:4]}_unexpected_public_metadata');continue
            raw=reader.read(path,100000);require(hashlib.sha256(raw).hexdigest()==pins[unit][run],f'{c.PARENT[:4]}_public_metadata_changed')
            files[str(path.relative_to(state))]=pins[unit][run]
    reader.check();return files


def metadata_files(reader,state,unit,value):
    return _metadata_files(sys.modules[__name__],reader,state,unit,value)
