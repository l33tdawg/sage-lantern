# SPDX-License-Identifier: Apache-2.0
"""Private, bounded public loss/resume management RPC; never a bearer grant.

Capture re-observes retained typed rows before sealing their protected metadata.
Enrollment binds those actual capture bytes before any application starts.
Guards reopen immutable history and genuine NativeCaps; they never repair a
missing prepare capture, open radio, refund debt, or authorize private services.
Returned dictionaries are comparison receipts, never in-process Snapshots.
"""
import base64
from contextlib import ExitStack
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import stat
import threading
import time

from .history_identity_seal import _Ancestry, capture, SealedHistory, MAX_FILES, MAX_DIRS
from .history_metadata_fence import _FLAGS, _FILES, _stamp, MAX_FILE
from .legacy_grant_fence import capture as legacy_capture, LegacyGrantFence
from .legacy_grant_fence import MAX_RUNS, OWNERS
from .pilot_parent_snapshot import Scope, collect, _child_ref
from .pilot_debt_snapshot import Scope as DebtScope
from .pilot_parent_caps import BUNDLE_KINDS, PUBLIC32_KINDS, ParentPolicy, Cost, canonical
from .pilot_native_caps import NativeCaps, NativeFacts
from .pilot_manage import facts as native_facts
from .resume_parent_guard import HISTORY, ResumeParentGuard, install_seals
from .retained_capture import historical_parent_ids, capture_paths, CAPTURE_PARENT, CAPTURE_ONLY_PARENTS
from .profiles import profile_for,GFSK_NAME,GFSK_KIND,GFSK_BUNDLE_KIND,GFSK_MB1_BUNDLE_KIND,profile_record,profile_from_plan,admission_for
from meshticulum.ip.wire import IpLimits
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.contracts.identity import PeerID

CONTRACT_SCHEMA = 'meshticulum.private-resume-native-rpc.v1'
STATE = Path('/var/lib/meshticulum-bench')
OLD_DIGESTS = (
    '86975afb2e94de9ae55c86269409f4d8456475499fc20995cd2bab6a4f4ed0af',
    '6d337c78faf8c2dbea7a0a0d29e82aa8af8d1452f954fb6b664106459c33d256',
    '1ca1774780928427f15befecf723064cd87aff6ad402b6ef94b137f5d45f6d33',
    '290fdcc66fb9696dd584e146f7c4e0ed271039fb51f75f430abf8bb0b9b5fdf1',
    '0d153ed60782be5028fe7b75f3345b0441e62ef95b8db629270d5b309fd0e049',
    'c4dac314abe5afac2d14c8f82f08466863bac438c7674864c0864c8eae5e2eb7',
    '617cdde5e1bb0bfb162552861abb6b84e25a389a981d16b5b17e7e5d5daaf10c',
    '83f56d8609b60532332631f86c3061c6f8b3fefa2ff7517406860e467dd7f2e8',
    'eb592d40f359afbc530aa61b4f1feb7d0c4afc74d27b1bbdd73e9ccf0f1faa01',
    '61f1f87bafdf307cd02e51ee5a17cd25d5a3728a80583dc6820dd2d9dbe9e119',
    '595d5864801330aeb4d1eb7d281599c090bbcf428602d6777a300761c442be55',
    'd9bd3b79c63843d29892d81846d7a5ebf4967eb01a676800d63b15e7d366a0b8',
    '44425235165ea6d49ce8b46760bdbc4c8519c98ece034c560566ae6dcd57bfb2',
    '85501a81327854cfa58ef52e65ab2253f17d18925d74617f7b2bb11644664c39',
    '25e8beb9049826a4dc1a5ddde3fcc239e6e7e67f4b265b85da80f0b9a22b9627',
    '1411f2db5d331a56b3d395e7b8443a516a766d69139275dd3276d7a8bfe19293',
    '5a837b0e365421ff9b9c5511ec768a16d458b16b3720af8c4aa1e6a085a8c827',
    '0de0a7d00510c4bdbe3ee6857a5130f1000576e305c8382b84bc27d40418d5d1',
    '2fd26f804a8d7d8d095e4d90584814a788a70b47ec36962c53b4d5f7239d4b8d',
    '47a8f82ef0b7ba29ba785623b870138487786c9e5a5e9371b00c84544ab5e53b',
    '75629c69abee33dbeb9e7f9f74b62a8af4fd3bdead281089ee5202e345e83464',
    'd0bd6f2d52ca5aceb8e69c9928b25bd7e3f8ab9db118dba9d4190ad05c01e9ec',
    '725159b7a2a9baa0486d462760fc68a1d58065c084a612a9c80b7e2d6838d0fe',
    'd63ff5d4096f0a68aef37f569dfe837f64d90b820b7290ea450febf901ca3468',
    '4549a36a15cf11fda34441d66dcc1ff9af0083273d5338b74c68701d0e6de572',
    '09eb8ec08d2e4c5e14091f8124fa966dfcf972887a2b62ecca481ec5d1ca5863',
    'ffd58627e5fecbf342d6d0ff2473788c56a7ea38bf640103eb585fd7fe9ddcef',
    'ce07d40fc619f16616ed464bd6f756dd137331a564bafbd8978befe676f43ed4',
    '6289467c390f4d4c505b750be783d649e5160f22b677cb20861c48d6781390a1',
    '9e199fe7646f662c10b051cfce3e3cf05278dcfb8a7ed638a5a761943fc0cb17',
    'a0b235585aed5fcd2c46d8c6399cf68d402ad449a95dc0b65d69d5682a272556',
    'e98e810d8ec1c8d007547310090867725bcc60bf282b3aa48e84d8b319820890',
    '97e3b2e0b51f7fb1b1d4c21c04d715f9dc630e6702e46eb6fd1d6f7cb6f62bef',
    'b86dde057f19d8d38f62743cb56b2d9109a5be4f90fc8d93e2ab2c6381d744eb',
)
LATEST_PAID = 'd60d8308326314a7753e651fa43a99e3'
COMPLETE_PAID = '4559277fa43913ce3935b078d6744a55'
BULK_PAID = '4034f8979b985660df21859606c4f8a7'
BULK_RECEIVER_RUN = 'b024cea0883f00b3b6978533f66c1a19'
BULK_RECEIVER_RESULT_SHA = '3144a2ab5108bd8a5363a03ca6da541cf56a150048c40753b2cd6f91ef7758ab'
BULK_OBJECT_SHA = '29aab48fe22c08588643e5d1e11f48696405697e8a59607ef34702cc2a1e0465'
MIXED_PAID = 'ba4572e95e598ad6c17fc954c3792db7'
MIXED_RECEIVER_RUN = '9dd5b8dd6466600acfdc8f1884db3da8'
MIXED_RECEIVER_RESULT_SHA = '5f594b1cba94b24883327f2457d1a79541e5cbffbe4fd073fc547ddeaabb08b6'
MIXED_OBJECT_SHA = 'd61126423d81a20aab75ada153b03c326d60fcc781884bba9f84d3d5821d134d'
SUCCESS_PAID = '1df30b47fa36b8d47328fb886528a7a9'
PARTIAL_PAID = 'f153a7a87a2803db85078df81a6a1aaf'
PARTIAL_RUN = '13ba2dddea2c9aa53e6e806a83c78b66'
PARTIAL_REFS_SHA = {'unit-a':'022830b83a534b91e5d121b178a8341ed074b48f7d5e6e009c5ea95f1019a78a',
 'unit-b':'4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
 'unit-c':'884ab23c305bf40fe3be93f7e70d01bb23dad860fde20a94b82537584a294fb4'}
ACCEPTED_BULK_PAID = '25a990a2d565c50b237ee45812a8c75f'
REVERSE_BULK_PAID = 'cb5869e6aa8de545a267007b085d4762'
SMALL_BULK_PAID = 'f6e147e70ec7ce07f673ea5e9cd2cdc2'
ZERO_RETRY_BULK_PAID = 'b8f9b5d9c5479c8d363c7b7e8014bc3b'
IMMEDIATE_RX_BULK_PAID = '806fdca055e79dfc0b86ff8f304bcdf9'
SF7_REPLICATE_PAID = '4ea23c6b8fa83366904ac62ffa98e2bf'
PUBLIC32_PAID = 'c26cd03a8fd427daf6b8d894e4b1d04c'
PUBLIC32_REQUEST = '0a947ca87b3c1c58eee35c4facdc189f'
PUBLIC32_RESPONSE = '5224795f932cffcde4580f20683340e2'
PUBLIC32_RESULT_CANONICAL = {
    'unit-a': {PUBLIC32_REQUEST:'eaf6fb995f2d7d85ec214ad8d0446275d83cb9a982d7de86e8e397284fb43ec7',
               PUBLIC32_RESPONSE:'aa3f9443ec397e261a933f9f783d7c46b7023176b4f95be540e47e3fa2864ee6'},
    'unit-b': {},
    'unit-c': {PUBLIC32_REQUEST:'52df8064aa3f826d3837c2e6ab1cfe924dcec326e1d8d5dc2735be730ca23c81',
               PUBLIC32_RESPONSE:'7020e800d9c12344225eba986cadd0cfcce1bf5af661b72757ef6e907f3d4bd7'}}
PUBLIC32_RETRY_PAID = '8dfcfdfe8f8be2c0b326520d70eab7f5'
PUBLIC32_RETRY_REQUEST = '848e99e224df8139164df9629299d20d'
PUBLIC32_RETRY_RESPONSE = '4b24f0d33b859635a2736c0b11fb7963'
PUBLIC32_RETRY_RESULT_CANONICAL = {'unit-a': {'848e99e224df8139164df9629299d20d': '31facf2ac27c9eb8f9c3f6a5d683acfa378a13bf042c6b0154e787d1d986f904', '4b24f0d33b859635a2736c0b11fb7963': 'bb1b0bf523aa2636ca40c704e9164bb901290bb01615760da77e45bfbed1af8e'}, 'unit-b': {}, 'unit-c': {'848e99e224df8139164df9629299d20d': 'cca0a8b85417d2fb8a31b960674389ad4221b3fe54b4884a31b26f6f70e73b89', '4b24f0d33b859635a2736c0b11fb7963': '84310ee546c96558afdb0a2d827f61dd20a2a1a8287a4f65e50c85060c877278'}}
PUBLIC32_SENDER_READY_PAID = '6f89e2a21919991e15224e393446928c'
PUBLIC32_SENDER_READY_REQUEST = '480dfacf35d6de374fdf7e62ed016bce'
PUBLIC32_SENDER_READY_RESPONSE = '89ebb5c7bcb47687835b99ee3b605537'
PUBLIC32_SENDER_READY_RESULT_CANONICAL = {'unit-a': {'480dfacf35d6de374fdf7e62ed016bce': '3c427ae8be71289a00b8ae006e9bfbe61d73ea460fffa209dab45523046d5137', '89ebb5c7bcb47687835b99ee3b605537': '788d0a0e99c287a1c9ab3670b4c83ee701549273d1f0df2133aedbf56178ebb0'}, 'unit-b': {}, 'unit-c': {'480dfacf35d6de374fdf7e62ed016bce': '3ae3c86f2f3eed05f03195487af9c5e776300f8a7d928e7e183b6be4482fa383', '89ebb5c7bcb47687835b99ee3b605537': '790dfedb8edede2400fcb43f3d2eb16b1d67cc35449aaff5a6dff78eec3ab6ba'}}
PUBLIC32_COMPLETE_PAID = '62bcef4f1c74532fd7ab3ada1b8b94e2'
PUBLIC32_COMPLETE_REQUEST = '8c002e59f0d6b728c7a4a3abe5904549'
PUBLIC32_COMPLETE_RESPONSE = '98e3ddaf0dc6006dc5590202487c1d88'
PUBLIC32_COMPLETE_RESULT_CANONICAL = {'unit-a': {'8c002e59f0d6b728c7a4a3abe5904549': '0f3692f7337607d458b6027dc2d7108938d057c09d98933e4c13bc1bde588236', '98e3ddaf0dc6006dc5590202487c1d88': '4915fec9688750e3a77f51e953e952c7264c025f3232261a7a3a93dd4e104989'}, 'unit-b': {}, 'unit-c': {'8c002e59f0d6b728c7a4a3abe5904549': '636adcc1a7f1fd3c4a4510fb17ecfe6fb1a6c8137cf3f80f91714266d2dfa951', '98e3ddaf0dc6006dc5590202487c1d88': 'ac01a9a73d3475ab0ed96ab7868a5c8d1f8e437e0da052d2b935e5508f210209'}}
PUBLIC32_PARENTS = (PUBLIC32_PAID,PUBLIC32_RETRY_PAID,PUBLIC32_SENDER_READY_PAID,PUBLIC32_COMPLETE_PAID)
PUBLIC32_OUTCOMES = {PUBLIC32_PAID:(PUBLIC32_REQUEST,PUBLIC32_RESPONSE,PUBLIC32_RESULT_CANONICAL,15),
    PUBLIC32_RETRY_PAID:(PUBLIC32_RETRY_REQUEST,PUBLIC32_RETRY_RESPONSE,PUBLIC32_RETRY_RESULT_CANONICAL,16),
    PUBLIC32_SENDER_READY_PAID:(PUBLIC32_SENDER_READY_REQUEST,PUBLIC32_SENDER_READY_RESPONSE,PUBLIC32_SENDER_READY_RESULT_CANONICAL,17),
    PUBLIC32_COMPLETE_PAID:(PUBLIC32_COMPLETE_REQUEST,PUBLIC32_COMPLETE_RESPONSE,PUBLIC32_COMPLETE_RESULT_CANONICAL,18)}
ENROLLED_ZERO_PAID = '141c892d83b6064a800c11aab6444f28'
ENROLLED_ZERO_DATABASES = {'unit-a':'2e9ffd06bce50b8e870fe29c2f0bbcf7ef7c76d807b5bf59f5e04f6ecc4711f2',
    'unit-b':'e141104945fc1322e398b61a55ec7b037f6f5bf32fb320245642446515c140e1',
    'unit-c':'4cb43e56a11f60e35017ff2b570b659820ab05cd822f4ee4c6eb76b7f8cd88f8'}
PARTIAL_BUNDLE_PAID = '05c36e09fd4056c94d341d2dd32243f4'
PARTIAL_BUNDLE_RUNS = ('2ab6b641bc0f5d5c3ad63051d093d80f','b42ad6cb0629adfc17147f2d8b961ff8')
PARTIAL_BUNDLE_DATABASES = {'unit-a':'e64bc7d43de661d43028702a526b41834f9b9230fb4bd86968593792261c29c9',
    'unit-b':'631834bb8380eeb87a51e230d332c770800cf8286f45b8272bb758166772c856',
    'unit-c':'6a3b56796127f0acd1e6ab3030535189956dd8d4130027f35c3fe11ae078cd57'}
PARTIAL_BUNDLE_OUTCOMES = {'unit-a':{'2ab6b641bc0f5d5c3ad63051d093d80f':'f0566c67181b925ba393d99bc8e68c4efdd83e2e367cd723a31058d269cdf2e2',
    'b42ad6cb0629adfc17147f2d8b961ff8':'39b61bab0af2d5eaac966606bf73b0795f1368eb20a6772eb590bc13678c7fe2'},'unit-b':{},
    'unit-c':{'2ab6b641bc0f5d5c3ad63051d093d80f':'ab7f63d7ce184334272e23cf385982f32c78e48d903340ab68077a919a4e42b5',
    'b42ad6cb0629adfc17147f2d8b961ff8':'86843e110fc1d29ccbd10df24acd55bb0c3365d7cbdd12cbcc9f1acd43b444dc'}}
V2_PARTIAL_BUNDLE_PAID = 'bccb99afe6589968190b131eeaf372b3'
V2_PARTIAL_BUNDLE_RUNS = ('f07a9e716fc658c8a4f8dcd10bbd35cd','7c527791df8400534a83d756cda0fbab')
V2_PARTIAL_BUNDLE_DATABASES = {'unit-a':'cd1e630fa983d41d3940afc40ec65c1e1720fd601ac911f4d8293f76647611c6',
    'unit-b':'a9f9ebc4f1f2a5af2555d82c44a38e1772f75d65eaadfafbdd5a5dfbbc8f5fad',
    'unit-c':'2b418064a3bf9be6cb87aedd7249cad1d0930d47d6f0bf75ebb27640def5be92'}
V2_PARTIAL_BUNDLE_OUTCOMES = {'unit-a':{'f07a9e716fc658c8a4f8dcd10bbd35cd':'7a14ab31216921d4731a1a39dfc43a22f2d207f4cd233cc31ad987806d157e03',
    '7c527791df8400534a83d756cda0fbab':'d07643d57f786e25b2475bebe531d72bf7f8177ead4a03f77f30d9a5d117007f'},'unit-b':{},
    'unit-c':{'f07a9e716fc658c8a4f8dcd10bbd35cd':'406ca28ebdbf3d63749e991b1696657ba3755eec6e00e14bf65bd86434a45593',
    '7c527791df8400534a83d756cda0fbab':'f8e72fe9f0dd8569d803f010781727d931b7f6ea7b32874c68bc2364944e1f60'}}
V2_FAILED_BUNDLE_PAID = '487ae574816ff5444de03e3daa4401e4'
V2_FAILED_BUNDLE_RUNS = ('4cc95f0dc6f3a3ff674b06e424475c44','db2eebfaf67757220cc3dfddd6e8adb1')
V2_FAILED_BUNDLE_DATABASES = {'unit-a':'8e8f3436d3790971ca511ca24a05f2122ec94600a1cbb9b0f10622d0c821a20e',
    'unit-b':'7e1df3bbdbd3215fc284879ca86d2008368032943a4100b6be32ecb9626eed56',
    'unit-c':'26de1a3bb2ebe3cf9034daf82e365b17b067e8bdd269671d439077256bac943a'}
V2_FAILED_BUNDLE_OUTCOMES = {'unit-a':{'4cc95f0dc6f3a3ff674b06e424475c44':'b323be8a45815aae601ab5fbd38789294be6280dcab9697054e7d37879ad975b',
    'db2eebfaf67757220cc3dfddd6e8adb1':'d8fa47ebba50b974ad26397bc9db12ee0bfcc55621be5b69cf0490b56d19685d'},'unit-b':{},
    'unit-c':{'4cc95f0dc6f3a3ff674b06e424475c44':'41e0a427d05fc291986b638a7319f291654b5240ed5a4e636fe2de924ef659a7',
    'db2eebfaf67757220cc3dfddd6e8adb1':'34a087efc4d76baefec15a84fd47c7f334401fe157a8867ed5622a30fbaef58f'}}
V3_PARTIAL_BUNDLE_PAID = 'e01508ddcae7389728add074de4876b7'
V3_PARTIAL_BUNDLE_RUNS = ('0d41d67c0db96671af2eb64dbbe7179e', '13ab39611582fc5e2037f70c78266fce')
V3_PARTIAL_BUNDLE_DATABASES = {'unit-a': '4e94b81f2260cf19d80338de283f29adbd623ff16509472249f68caf44ceecfa', 'unit-b': 'a06dd2242066fd3f22ab20fa5ec00c4f5daf2ffedf2e680e5b4d41779efa83d5', 'unit-c': '04fa06af019fa680964faf9c8e40ff45651e98cfbd08ffb2c3a62a40b0ee0181'}
V3_PARTIAL_BUNDLE_OUTCOMES = {'unit-a': {'0d41d67c0db96671af2eb64dbbe7179e': '8f5bc421a1d91afafb20eccfe96f79482da40a9f54b68c8d2f3c10f837d89cd6', '13ab39611582fc5e2037f70c78266fce': 'f5bab844d3271d75d43a1496eb186f3d1c529c070cecb1295c1d8e1e584a3d7c'}, 'unit-b': {}, 'unit-c': {'0d41d67c0db96671af2eb64dbbe7179e': '201576e41af6bcb4d26eb191835cdb50fd363278101fbebb82b9e0ede99da15a', '13ab39611582fc5e2037f70c78266fce': '1fb96b9b45b60e677c33c8dcd73d30fd7539e4a42ace491031d6592ca02430bd'}}
V3_ACCEPTED_PART_SHA = '08a433a64af413f3e5ec2944f15c5935f0779d9f38e77e29880de94ecef7e7c4'
V3_CHECKPOINT_BUNDLE_PAID = '2429969fcbb0f57b3deaeda7187036c2'
V3_CHECKPOINT_BUNDLE_RUNS = ('a7d2d5714770380d75fd833ca0fce1f2', 'd257a430665407fb424fbdb11cb27847')
V3_CHECKPOINT_BUNDLE_DATABASES = {'unit-a': '8d0bdf05bc5f5a9117feabfba1120973a5f516e0881de6c4f20dc9d8086e30b4', 'unit-b': '0f40850d15cb8505202fcb88c79647de08c47761cde4ba515095aedc9b26175c', 'unit-c': 'c933380b524fdbe2c6161d859f95bd281d1eaa74535d336dc0446dc6006fcb2e'}
V3_CHECKPOINT_BUNDLE_OUTCOMES = {'unit-a': {'a7d2d5714770380d75fd833ca0fce1f2': 'c72032defd46d75b8f053ec14a50910716533e56720d05120dda961578f3b8dd', 'd257a430665407fb424fbdb11cb27847': 'c44130eb0910f14679cca63d12e479be0e289f91e6117f83fa91e6625385e2e8'}, 'unit-b': {}, 'unit-c': {'a7d2d5714770380d75fd833ca0fce1f2': '208dd578c7249819e52a72cb08452694d94111322e7a0905af49b66eed6d03e9', 'd257a430665407fb424fbdb11cb27847': '688c3154e759d0a360868b60bb616e972a8e4663b43fc4f264cab6095255237d'}}
V3_CHECKPOINT_ACCEPTED_PART_SHA = '6adfa44268ab3d49072d6793a55e457a69a22411a94632de2929f62ea5ff0cc9'
V3_VERIFIED_BUNDLE_PAID = '7746ddba2544ce79c01a195f1e8746ce'
V3_VERIFIED_BUNDLE_RUNS = ('f4f23bd0fdb3a629521e36d37855b169', '29589e7aaf30ced0aaaf7354055c2ef1')
V3_VERIFIED_BUNDLE_DATABASES = {'unit-a': '906ea2e0048a5cefaa1086e6f2abe9ae96254381b09e78b1a9ae029ecba5ad05', 'unit-b': '3347171d54390ab83d451b388ab41a1751e7f918021ca2e775eefd78120adb9a', 'unit-c': 'c946e342e024960874c4838580c919f3c5da85f69a16971833cbb9d6f1f45a73'}
V3_VERIFIED_BUNDLE_OUTCOMES = {'unit-a': {'f4f23bd0fdb3a629521e36d37855b169': '741b4c2a18ae476fc416cabbf9972fcf9b0457f38b414bc91192bb875a1fa0db', '29589e7aaf30ced0aaaf7354055c2ef1': '24319657843b5051bfabdf49b56d60e3bd48e08383aee0ce0e33ff20a40b4bda'}, 'unit-b': {}, 'unit-c': {'f4f23bd0fdb3a629521e36d37855b169': '07056059d6c61cf184016ee1d69388edd33b34fc18835b79e11e87885ce23d10', '29589e7aaf30ced0aaaf7354055c2ef1': '1b24720eb7f1b15c7d0e03c64d8b1204d3252b1c28cabe781ceba661fd0fad0d'}}
V3_VERIFIED_ACCEPTED_PART_SHA = '1c9195955f20fa3039bceb02c516a69476798c0c9c0a0e0488b28dd563064992'
SF5_STARTUP_PAID = '57339cca60ddf62b41eafac60147a252'
SF5_STARTUP_RUN = '3ef93b13ec6966964c2f73d1dbd0a176'
SF5_STARTUP_DATABASES = {'unit-a': '488905937419145a0fe89df1e0d601435cbecacb8759d4fc22d128e8c4144424', 'unit-b': '872336ed1ebf330eb309127a9e3f4f11ac601b1e5bff7cb798a70e7d27b68266', 'unit-c': 'd76086b9ab30a64f5910208b8aca1abc490630c416648d76e3f4a0a12d670fd6'}
SF5_STARTUP_OUTCOMES = {'unit-a': {'3ef93b13ec6966964c2f73d1dbd0a176': '5530c5125906f695df9164e6e8c7411613a7f44d89ca701ac755d8b32860d3e5'}, 'unit-b': {}, 'unit-c': {'3ef93b13ec6966964c2f73d1dbd0a176': '70f95a1d0c3174c0fcb32599466bf96c85180f56b9b0d437fe44c012218cf6d4'}}
SF5_COMPLETED_PAID = '4aa3ad2bc1346cae82bc90d8abd6dcd4'
SF5_COMPLETED_RUNS = ('bf0f8bf4bee09cfe456167a777354bba', '958625a64fa91fd76c913851080f997e')
SF5_COMPLETED_OUTCOMES = {'unit-a': {'bf0f8bf4bee09cfe456167a777354bba': '01b60943e8b197b212f29f5e75675c2e8e50c7af0b8633b569d38542cbb379e1', '958625a64fa91fd76c913851080f997e': '7555ef494f3b04a2a2aac4d2bde5449d774c3f8c2d829e5b11726f537c64652b'}, 'unit-b': {}, 'unit-c': {'bf0f8bf4bee09cfe456167a777354bba': '450c07dff679c6fb14d0530edd2f22d554e454299214e7e378e9437002a873cc', '958625a64fa91fd76c913851080f997e': '68b0ab29f465fdfc26df10004314c08353a39a8da279a29d1d53d5bb6356d7b9'}}
PARTIAL_BATCH_PAID='a3ded77ef251c9d7e085ab790b9ac5b3'
PARTIAL_BATCH_FAILED=['7fec4270d11d93927e1f41b198e73102']
PARTIAL_BATCH_DATABASES={'unit-a': '55b4e6393f7736ad1b073269c84f1699b907ecca6bfce09fe148ce3a60333d92', 'unit-b': '03b47059ab3f5d33c295c0532ebd4a7f451a3cd26b6cb9c01800de680332e751', 'unit-c': 'f06a320f9691dcbc54f49233b55a2f36424a91234e3ed81a977895bbc716c190'}
PARTIAL_BATCH_OUTCOMES={'unit-a': {'85aa59f981dfb97ff525e0be9bef4a21': '87c9704bec7e90058d470dc67a057132afa1bb2803daec6a992e2e5563a2d938', 'f4a376e8e651476c86ef7dcb0c8cc51d': '1fe27b07ed150b0128797477b35092644a36678f4fdf94c2471bdd47bc113ddb', '5f4d9228d8727d7a6a8c949b4eb2dde8': 'b4479a14d7b1631b65a263d258d3f6f907e57603361c0d9a06e31e5e5939f774', '7fec4270d11d93927e1f41b198e73102': 'ef74b12c5083f70e8ffc8fc973fb2d6131f2f8b33ec91698e3c6165c730de173'}, 'unit-b': {}, 'unit-c': {'85aa59f981dfb97ff525e0be9bef4a21': '56d6d9580ac616d2837055cb4e1e33572647402cac738204b81be39fe73c323a', 'f4a376e8e651476c86ef7dcb0c8cc51d': 'c4d1226fd837fc1762c8606389e55edef03a7f79f19d5060f03c6a87f40ae085', '5f4d9228d8727d7a6a8c949b4eb2dde8': '3b38d585c089229a1afabf693a663cdc3b06c4dadf6d8de666dcd820f96a18d5', '7fec4270d11d93927e1f41b198e73102': '3757515416e2d0787bbdde21b4e8b5ce72cdce8bf84aabda225d32ae8bfe3ac0'}}
COMPLETED_BATCH_PAID='76fe38883690e058771940d51d0d57d6'
COMPLETED_BATCH_DATABASES={'unit-a': '1e3763e45571580a690139ea0e3bc7125986cfc0a541ac30aee3610455532e72', 'unit-b': '464eeb6e64843a027da032d93d399a0409e3ffbeaf473d24fd6fa0e937ce9b5d', 'unit-c': '93730f3807546b1f7f18943d3471b1696b2ac6675e2d46cfc60736566b257232'}
COMPLETED_BATCH_OUTCOMES={'unit-a': {'17ff6c3215acfbd651a8464509872ddb': 'e068da2efb2a370ce29fc850972313d2d59cc20f629d63ee5d025e72a1099cb4', '44267abc6c6a43cfa5774584cff9f2b6': '40e43da3c66a5670a72192393aa674e21f4025cc94416ff500a48f38a0e865c0', '299ed364c82448815bf33536b87e8117': '8242f05d13abf580c599a0d216f34f4dd023fdaed242a24359378dbfef90a65e', '40f7418d19d2706dfc4f9eef0e36c8f9': '26c430154c34ecd797858a6875a91c6ba0de13ccaaf8e65c911fc82396258763', '1eae08e206a865d7f4e5dc7e8029df05': '531fccd7160cf6ad50f4b02cddcee5ad0036b4a95e45c6ab32962b5adb29928d', 'fb5f692175175fc05d9a8c264e0858b7': '6bd6d6a2ed89e914b2e4b0a5f7a050558629703534afcb8f6baf386fd5aac8f0'}, 'unit-b': {}, 'unit-c': {'17ff6c3215acfbd651a8464509872ddb': '5141af54dee67e04c12dd6e5d30176bf8edbe46d4a2cfa2051830942a6521437', '44267abc6c6a43cfa5774584cff9f2b6': 'eb9a6ac9dc9521a562e7373e034fcf0ca7506eb72a0a53d6ec18c7eb1ad51322', '299ed364c82448815bf33536b87e8117': 'afcbb4fa7aaf57850274d7d0b0f2fdc858b45036e400af3187dd37ac4939028a', '40f7418d19d2706dfc4f9eef0e36c8f9': '9ccfb06bda2410de27fd4bf923a39a7c5d35451de376ddeae552fdd3f2ec83d3', '1eae08e206a865d7f4e5dc7e8029df05': '15740088c9f4d8e8318f898083d8d5b09537286e866cde528d3fe60163aa3655', 'fb5f692175175fc05d9a8c264e0858b7': '7599580ea085fa8ff99cac1cd9c882e42765abce5a092b36bd6ae322d289e65b'}}
RX_PRIORITY_BATCH_PAID='7630420c0e72181d6e7239c105c165c6'
RX_PRIORITY_BATCH_DATABASES={'unit-a': 'a5a88c042be185e2741bdbc363276aa5372283d703b301c72205b7eaaf6c01c6', 'unit-b': 'c783615f52177379b7231039ac5dde7e2e9e0cb87747da10df65153ebedaecbb', 'unit-c': '21e3953adb0bc39485570c4b619bb629d0ac50d0a1a3ee918881b4dd533de905'}
RX_PRIORITY_BATCH_OUTCOMES={'unit-a': {'e85ac2eb60b6a74978463988875d9fe2': 'ea32fb8fe66309c41b2a6753e5bf51785aaa9245d28bc0e848eb5f9d229e7773', 'b125fc2c53c5df0a91ee66512e799179': 'dea960f7366699d5da75a0e1a58ad1cdc8fb5c43fd2cc2b18583576336dabe5b', '224eb352fe615f3dffb04b384e718acc': '4bf10834285fbd9c8702b9aecdaa4ecb3af6b63fe80e25e50d869f611d3633c3', 'ce90e48370a4688288cf5bd0c0e28824': 'bd3dc988d7147ddc602d0c8ec4d9366d66ebefb65612c47730f6e9cd0949451a', 'aa65b1694ffad8cbb4f8d368a69553ca': '8cbf4af04ff23bfd90250797103daa3e412a1e6a58dc1608ab055818178bdb4a', '84028c86276f1db451aedec0ae788fed': '8f6720fff0241682443b32d5180f435bc835ad2220743353e32b0fa491f26d46'}, 'unit-b': {}, 'unit-c': {'e85ac2eb60b6a74978463988875d9fe2': '71f0ecde03658bbae2964ab43caa43f88c93caf1c803b0b7ce244ac79ef90391', 'b125fc2c53c5df0a91ee66512e799179': '8d9ec6dbb7e493fac7d509a1997b2c5bbc692643c2691d3ba5a1dcc824a9f3de', '224eb352fe615f3dffb04b384e718acc': 'd9576b3729cd2e1ec825956306400aae66f0c1b541ad7143297fab10a7c4aaaf', 'ce90e48370a4688288cf5bd0c0e28824': '7cec37bce50494afac92985c0a998627cb311dec2849aded285d802746db896a', 'aa65b1694ffad8cbb4f8d368a69553ca': 'e87921133a8b6f117d0bb7f8a618e3fd3afdee86008511ea1c3b6c4b25e43761', '84028c86276f1db451aedec0ae788fed': '1ea8385f17a65354594d0ebaa1c71bbeb904cfd260cfeb712fa8986377f08f88'}}
EARLY_RX_PARTIAL_PAID='4ca8d86644aa24e6fefd06872a9cd131'
EARLY_RX_PARTIAL_FAILED=['bb7a5dba3aa18ada931b2448d457c041']
EARLY_RX_PARTIAL_DATABASES={'unit-a': '4daba12f3f5280547ff9b522bc428484a226f7a5ae5240aad19e2e13ee5b76dc', 'unit-b': 'be987e50067b9780dd5731b34900fc4ce6092d6dd290ea7fe1888667c1a3a3f1', 'unit-c': '072ce9c8b340af946f0c109dc2f7d81f0ce19c10c9aef013197ad51318e0cb25'}
EARLY_RX_PARTIAL_OUTCOMES={'unit-a': {'53bf4b3b34b200cd04de4efeb1fb2e8e': '6530063f526b5030da8f7d85dd42546faeae55ce1e870d0fc94ed6e806a7e784', '4d6c2286fe4c6aeb5304b832f037732b': 'd5afb4bec3306dcd94e855a8d2f3c9c886276ed8f5cbe80fd32005487856adbe', '81b327fa1dce3e0ddb50525c428b3d28': 'dcba7781b3027e7e64332a6d40b65c4c44be31451c7976e23e3fe5320b4d018b', 'bb7a5dba3aa18ada931b2448d457c041': '56cd45cc44d6620f3ea9adf408a68a6c09eb0883e08a45b7070c64ffb2e04a24'}, 'unit-b': {}, 'unit-c': {'53bf4b3b34b200cd04de4efeb1fb2e8e': '614009b8a8f7c793a300f0004762a083d85509769bf48e53dc19430bf842c04a', '4d6c2286fe4c6aeb5304b832f037732b': '30a211cb4ab910a3297595cdb1151906536fea74f46d81f3aa0391a7eac3f55b', '81b327fa1dce3e0ddb50525c428b3d28': '04ab4ad828bf1c69a671e6d016f712cfef74155a503f58ad8691fc63852d8a88', 'bb7a5dba3aa18ada931b2448d457c041': '5f4da1c7054a336d4f2dadc5c33b3b6ef14fd60ea34ff02290bb6f212ba21366'}}
GFSK_COMPLETE_PAID='6be212b13351cb9852ed07ba7998be96'
GFSK_COMPLETE_DATABASES={'unit-a': '825513aefeeb4b228a2ee28990216cbf220df86868543edfe090630aa5acd89b', 'unit-b': 'f6d66fcc6c183828fee587eb7f1504cd6b788be4f9912cb34d3d14c977732c9a', 'unit-c': '2cf711174437881c6b1fa02e199953a36ae523f4383d277112d0ea0afbb63f48'}
GFSK_COMPLETE_OUTCOMES={'unit-a': {'43d8b8bd22153f1d32d5792ba5721026': 'e55b9b0667b0787e1efa0d7b1dc432f11779ca1481c524e28f369c56f6608619', '31b39c7a0a3f10d30159de2b06908513': '013f8bb9e3feae3fc21ea6c556a64e896865fdfe8a687994bc710e5ea19b0161', 'b8de9bddc9759ed8c35a79b784cc49f2': '9625749b65259b1aa102c88fc8821a0da0d4ed8b80a7fbf5a3d88fd098a7b964', '3a3998fbacc12262a4ec34184b9f906e': '015ab12fc09dbf2859cfed65258098bd9d8aafa1e588978664e47f519b0ae771', 'e47860a3e5cafd82350210e5ed656c53': 'a11f915a2594b8ba091282f9b18041968ff39794340b98de7e6cb9e6e78cfba8', '17da9cf34f519b531bff141458e21fe6': 'da778ec1063daa7140ae858854eb8a7c37f789b604825866f61a8c02e52a8921'}, 'unit-b': {}, 'unit-c': {'43d8b8bd22153f1d32d5792ba5721026': '4660cd4ad93467e5b244b7af8e5bcc27e30a44c5dc6674744289e8090f43b725', '31b39c7a0a3f10d30159de2b06908513': '0f15ad852118fe18d01fff9a6fb77407b2849d60f68f3c166494411b02b54087', 'b8de9bddc9759ed8c35a79b784cc49f2': 'afa7f36af16f536a45f5ae4329783802fc90debf33fc7152f07338c286064985', '3a3998fbacc12262a4ec34184b9f906e': 'b792f28086caa09d95a124573548d1ceb9b08937250050c5ed28b020b71954a8', 'e47860a3e5cafd82350210e5ed656c53': 'f27a7d0645fec02c6652dbb404b5b1c161d82ac53cc542daa33d34634bd147bc', '17da9cf34f519b531bff141458e21fe6': '8f983a4a7425fa2a1475d8b1f922f82468df37b2b520d46b2e0da7a2300cde91'}}
V3_ACCEPTED_PARTS = {V3_PARTIAL_BUNDLE_PAID:(V3_PARTIAL_BUNDLE_RUNS,V3_ACCEPTED_PART_SHA),
    V3_CHECKPOINT_BUNDLE_PAID:(V3_CHECKPOINT_BUNDLE_RUNS,V3_CHECKPOINT_ACCEPTED_PART_SHA),
    V3_VERIFIED_BUNDLE_PAID:(V3_VERIFIED_BUNDLE_RUNS,V3_VERIFIED_ACCEPTED_PART_SHA)}
PARTIAL_BUNDLE_SCOPES = {PARTIAL_BUNDLE_PAID:(20,'c-bundle100k-v1',PARTIAL_BUNDLE_DATABASES,PARTIAL_BUNDLE_OUTCOMES),
    V2_PARTIAL_BUNDLE_PAID:(21,'c-bundle100k-v2',V2_PARTIAL_BUNDLE_DATABASES,V2_PARTIAL_BUNDLE_OUTCOMES),
    V2_FAILED_BUNDLE_PAID:(22,'c-bundle100k-v2',V2_FAILED_BUNDLE_DATABASES,V2_FAILED_BUNDLE_OUTCOMES),
    V3_PARTIAL_BUNDLE_PAID:(23,'c-bundle100k-v3',V3_PARTIAL_BUNDLE_DATABASES,V3_PARTIAL_BUNDLE_OUTCOMES),
    V3_CHECKPOINT_BUNDLE_PAID:(24,'c-bundle100k-v3',V3_CHECKPOINT_BUNDLE_DATABASES,V3_CHECKPOINT_BUNDLE_OUTCOMES),
    V3_VERIFIED_BUNDLE_PAID:(25,'c-bundle100k-v3',V3_VERIFIED_BUNDLE_DATABASES,V3_VERIFIED_BUNDLE_OUTCOMES)}

from .gfsk_bundle_history import PARENT as GFSK_BUNDLE_PAID, NATIVE_OUTCOME_SHA256 as GFSK_BUNDLE_OUTCOMES, NATIVE_DATABASE_SHA256 as GFSK_BUNDLE_DATABASES
GUARDED_PAID = {LATEST_PAID: OLD_DIGESTS[3], COMPLETE_PAID: OLD_DIGESTS[4],
                BULK_PAID: OLD_DIGESTS[5], MIXED_PAID: OLD_DIGESTS[6], SUCCESS_PAID: OLD_DIGESTS[7], PARTIAL_PAID: OLD_DIGESTS[8], ACCEPTED_BULK_PAID: OLD_DIGESTS[9], REVERSE_BULK_PAID: OLD_DIGESTS[10], SMALL_BULK_PAID: OLD_DIGESTS[11], ZERO_RETRY_BULK_PAID: OLD_DIGESTS[12], IMMEDIATE_RX_BULK_PAID: OLD_DIGESTS[13], SF7_REPLICATE_PAID: OLD_DIGESTS[14], PUBLIC32_PAID: OLD_DIGESTS[15], PUBLIC32_RETRY_PAID: OLD_DIGESTS[16], PUBLIC32_SENDER_READY_PAID: OLD_DIGESTS[17], PUBLIC32_COMPLETE_PAID: OLD_DIGESTS[18], ENROLLED_ZERO_PAID: OLD_DIGESTS[19], PARTIAL_BUNDLE_PAID: OLD_DIGESTS[20],V2_PARTIAL_BUNDLE_PAID:OLD_DIGESTS[21],V2_FAILED_BUNDLE_PAID:OLD_DIGESTS[22],V3_PARTIAL_BUNDLE_PAID:OLD_DIGESTS[23],V3_CHECKPOINT_BUNDLE_PAID:OLD_DIGESTS[24],V3_VERIFIED_BUNDLE_PAID:OLD_DIGESTS[25],SF5_STARTUP_PAID:OLD_DIGESTS[26],SF5_COMPLETED_PAID:OLD_DIGESTS[27],PARTIAL_BATCH_PAID:OLD_DIGESTS[28],COMPLETED_BATCH_PAID:OLD_DIGESTS[29],RX_PRIORITY_BATCH_PAID:OLD_DIGESTS[30],EARLY_RX_PARTIAL_PAID:OLD_DIGESTS[31],GFSK_COMPLETE_PAID:OLD_DIGESTS[32],GFSK_BUNDLE_PAID:OLD_DIGESTS[33]}
RETIRED_UNSTARTED = 'd17b99c6932332f4ae649166dd32f129'
KNOWN_CUT_RUN = '01ab075dd06d1399fe2dc27c4a9331d3'
KNOWN_CUTS = {LATEST_PAID: KNOWN_CUT_RUN, COMPLETE_PAID: '55dffd06faee2ca08ccfc61d816a34ef'}
HEX = re.compile(r'[0-9a-f]{64}\Z')
SID = re.compile(r'[0-9a-f]{32}\Z')


def need(value, reason):
    if not value: raise RuntimeError(reason)


def sha(raw): return hashlib.sha256(raw).hexdigest()
def clock(): return time.time_ns()//1000, time.monotonic_ns()//1000
def boot(): return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def stable(value): return {k:value[k] for k in ('unit','host_boot_id','services','service_lifetimes')}

def reserve_history_descriptor_capacity():
    """Reserve before history Reader growth, retaining all continuity handles.

    Reader duplicates for verified inputs and strict capture are bounded by
    published metadata limits. This affects only the current process soft limit.
    """
    current=len(os.listdir('/proc/self/fd'))
    required=current+3*MAX_FILES+3*MAX_DIRS+2*MAX_RUNS*(3+len(OWNERS))+4*64+32+21
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    need(hard==resource.RLIM_INFINITY or required<=hard,'history_descriptor_hard_limit_insufficient')
    changed=soft!=resource.RLIM_INFINITY and soft<required
    if changed:resource.setrlimit(resource.RLIMIT_NOFILE,(required,hard))
    actual_soft,actual_hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    need(actual_hard==hard and (actual_soft==resource.RLIM_INFINITY or actual_soft>=required),
         'history_descriptor_capacity_not_established')
    return dict(current_descriptors=current,required_soft_limit=required,soft_limit=actual_soft,
                hard_limit=actual_hard,changed=changed,scope='this process only; before history reads')

def reserve_descriptor_capacity(files, directories, legacy_rows):
    """Keep every continuity FD; raise only this RPC's soft limit within hard."""
    need(type(files) is dict and len(files)<=MAX_FILES and type(directories) is dict
         and len(directories)<=MAX_DIRS and type(legacy_rows) in (dict,list,tuple)
         and len(legacy_rows)<=MAX_RUNS,'descriptor_scope_exceeds_source_bounds')
    current=len(os.listdir('/proc/self/fd'))
    # One retained strict fence plus the nested legacy fence; both may hold
    # their ancestry(max64 each). Scandir duplicates and systemctl pipes need16.
    # A reopened guard adds its parent and at most20 prepared run-directory FDs.
    required=current+len(files)+len(directories)+len(legacy_rows)*(3+len(OWNERS))+128+16+21
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    need(hard==resource.RLIM_INFINITY or required<=hard,'descriptor_hard_limit_insufficient')
    changed=soft!=resource.RLIM_INFINITY and soft<required
    if changed:resource.setrlimit(resource.RLIMIT_NOFILE,(required,hard))
    actual_soft,actual_hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    need(actual_hard==hard and (actual_soft==resource.RLIM_INFINITY or actual_soft>=required),
         'descriptor_capacity_not_established')
    return dict(current_descriptors=current,required_soft_limit=required,soft_limit=actual_soft,
                hard_limit=actual_hard,changed=changed,scope='this process only')


def endpoint_budget(unit, qualification_kind, radio, tcp):
    """Exact owner-bound public scope; no caller-selected arbitrary maxima."""
    need(unit in ('unit-a','unit-b','unit-c'), 'unknown_endpoint_budget_unit')
    need(qualification_kind is None or type(qualification_kind) is str, 'unknown_public_qualification_kind')
    if qualification_kind is None:
        return Cost() if unit=='unit-a' else radio.plus(radio).plus(
            Cost(ip_frames=tcp.ip_frames*8,tx_stream_bytes=tcp.tx_stream_bytes*8,
                 rx_stream_bytes=tcp.rx_stream_bytes*8))
    if qualification_kind=='mixed-only-v1':
        if unit=='unit-a':return radio.plus(radio).plus(tcp).plus(tcp)
        if unit=='unit-b':return tcp.plus(tcp)
        return radio.plus(radio)
    if qualification_kind in BUNDLE_KINDS:
        return Cost() if unit == 'unit-b' else Cost(radio.radio_attempts*10,radio.estimated_airtime_us*10)
    if qualification_kind in ('c-bulk-8k-batch-v1',GFSK_KIND):
        return Cost() if unit=='unit-b' else Cost(radio.radio_attempts*6,radio.estimated_airtime_us*6)
    if qualification_kind in ('a-bulk-v1','c-bulk-v1','c-bulk-8k-v1','c-bulk32-v1'):
        return Cost() if unit=='unit-b' else radio.plus(radio)
    need(qualification_kind=='mixed-bulk-v1', 'unknown_public_qualification_kind')
    if unit=='unit-c': return radio.plus(radio).plus(radio).plus(radio)
    return radio.plus(radio).plus(tcp).plus(tcp)


def owner_radio_profile(owner, policy):
    """An explicit SF5 choice is confined to the two-link C/A 8 KiB scope."""
    name = owner.get('radio_profile_name', 'sf7-500')
    if name==GFSK_NAME and policy.qualification_kind==GFSK_MB1_BUNDLE_KIND:
        from .gfsk_mb1_bundle import require_owner
        require_owner(owner,policy)
        return profile_for(GFSK_NAME)
    if name==GFSK_NAME and policy.qualification_kind==GFSK_BUNDLE_KIND:
        from .gfsk_bundle import require_owner
        require_owner(owner,policy)
        return profile_for(GFSK_NAME)
    if name==GFSK_NAME:
        from .gfsk_batch import BatchContext as GfskContext
        need(owner.get('radio_modem')==GFSK_NAME and policy.qualification_kind==GFSK_KIND,'owner_gfsk_scope_not_reviewed')
        GfskContext.from_owner(owner,policy)
        selected=profile_for(GFSK_NAME);need(profile_record(selected)==owner['rf_profile'],'owner_RF_profile_changed');return selected
    need('radio_modem' not in owner,'owner_unreviewed_radio_modem')
    need(type(name) is str and name in ('sf7-500', 'sf5-500'), 'owner_RF_profile_not_reviewed')
    need(name != 'sf5-500' or ((owner.get('qualification_kind')=='c-bulk-8k-v1' and policy.qualification_kind is None)
         or (owner.get('qualification_kind')=='c-bulk-8k-batch-v1' and policy.qualification_kind=='c-bulk-8k-batch-v1')),
         'owner_SF5_scope_not_reviewed')
    if owner.get('qualification_kind')=='c-bulk-8k-batch-v1':
        from .sf5_batch import BatchContext
        BatchContext.from_owner(owner,policy)
    selected = profile_for(name)
    need(asdict(selected) == owner['rf_profile'], 'owner_RF_profile_changed')
    return selected


def parse(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, 'duplicate_rpc_metadata_key'); value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique)


class Reader:
    """Held protected metadata/locks and immutable bytes, no SQLite path open.

    Each check still re-reads every held file and verifies its complete stamp,
    ancestry and open-description token. Exact byte comparison to the original
    verified read avoids hashing the same source/capture bytes on every native
    permission callback. It caches no permission or current-file observation.
    """
    def __init__(self):
        self.stack, self.held, self.directories = ExitStack(), [], {}
        self._nodes, self._tokens, self._named = {}, {}, {}
        self._node_state = self._held_state = self._directory_state = ()
        self._token_state = ()
        self._named_state = ()
        self._owner = (os.getpid(), threading.get_ident(), os.geteuid())
        self._owner_ref = self._owner
        self._refs = (self.stack, self.held, self.directories, self._nodes, self._tokens, self._named)
        self._closed = False
    def _available(self, *, full=True):
        need(not self._closed and self._owner is self._owner_ref
             and self._owner == (os.getpid(), threading.get_ident(), os.geteuid())
             and all(a is b for a,b in zip((self.stack,self.held,self.directories,self._nodes,self._tokens,self._named),self._refs))
             and len(self._nodes)==len(self._node_state) and len(self.held)==len(self._held_state)
             and len(self.directories)==len(self._directory_state) and len(self._tokens)==len(self._token_state)
             and len(self._named)==len(self._named_state),
             'rpc_reader_owner_changed')
        if full:
            need(tuple(self._nodes.items())==self._node_state and tuple(self.held)==self._held_state
                 and tuple((path,tuple(identity)) for path,identity in self.directories.items())==self._directory_state
                 and tuple(self._tokens.items())==self._token_state
                 and tuple(self._named.items())==self._named_state, 'rpc_reader_state_changed')
    @staticmethod
    def _close_owned(fd, identity, token):
        try:
            if tuple(_stamp(os.fstat(fd))[:2]) == identity and os.lseek(fd,0,os.SEEK_CUR) == token:
                os.close(fd)
        except OSError: pass
    def _retain(self, fd):
        if fd in self._tokens:
            os.close(fd); need(False,'rpc_descriptor_reused')
        identity = tuple(_stamp(os.fstat(fd))[:2])
        token = MAX_FILE + fd + 1
        try: os.lseek(fd,token,os.SEEK_SET)
        except BaseException:
            os.close(fd); raise
        self.stack.callback(self._close_owned,fd,identity,token)
        self._tokens[fd] = token; self._token_state = tuple(self._tokens.items())
        return token
    def _check_nodes(self):
        self._available()
        need(all(os.lseek(fd,0,os.SEEK_CUR)==token for fd,token in self._token_state),
             'rpc_reader_descriptor_changed')
        for fd,parent,name,identity,token in self._nodes.values():
            need(tuple(_stamp(os.fstat(fd))[:4]) == identity
                 == tuple(_stamp(os.stat(name,dir_fd=parent,follow_symlinks=False))[:4])
                 and os.lseek(fd,0,os.SEEK_CUR) == token, 'rpc_ancestry_changed')
        self._available()
    def directory(self, path):
        self._available()
        absolute = Path(os.path.abspath(path)); names = ('/',*absolute.parts[1:])
        need(len(names)<=64 and len(str(absolute))<=4096, 'ancestry_too_long')
        parent = None; ancestor = Path('/')
        for index,name in enumerate(names):
            if index: ancestor = ancestor/name
            if ancestor not in self._nodes:
                fd = os.open(name,_FLAGS|os.O_DIRECTORY,dir_fd=parent)
                token = self._retain(fd); info = os.fstat(fd)
                need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0,os.geteuid())
                     and not stat.S_IMODE(info.st_mode)&0o022, 'writable_or_foreign_ancestor')
                self._nodes[ancestor] = (fd,parent,name,tuple(_stamp(info)[:4]),token)
                self._node_state = tuple(self._nodes.items())
            parent = self._nodes[ancestor][0]
        self._check_nodes(); info = os.fstat(parent)
        need(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700, 'unsafe_private_leaf')
        identity = list(_stamp(info)[:4])
        need(path not in self.directories or self.directories[path]==identity, 'rpc_directory_rebound')
        self.directories[path] = identity
        self._directory_state = tuple((path,tuple(value)) for path,value in self.directories.items())
        return parent
    def read(self, path, maximum, *, lock=False):
        parent = self.directory(path.parent)
        fd = os.open(path.name,_FLAGS,dir_fd=parent); self._retain(fd); self._check_nodes()
        s = os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
             and stat.S_IMODE(s.st_mode) == 0o600 and s.st_nlink == 1
             and 0 <= s.st_size <= maximum, 'unsafe_rpc_metadata')
        if lock: fcntl.flock(fd, fcntl.LOCK_SH|fcntl.LOCK_NB)
        raw = os.pread(fd, maximum+1, 0)
        need(len(raw) == s.st_size, 'short_rpc_metadata')
        identity = tuple(_stamp(s))
        need(tuple(_stamp(os.fstat(fd))) == identity
             == tuple(_stamp(os.stat(path.name,dir_fd=parent,follow_symlinks=False))), 'rpc_metadata_changed')
        self._check_nodes()
        need(tuple(_stamp(os.fstat(fd))) == identity
             == tuple(_stamp(os.stat(path.name,dir_fd=parent,follow_symlinks=False)))
             and os.lseek(fd,0,os.SEEK_CUR)==self._tokens[fd], 'rpc_metadata_changed_after_check')
        self._available()
        # Reuse the already retained parent; full ancestry is still checked
        # before and after every complete byte reread.
        named = (parent,path.name)
        need(path not in self._named or self._named[path]==named, 'rpc_named_parent_rebound')
        self._named[path] = named; self._named_state = tuple(self._named.items())
        self.held.append((path,fd,identity,raw)); self._held_state = tuple(self.held)
        return raw
    def check(self):
        self._check_nodes()
        for path, fd, identity, expected in self._held_state:
            parent,name=self._named[path]
            token=self._tokens[fd]
            need(tuple(_stamp(os.fstat(fd))) == identity
                 == tuple(_stamp(os.stat(name,dir_fd=parent,follow_symlinks=False)))
                 and os.lseek(fd,0,os.SEEK_CUR) == token, 'held_rpc_metadata_changed')
            raw=os.pread(fd,identity[5]+1,0);self._available(full=False)
            need(tuple(_stamp(os.fstat(fd))) == identity
                 == tuple(_stamp(os.stat(name,dir_fd=parent,follow_symlinks=False)))
                 and os.lseek(fd,0,os.SEEK_CUR) == token
                 and raw == expected, 'held_rpc_metadata_changed_after_read')
        self._check_nodes()
        return True
    def close(self):
        if self._closed or self._owner_ref[:2] != (os.getpid(),threading.get_ident()): return
        self._closed = True; self._refs[0].close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class Runtime:
    """Full independently pinned installed SDK-free source closure, held per RPC."""
    def __init__(self, root, digest):
        need(type(digest) is str and HEX.fullmatch(digest), 'invalid_runtime_manifest_pin')
        self.root, self.reader = Path(root), Reader()
        try:
            need(self.root.is_absolute() and self.root.parent == Path('/opt/meshticulum-bench')
                 and re.fullmatch(r'runtime-[0-9a-f]{16}', self.root.name), 'unsafe_runtime_root')
            raw = self.reader.read(self.root/'manifest.json', 65536)
            need(sha(raw) == digest, 'runtime_manifest_changed'); self.manifest = parse(raw)
            m = self.manifest
            need(m['private_services_enabled'] is False and m['public_parent_claims_required'] is True
                 and type(m['files']) is dict and 75 <= len(m['files']) <= 150, 'wrong_public_runtime')
            for name, pin in m['files'].items():
                part = Path(name)
                need(type(name) is str and not part.is_absolute() and '..' not in part.parts
                     and '.' not in part.parts and HEX.fullmatch(pin), 'unsafe_runtime_member')
                need(sha(self.reader.read(self.root/part, 500000)) == pin, 'runtime_payload_changed')
            relative = 'lantern_meshticulum/bench/pilot_resume_rpc.py'
            need(relative in m['files'] and Path(__file__).absolute() == self.root/relative,
                 'RPC_not_installed_pinned_source')
            self.check()
        except BaseException: self.close(); raise
    def check(self): return self.reader.check()
    def close(self): self.reader.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def phase_check(unit, expected, start, end, phase, *, commitment=None):
    maximum=899000000
    if commitment is not None:
        from .pilot_bundle import COMMITTED_CONTRACTS
        need(type(commitment) is dict and type(commitment.get('qualification_kind')) is str
             and commitment['qualification_kind'] in COMMITTED_CONTRACTS,'v2_capture_phase_not_original')
        contract=COMMITTED_CONTRACTS[commitment['qualification_kind']]
        need(commitment['schema']==contract[4] and commitment['context_schema']==contract[0]
             and (start,end)==(commitment['phase_start_epoch_us'],commitment['phase_end_epoch_us'])
             and end-start==contract[2],'v2_capture_phase_not_original')
        maximum=contract[2]
    now = clock()
    need(type(start) is int and type(end) is int and start <= now[0] < end
         and phase['epoch_us'] <= now[0] and phase['monotonic_us'] <= now[1]
         < phase['deadline_monotonic_us'] and end-start <= maximum,
         'original_native_phase_expired')
    observed = stable(native_facts(unit))
    need(observed == expected and observed['host_boot_id'] == boot(), 'native_facts_changed')
    return observed


def write_new(directory, name, raw):
    need(type(raw) is bytes and len(raw) <= 2000000, 'RPC_write_bound')
    with _Ancestry(directory, os.geteuid()) as chain:
        fd = os.open(name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
                     0o600, dir_fd=chain.fds[-1])
        try:
            view = memoryview(raw)
            while view:
                done = os.write(fd, view); need(done > 0, 'short_rpc_write'); view = view[done:]
            os.fsync(fd)
        finally: os.close(fd)
        os.fsync(chain.fds[-1]); chain.check()


def full_history(reader, unit, history, guard, *, own_parent=None, own_runs=()):
    """Actual three-parent collect plus all original full grants, not DTO authority."""
    reserve_history_descriptor_capacity()
    old = history['previous']; inventory = history['legacy_inventory']
    policies = [ParentPolicy.from_dict(v['policy']) for v in old]
    need(tuple(p.parent_id for p in policies) == HISTORY
         and tuple(p.digest for p in policies) == OLD_DIGESTS, 'wrong_retained_parents')
    original = {r['run_id']:r for r in inventory['rows']}
    newer = {r['run_id']:r for v in old for r in v['runs']+v.get('retired_unstarted',[])}
    need(len(original) == {'unit-a':41,'unit-b':64,'unit-c':63}[unit]
         and len(original) == len(inventory['rows']) and not set(original)&set(newer), 'wrong_legacy_inventory')
    known, reports = {}, []
    def names():
        reader.directory(STATE); reader.directory(STATE/'parents'); reader.directory(STATE/'runs')
        retained = historical_parent_ids(STATE,unit,clock,require_retained=True,require_unadmitted=True)
        need(own_parent not in CAPTURE_ONLY_PARENTS
             and sorted(os.listdir(STATE/'parents')) == sorted(retained+((own_parent,) if own_parent else ()))
             and sorted(os.listdir(STATE/'runs')) == sorted(set(original)|set(newer)|set(own_runs)),
             'foreign_or_missing_historical_namespace')
    def metadata(path, limit=MAX_FILE, lock=False):
        raw = reader.read(path, limit, lock=lock); known[str(path.relative_to(STATE))] = sha(raw); return raw
    guard(); names(); totals = {k:0 for k in inventory['full_caps_retained_totals']}
    for relative, pin in capture_paths(STATE,unit)['files'].items():
        need(sha(metadata(STATE/relative)) == pin, 'retained_capture_file_changed')
    for run, row in list(original.items())+list(newer.items()):
        target = STATE/'runs'/run; reader.directory(target)
        raw = metadata(target/'grant.json', 16384); g = parse(raw)
        need(sha(raw) == row['grant_sha256'] and g['expires_epoch_us'] < clock()[0], 'old_grant_changed_or_live')
        marker = metadata(target/'active', 128, True)
        need(marker == ((row['active_marker']+'\n').encode() if run in original else b'retired\n'),
             'old_active_marker_changed')
        if run not in original: continue
        need(g['issued_epoch_us'] == row['original_issued_epoch_us']
             and g['expires_epoch_us'] == row['original_expires_epoch_us']
             and g.get('host_boot_id') == row['host_boot_id'] == boot()
             and row['actual_cost'] is None, 'old_original_binding_changed')
        if g['schema'] == 'meshticulum.ip-service-run.v1':
            need(g['session']['session_id'] == run, 'old_IP_session_changed'); limits = g['limits']
            cost = dict(ip_frames=limits['max_frames'],tx_stream_bytes=limits['max_tx_stream_bytes'],
                        rx_stream_bytes=limits['max_rx_stream_bytes'],radio_attempts=0,radio_estimated_airtime_us=0)
        else:
            need(g['schema'] in ('meshticulum.bench-run.v1','meshticulum.service-run.v1')
                 and g['session_id'] == run and g['unit'] == unit, 'old_radio_session_changed')
            cost = dict(radio_attempts=g['profile']['max_boot_attempts'],
                radio_estimated_airtime_us=g['profile']['max_boot_estimated_airtime_us'],
                ip_frames=0,tx_stream_bytes=0,rx_stream_bytes=0)
        need(cost == row['caps'], 'original_full_caps_changed')
        for key, value in cost.items():
            need(type(value) is int and value >= 0, 'unsafe_old_cost'); totals[key] += value
    need(totals == inventory['full_caps_retained_totals'], 'original_cap_sum_changed')
    for index, (v, policy) in enumerate(zip(old, policies)):
        guard(); target = STATE/'parents'/policy.parent_id; reader.directory(target)
        prior = inventory if v['prior_kind'] == 'legacy' else dict(v['prior_metadata'],legacy_inventory=inventory)
        for name, value in (('policy.json',v['policy']),('owner.json',v['owner']),('prior.json',prior)):
            need(metadata(target/name,200000) == canonical(value), 'old_parent_input_changed')
        need(policy.expires_epoch_us < clock()[0] and policy.unit(unit).host_boot_id == boot(), 'old_parent_live_or_rebooted')
        need(sha(metadata(target/'native.sqlite')) == v['database_sha256'], 'old_native_database_changed')
        end = min(clock()[0]+35000000, guard()['phase_end_epoch_us'])
        legacy = None
        if v['runs']:
            legacy = DebtScope.from_dict(dict(schema='meshticulum.public-debt-scope.v1',unit=unit,
                current_host_boot_id=boot(),runtime_sha256=v['owner']['runtime_sha256'],
                manifest_sha256=v['owner']['manifest_sha256'],observation_expires_epoch_us=end,runs=v['runs']))
        observed = collect(STATE,Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,
            policy.parent_id,policy.digest,v['database_sha256'],unit,boot(),end),
            clock=clock,boot_reader=boot,legacy_scope=legacy).to_dict()
        need(observed['status'] == 'scoped_rows_observed' and observed['scoped_rows_complete']
             and observed['policy_expired_at_observation'] and not observed['pending_unknown'], 'old_parent_uncertain')
        need(sha(canonical({k:observed[k] for k in v['projection_keys']})) == v['projection_sha256'], 'old_parent_rows_changed')
        need(observed['prior_lineage']['global_snapshot_sha256'] == policy.prior_snapshot_sha256
             == sha(canonical(v['owner']['prior_registry'])), 'old_global_lineage_changed')
        local = next(r for r in observed['prior_lineage']['units'] if r['unit'] == unit)
        need(local['prior_snapshot_sha256'] == sha(canonical(prior)) == policy.unit(unit).prior_snapshot_sha256,
             'old_local_lineage_changed')
        claims = observed['detail']['claims']
        if policy.parent_id in GUARDED_PAID:
            for relative,pin in latest_preparation_files(reader,unit,policy,observed,
                                                       v['preparation_sha256']).items(): known[relative]=pin
        refs = sorted([dict(controller_allocation_id=c['controller_allocation_id'],child=c['child'])
                       for c in claims],key=lambda c:c['child']['run_id'])
        unstarted = v.get('retired_unstarted',[])
        if policy.parent_id==PARTIAL_PAID:
            qualify_partial_costs(unit,observed,v,refs)
        else:need(refs == v['controller_endpoint_refs'], 'old_child_relation_changed')
        qualify_history_claims(policy,unit,observed,v['runs'],unstarted)
        for row in unstarted:
            evidence = retired_unstarted(reader,unit,policy,observed,row,clock)
            for name,pin in evidence['files'].items(): known[name] = pin
        if policy.parent_id == BULK_PAID:
            need(v.get('failed_started_runs',[]) == ([BULK_RECEIVER_RUN] if unit=='unit-c' else []),
                 'bulk_failed_endpoint_scope_changed')
        if policy.parent_id == MIXED_PAID:
            need(v.get('failed_started_runs',[]) == ([MIXED_RECEIVER_RUN] if unit=='unit-c' else []),
                 'mixed_failed_endpoint_scope_changed')
        if policy.parent_id in (SUCCESS_PAID,ACCEPTED_BULK_PAID,REVERSE_BULK_PAID,ZERO_RETRY_BULK_PAID,IMMEDIATE_RX_BULK_PAID,SF7_REPLICATE_PAID,SF5_COMPLETED_PAID):
            need(v.get('failed_started_runs',[])==[], 'successful_mixed_failure_scope_changed')
        if policy.parent_id in PUBLIC32_PARENTS:
            qualify_public32_failure_scope(unit,policy.parent_id,v)
        if policy.parent_id in (COMPLETED_BATCH_PAID,RX_PRIORITY_BATCH_PAID,GFSK_COMPLETE_PAID,GFSK_BUNDLE_PAID):
            need(v['require_result_complete'] is True and v.get('failed_started_runs')==[], 'completed_batch_failure_scope_changed')
        if policy.parent_id == EARLY_RX_PARTIAL_PAID:
            need(v['require_result_complete'] is False and v.get('failed_started_runs')==([] if unit=='unit-b' else EARLY_RX_PARTIAL_FAILED),'early_rx_partial_failure_scope_changed')
        if policy.parent_id == PARTIAL_BATCH_PAID:
            need(v['require_result_complete'] is False and v.get('failed_started_runs')==([] if unit=='unit-b' else PARTIAL_BATCH_FAILED),
                 'partial_batch_failure_scope_changed')
        if policy.parent_id == SF5_STARTUP_PAID:
            need(v['require_result_complete'] is False and v.get('failed_started_runs')
                 == ([] if unit=='unit-b' else [SF5_STARTUP_RUN]), 'sf5_startup_failure_scope_changed')
        if policy.parent_id in PARTIAL_BUNDLE_SCOPES:
            need(v['require_result_complete'] is (policy.parent_id!=V2_FAILED_BUNDLE_PAID)
                 and v.get('failed_started_runs')==([V2_FAILED_BUNDLE_RUNS[1]] if policy.parent_id==V2_FAILED_BUNDLE_PAID and unit!='unit-b' else []),
                 'partial_bundle_failure_scope_changed')
        if v['runs']:
            need(sha(canonical(observed['legacy_runs'])) == v['legacy_sha256'], 'old_actual_rows_changed')
            relation = observed['legacy_relation']
            omitted = relation['native_claims_without_scoped_actuals'] if relation is not None else []
            need(relation is not None and {r['run_id'] for r in omitted} == {r['run_id'] for r in unstarted}
                 and len(relation['links']) == len(v['runs']) and all(r['no_refund'] is True
                 and r['separate_debt_sum_permitted'] is False for r in relation['links']), 'old_actual_coverage_missing')
            if v['require_result_complete'] or policy.parent_id in PUBLIC32_PARENTS:
                need(observed['legacy_scoped_rows_complete'] and relation['scoped_linkage_complete']
                     and all(r['relation'] == 'same_endpoint_allocation' for r in relation['links']), 'old_endpoint_overlap_unqualified')
            for row in observed['legacy_runs']:
                need(row['status'] == 'scoped_rows_observed' and row['wire']['retired'] == 1,
                     'old_actual_journal_unsettled')
                if row['journal']['has_unknown_or_pending']:
                    qualify_known_cut(reader,unit,policy,v['owner'],observed,row,
                                     KNOWN_CUTS.get(policy.parent_id))
                result_raw = metadata(STATE/'runs'/row['run_id']/'result.json',300000); result = parse(result_raw)
                need(sha(result_raw) == row['files']['result.json']['sha256']
                     and result['complete'] is (row['run_id'] not in v.get('failed_started_runs',[])
                          if policy.parent_id in GUARDED_PAID else v['require_result_complete'])
                     and (row['transport'] == 'tcp' or result['disarmed_on_exit'] is True)
                     and result['parent']['started'] is True and result['parent']['run_id'] == row['run_id']
                     and result['parent']['parent_id'] == policy.parent_id, 'old_actual_result_rebound')
                if policy.parent_id == BULK_PAID and unit=='unit-c' and row['run_id']==BULK_RECEIVER_RUN:
                    qualify_bulk_receiver(row,result,result_raw)
                if policy.parent_id == MIXED_PAID and unit=='unit-c' and row['run_id']==MIXED_RECEIVER_RUN:
                    qualify_mixed_receiver(row,result,result_raw)
                if policy.parent_id in PUBLIC32_PARENTS: qualify_public32_result(unit,row['run_id'],result,parent_id=policy.parent_id)
                if policy.parent_id in PARTIAL_BUNDLE_SCOPES: qualify_partial_bundle_result(unit,row['run_id'],result,parent_id=policy.parent_id)
                if policy.parent_id == SF5_STARTUP_PAID: qualify_sf5_startup_result(unit,row['run_id'],result)
                if policy.parent_id == RX_PRIORITY_BATCH_PAID:
                    need(sha(canonical(result))==RX_PRIORITY_BATCH_OUTCOMES[unit][row['run_id']] and result['complete'] is True,'rx_priority_batch_outcome_changed')
                if policy.parent_id == COMPLETED_BATCH_PAID:
                    need(sha(canonical(result))==COMPLETED_BATCH_OUTCOMES[unit][row['run_id']] and result['complete'] is True,'completed_batch_outcome_changed')
                if policy.parent_id == GFSK_BUNDLE_PAID:
                    need(sha(canonical(result))==GFSK_BUNDLE_OUTCOMES[unit][row['run_id']] and result['complete'] is True and result.get('radio_modem')==GFSK_NAME,'completed_gfsk_bundle_outcome_changed')
                if policy.parent_id == GFSK_COMPLETE_PAID:
                    need(sha(canonical(result))==GFSK_COMPLETE_OUTCOMES[unit][row['run_id']] and result['complete'] is True and result.get('radio_modem')==GFSK_NAME,'completed_gfsk_outcome_changed')
                if policy.parent_id == EARLY_RX_PARTIAL_PAID:
                    qualify_early_rx_partial_result(unit,row['run_id'],result)
                if policy.parent_id == PARTIAL_BATCH_PAID:
                    need(sha(canonical(result))==PARTIAL_BATCH_OUTCOMES[unit][row['run_id']], 'partial_batch_outcome_changed')
                if policy.parent_id == SF5_COMPLETED_PAID:
                    need(sha(canonical(result))==SF5_COMPLETED_OUTCOMES[unit][row['run_id']] and result['complete'] is True,
                         'completed_sf5_outcome_changed')
                for name, record in row['files'].items():
                    need(name in _FILES, 'unallowlisted_old_metadata')
                    raw = metadata(STATE/'runs'/row['run_id']/name)
                    need(sha(raw) == record['sha256'], 'old_typed_file_changed')
        for name, record in observed['files'].items():
            need(name in _FILES and sha(metadata(target/name)) == record['sha256'], 'old_parent_file_changed')
        if policy.parent_id==ENROLLED_ZERO_PAID:
            qualify_enrolled_zero_costs(unit,policy,observed,v)
        elif policy.parent_id in PARTIAL_BUNDLE_SCOPES:
            qualify_partial_bundle_costs(unit,policy,observed,v)
            if policy.parent_id in V3_ACCEPTED_PARTS:verify_retained_v3_part_files(reader,unit,policy,observed,v)
        elif policy.parent_id==RX_PRIORITY_BATCH_PAID:
            qualify_rx_priority_batch_costs(unit,policy,observed,v)
        elif policy.parent_id==COMPLETED_BATCH_PAID:
            qualify_completed_batch_costs(unit,policy,observed,v)
        elif policy.parent_id==GFSK_BUNDLE_PAID:
            qualify_gfsk_bundle_costs(unit,policy,observed,v)
        elif policy.parent_id==GFSK_COMPLETE_PAID:
            qualify_gfsk_complete_costs(unit,policy,observed,v)
        elif policy.parent_id==EARLY_RX_PARTIAL_PAID:
            qualify_early_rx_partial_costs(unit,policy,observed,v)
        elif policy.parent_id==PARTIAL_BATCH_PAID:
            qualify_partial_batch_costs(unit,policy,observed,v)
        elif policy.parent_id==SF5_STARTUP_PAID:
            qualify_sf5_startup_costs(unit,policy,observed,v)
        elif policy.parent_id!=PARTIAL_PAID:
            need(observed['detail']['prior_plus_prepaid_caps'] == v['paid_caps'], 'old_paid_caps_changed')
        if index: need(asdict(policy.unit(unit).prior_debt) == old[index-1]['paid_caps'], 'old_paid_debt_chain_changed')
        reports.append(dict(parent_id=policy.parent_id,paid_caps=v['paid_caps'],actual_children=len(v['runs'])))
    names(); reader.check(); guard()
    return known, original, newer, reports


def latest_preparation_files(reader,unit,policy,observed,pins):
    ids={c['child']['run_id'] for c in observed['detail']['claims']}
    need(set(pins)==ids,'latest_preparation_scope_changed')
    target=STATE/'parents'/policy.parent_id/'prepared-identities';reader.directory(target)
    need(set(os.listdir(target))=={run+'.json' for run in ids},'latest_preparation_namespace_changed')
    files={}
    for run,pin in pins.items():
        raw=reader.read(target/(run+'.json'),4096)
        need(sha(raw)==pin,'latest_preparation_record_changed')
        files[str((target/(run+'.json')).relative_to(STATE))]=pin
    reader.check();return files


def qualify_history_claims(policy,unit,observed,started,unstarted):
    """Predicate over the actual collector result; never DTO admission."""
    detail=observed['detail']; claims=detail['claims']
    ids={r['run_id'] for r in started}; absent={r['run_id'] for r in unstarted}
    actual_started={c['child']['run_id'] for c in claims if c['started'] is True}
    actual_absent={c['child']['run_id'] for c in claims if c['started'] is False}
    need(not ids&absent and detail['claim_count']==detail['prepared_count']==len(ids|absent)
         and detail['start_count']==len(ids) and all(c['prepared'] is True for c in claims)
         and actual_started==ids and actual_absent==absent,'old_child_relation_changed')
    if policy.parent_id == LATEST_PAID:
        need((len(ids),absent)==((0,set()) if unit=='unit-a' else (8,{RETIRED_UNSTARTED})),
             'latest_paid_claim_scope_changed')
    elif policy.parent_id == COMPLETE_PAID:
        need((len(ids),absent)==((0,set()) if unit=='unit-a' else (10,set())),
             'complete_paid_claim_scope_changed')
    elif policy.parent_id == BULK_PAID:
        expected={'unit-a':{'27e27a319d4a86cdc32236554de58cf7','7abd5cfd2c3da01ae4a5f687867073d4','da4ef48b14383c3f6cad0a8bc1ba396a','deea3ba36c13cd67b350cdd01785a80a'},
            'unit-b':{'7abd5cfd2c3da01ae4a5f687867073d4',BULK_RECEIVER_RUN,'d19e8fa1002399b8db98f8ddf26b4296','deea3ba36c13cd67b350cdd01785a80a'},
            'unit-c':{'27e27a319d4a86cdc32236554de58cf7',BULK_RECEIVER_RUN,'d19e8fa1002399b8db98f8ddf26b4296','da4ef48b14383c3f6cad0a8bc1ba396a'}}
        need(ids==expected[unit] and not absent,'bulk_paid_claim_scope_changed')
    elif policy.parent_id == MIXED_PAID:
        expected={'unit-a':{'2b12eb6b8edad86af9ef926820bdca34','76fb18ac4571c12a4c461a9b9c18de43',MIXED_RECEIVER_RUN,'bab9e8237656cf6860cae9e2c0eb9cbe'},
            'unit-b':{'76fb18ac4571c12a4c461a9b9c18de43','8c749476914d876554f0052ab841a0d2','9fcc14ee8e50a8f9093e0e1362f34d76','bab9e8237656cf6860cae9e2c0eb9cbe'},
            'unit-c':{'2b12eb6b8edad86af9ef926820bdca34','8c749476914d876554f0052ab841a0d2',MIXED_RECEIVER_RUN,'9fcc14ee8e50a8f9093e0e1362f34d76'}}
        need(ids==expected[unit] and not absent,'mixed_paid_claim_scope_changed')
    elif policy.parent_id == SUCCESS_PAID:
        expected={'unit-a':{'1dc2d03da057186c81d1476aabe611b2','2900ab6e27f2abca9e7d7a77b5d60c10','3e387ba3fcdabef7a0ac1351655022c9','5dc283b3f4af4fea45d83158a6cf8846'},
            'unit-b':{'2900ab6e27f2abca9e7d7a77b5d60c10','3e387ba3fcdabef7a0ac1351655022c9'},
            'unit-c':{'1dc2d03da057186c81d1476aabe611b2','5dc283b3f4af4fea45d83158a6cf8846'}}
        need(ids==expected[unit] and not absent,'successful_mixed_claim_scope_changed')
    elif policy.parent_id==PARTIAL_PAID:
        need(not ids and absent==({PARTIAL_RUN} if unit=='unit-c' else set()),'partial_claim_scope_changed')
    elif policy.parent_id==ACCEPTED_BULK_PAID:
        expected=set() if unit=='unit-b' else {'b481688a3efc8b1adb2ce75e6e78fe8c','d76fa4db7c77a96dff2f5fc5d1a54091'}
        need(ids==expected and not absent,'accepted_bulk_claim_scope_changed')
    elif policy.parent_id==REVERSE_BULK_PAID:
        expected=set() if unit=='unit-b' else {'dca09df76b8115eef1216b7c29de31b1','b4b2c3afe5c937738bb3a9fd1ece281c'}
        need(ids==expected and not absent,'reverse_bulk_claim_scope_changed')
    elif policy.parent_id==SMALL_BULK_PAID:
        expected=set() if unit=='unit-b' else {'28a613163ef48a171afae569b8a4e5a7','77b646ef8e40d770e08ed0729d48c4b9'}
        need(ids==expected and not absent,'small_bulk_claim_scope_changed')
    elif policy.parent_id==ZERO_RETRY_BULK_PAID:
        expected=set() if unit=='unit-b' else {'79d9fe1ec599a095f615d81e76fdfc4c','9b03b50546a1db49d48d0d60453f2b2b'}
        need(ids==expected and not absent,'zero_retry_bulk_claim_scope_changed')
    elif policy.parent_id==IMMEDIATE_RX_BULK_PAID:
        expected=set() if unit=='unit-b' else {'8986606635a4ef2d0b96b2700d946ee4','c08de47e5afdd8b287e46354afe4d79a'}
        need(ids==expected and not absent,'immediate_rx_bulk_claim_scope_changed')
    elif policy.parent_id==SF7_REPLICATE_PAID:
        expected=set() if unit=='unit-b' else {'ad817046664eef590433541ac976ba34','c64aede596ce549e1af12542affca4cc'}
        need(ids==expected and not absent,'sf7_replicate_claim_scope_changed')
    elif policy.parent_id in PUBLIC32_PARENTS:
        expected=PUBLIC32_OUTCOMES[policy.parent_id][2]
        need(ids==set(expected[unit]) and not absent,'public32_claim_scope_changed')
    elif policy.parent_id==RX_PRIORITY_BATCH_PAID:
        need(ids==set(RX_PRIORITY_BATCH_OUTCOMES[unit]) and not absent,'rx_priority_batch_claim_scope_changed')
    elif policy.parent_id==COMPLETED_BATCH_PAID:
        need(ids==set(COMPLETED_BATCH_OUTCOMES[unit]) and not absent,'completed_batch_claim_scope_changed')
    elif policy.parent_id==GFSK_BUNDLE_PAID:
        need(ids==set(GFSK_BUNDLE_OUTCOMES[unit]) and not absent,'completed_gfsk_bundle_claim_scope_changed')
    elif policy.parent_id==GFSK_COMPLETE_PAID:
        need(ids==set(GFSK_COMPLETE_OUTCOMES[unit]) and not absent,'completed_gfsk_claim_scope_changed')
    elif policy.parent_id==EARLY_RX_PARTIAL_PAID:
        need(ids==set(EARLY_RX_PARTIAL_OUTCOMES[unit]) and not absent,'early_rx_partial_claim_scope_changed')
    elif policy.parent_id==PARTIAL_BATCH_PAID:
        need(ids==set(PARTIAL_BATCH_OUTCOMES[unit]) and not absent, 'partial_batch_claim_scope_changed')
    elif policy.parent_id==SF5_COMPLETED_PAID:
        need(ids==set(SF5_COMPLETED_OUTCOMES[unit]) and not absent, 'completed_sf5_claim_scope_changed')
    elif policy.parent_id==SF5_STARTUP_PAID:
        need(ids==(set() if unit=='unit-b' else {SF5_STARTUP_RUN}) and not absent, 'sf5_startup_claim_scope_changed')
    elif policy.parent_id in PARTIAL_BUNDLE_SCOPES:
        need(ids==set(PARTIAL_BUNDLE_SCOPES[policy.parent_id][3][unit]) and not absent,'partial_bundle_claim_scope_changed')
    elif policy.parent_id==ENROLLED_ZERO_PAID:
        need(not ids and not absent and not claims,'enrolled_zero_child_scope_changed')
    else: need(not unstarted,'unexpected_old_unstarted_claim')


def qualify_public32_failure_scope(unit,parent_id,history):
    need(parent_id in PUBLIC32_PARENTS and unit in ('unit-a','unit-b','unit-c'),
         'unknown_public32_paid_parent')
    failed=PUBLIC32_OUTCOMES[parent_id][1]
    complete=parent_id==PUBLIC32_COMPLETE_PAID
    need(history.get('failed_started_runs')==([] if unit=='unit-b' or complete else [failed])
         and history['require_result_complete'] is complete,'public32_failure_scope_changed')


def qualify_public32_result(unit,run,result,*,parent_id=PUBLIC32_PAID):
    """Exact retained paid outcome; no new transport or global handoff authority."""
    need(parent_id in PUBLIC32_PARENTS,'unknown_public32_paid_parent')
    request,_,hashes,index=PUBLIC32_OUTCOMES[parent_id]
    policy_digest=OLD_DIGESTS[index]
    need(unit in hashes and run in hashes[unit]
         and sha(canonical(result))==hashes[unit][run]
         and result['complete'] is (parent_id==PUBLIC32_COMPLETE_PAID or run==request) and result['disarmed_on_exit'] is True
         and result['parent']['started'] is True and result['parent']['parent_id']==parent_id
         and result['parent']['policy_digest']==policy_digest and result['parent']['run_id']==run,
         'public32_retained_result_changed')


def qualify_bulk_receiver(row,result,raw):
    """One exact failed but durable receiver; never publisher acceptance."""
    need(row['run_id']==BULK_RECEIVER_RUN and row['transport']=='lora'
         and row['status']=='scoped_rows_observed' and row['wire']['retired']==1
         and not row['journal']['has_unknown_or_pending']
         and sha(raw)==row['files']['result.json']['sha256']==BULK_RECEIVER_RESULT_SHA
         and result['unit']=='unit-c' and result['session_id']==BULK_RECEIVER_RUN
         and result['complete'] is False and result['error']=='WireError:service_transfer_not_live'
         and result['sends'] is False and result['service_format']=='MR1.response'
         and result['parent']['parent_id']==BULK_PAID and result['parent']['started'] is True
         and result['disarmed_on_exit'] is True and not result['attempt_journal']['fenced']
         and result['service_object_durable'] is True and result['service_object_sha256']==BULK_OBJECT_SHA
         and result['object']['bytes']==16384 and result['object']['sha256']==BULK_OBJECT_SHA
         and result['flow']['complete'] is True and result['flow']['received_bytes']==16384
         and result['publisher_verified'] is False and not result.get('cleanup_errors'),
         'bulk_failed_durable_receiver_changed')

def qualify_mixed_receiver(row,result,raw):
    """Exact expired H7 receiver: durable wire object, application still failed."""
    need(row['run_id']==MIXED_RECEIVER_RUN and row['transport']=='lora'
         and row['status']=='scoped_rows_observed' and row['wire']['retired']==1
         and not row['journal']['has_unknown_or_pending']
         and sha(raw)==row['files']['result.json']['sha256']==MIXED_RECEIVER_RESULT_SHA
         and result['unit']=='unit-c' and result['session_id']==MIXED_RECEIVER_RUN
         and result['complete'] is False and result['error']=='WireError:bench_application_deadline'
         and result['sends'] is False and result['service_format']=='MR1.response'
         and result['parent']['parent_id']==MIXED_PAID and result['parent']['started'] is True
         and result['disarmed_on_exit'] is True and not result['attempt_journal']['fenced']
         and result['service_object_durable'] is True and result['service_object_sha256']==MIXED_OBJECT_SHA
         and result['object']['bytes']==1028 and result['object']['sha256']==MIXED_OBJECT_SHA
         and result['flow']['complete'] is True and result['flow']['received_bytes']==1028
         and result['publisher_verified'] is False and not result.get('cleanup_errors'),
         'mixed_failed_durable_receiver_changed')

def qualify_enrolled_zero_costs(unit,policy,observed,value):
    """One exact enrolled zero-child attempt: retain limits, never invent spent cost."""
    need(policy.parent_id==ENROLLED_ZERO_PAID and policy.digest==OLD_DIGESTS[19]
         and policy.qualification_kind=='c-bundle100k-v1' and unit in ENROLLED_ZERO_DATABASES,
         'unknown_enrolled_zero_parent')
    detail=observed['detail'];local=policy.unit(unit);actual=detail['prior_plus_prepaid_caps']
    need(observed['policy_expired_at_observation'] is True
         and all(type(observed[k]) is int for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
         and observed['observation_original_epoch_us']>=policy.expires_epoch_us
         and observed['observation_original_monotonic_us']>=detail['enrollment']['deadline_monotonic_us'],
         'enrolled_zero_original_clocks_not_expired')
    extra=Cost(2560,600000000) if unit in ('unit-a','unit-c') else Cost()
    need(all(type(detail[k]) is int and detail[k]==0 for k in
             ('claim_count','prepared_count','start_count','resource_count'))
         and detail['claims']==[] and detail['resources']==[]
         and detail['full_prepaid_caps']==asdict(Cost())
         and detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256']==value['owner']['history_binding_sha256']
         and observed['expected_database_sha256']==value['database_sha256']==ENROLLED_ZERO_DATABASES[unit]
         and observed['legacy_runs'] is None and observed['legacy_relation'] is None,
         'enrolled_zero_actual_rows_changed')
    need(actual==asdict(local.prior_debt)==value['actual_native_paid_caps']
         ==value['actual_controller_paid_caps'] and detail['limit']==asdict(local.limits)
         and local.limits==local.prior_debt.plus(extra)
         and value['conservative_unused_enrollment_caps']==asdict(extra)
         and value['paid_caps']==asdict(local.limits), 'enrolled_zero_conservative_bound_changed')
    need(value['runs']==value['retired_unstarted']==value['controller_endpoint_refs']==[]
         and value['preparation_sha256']=={} and value['legacy_sha256'] is None
         and value['require_result_complete'] is False and value['failed_started_runs']==[],
         'enrolled_zero_retention_scope_changed')




def qualify_partial_batch_costs(unit,policy,observed,value):
    """Retain all three samples' original caps once; only four grants were paid."""
    from .sf5_batch import BatchContext,KIND
    need(policy.parent_id==PARTIAL_BATCH_PAID and policy.digest==OLD_DIGESTS[28]
         and policy.qualification_kind==KIND and unit in PARTIAL_BATCH_DATABASES,'partial_batch_scope_changed')
    context=BatchContext.from_owner(value['owner'],policy);owner=value['owner'];detail=observed['detail'];local=policy.unit(unit)
    count=0 if unit=='unit-b' else 4;resources=0 if not count else 2
    actual_extra=Cost() if not count else Cost(1024,240000000);unused=Cost() if not count else Cost(512,120000000)
    need(observed['policy_expired_at_observation'] is True
         and all(type(observed[k]) is int for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
         and observed['observation_original_epoch_us']>=owner['phase_end_epoch_us']>=policy.expires_epoch_us
         and observed['observation_original_monotonic_us']>=owner['native_phase'][unit]['deadline_monotonic_us'],
         'partial_batch_original_clocks_live')
    need(all(type(detail[k]) is int and detail[k]==count for k in ('claim_count','prepared_count','start_count'))
         and type(detail['resource_count']) is int and detail['resource_count']==resources
         and detail['full_prepaid_caps']==asdict(actual_extra)
         and detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256']==owner['history_binding_sha256']
         and observed['expected_database_sha256']==value['database_sha256']==PARTIAL_BATCH_DATABASES[unit],
         'partial_batch_actual_rows_changed')
    need(detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']==value['actual_controller_paid_caps']
         ==asdict(local.prior_debt.plus(actual_extra)) and detail['limit']==asdict(local.limits)
         and value['paid_caps']==asdict(local.limits) and value['conservative_unused_enrollment_caps']==asdict(unused),
         'partial_batch_full_bound_changed')
    expected=set(PARTIAL_BATCH_OUTCOMES[unit])
    need(len(value['runs'])==len(value['controller_endpoint_refs'])==count
         and {r['run_id'] for r in value['runs']}==expected
         and {r['child']['run_id'] for r in value['controller_endpoint_refs']}==expected
         and set(value['preparation_sha256'])==expected and value['retired_unstarted']==[]
         and value['require_result_complete'] is False
         and value['failed_started_runs']==([] if not count else PARTIAL_BATCH_FAILED), 'partial_batch_retention_scope_changed')
    if count:
        expected_resources={r['resource_sha256'] for r in context.descriptor['samples'][:2]}
        need({r['resource_sha256'] for r in detail['resources']}==expected_resources
             and observed['legacy_scoped_rows_complete'] is True and len(observed['legacy_runs'])==count
             and observed['legacy_relation']['scoped_linkage_complete'] is True
             and not observed['legacy_relation']['native_claims_without_scoped_actuals']
             and all(r['wire']['retired']==1 and r['journal']['has_unknown_or_pending'] is False
                     and r['journal']['owner']['fenced'] is False for r in observed['legacy_runs']),
             'partial_batch_coverage_unsettled')
    else:need(observed['legacy_runs'] is None and observed['legacy_relation'] is None
              and value['legacy_sha256'] is None,'partial_batch_zero_peer_changed')
    context.check()



def qualify_early_rx_partial_result(unit,run,result):
    need(unit in EARLY_RX_PARTIAL_OUTCOMES and run in EARLY_RX_PARTIAL_OUTCOMES[unit]
         and sha(canonical(result))==EARLY_RX_PARTIAL_OUTCOMES[unit][run]
         and result['disarmed_on_exit'] is True and not result.get('cleanup_errors'),
         'early_rx_partial_outcome_changed')
    failed=run in EARLY_RX_PARTIAL_FAILED
    need(result['complete'] is (not failed),'early_rx_partial_outcome_scope_changed')
    if failed:
        wanted='WireError:bench_application_deadline' if unit=='unit-a' else 'WireError:reliable_flow_retry_ceiling'
        need(result.get('error')==wanted and result['flow']['complete'] is False
             and result['attempt_journal']['fenced'] is False,
             'early_rx_partial_failed_endpoint_changed')


def qualify_early_rx_partial_costs(unit,policy,observed,value):
    """Retain all three samples' original caps once; only four grants were paid."""
    from .sf5_batch import BatchContext,KIND
    need(policy.parent_id==EARLY_RX_PARTIAL_PAID and policy.digest==OLD_DIGESTS[31]
         and policy.qualification_kind==KIND and unit in EARLY_RX_PARTIAL_DATABASES,'early_rx_partial_scope_changed')
    context=BatchContext.from_owner(value['owner'],policy);owner=value['owner'];detail=observed['detail'];local=policy.unit(unit)
    count=0 if unit=='unit-b' else 4;resources=0 if not count else 2
    actual_extra=Cost() if not count else Cost(1024,240000000);unused=Cost() if not count else Cost(512,120000000)
    need(observed['policy_expired_at_observation'] is True
         and all(type(observed[k]) is int for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
         and observed['observation_original_epoch_us']>=owner['phase_end_epoch_us']>=policy.expires_epoch_us
         and observed['observation_original_monotonic_us']>=owner['native_phase'][unit]['deadline_monotonic_us'],
         'early_rx_partial_original_clocks_live')
    need(all(type(detail[k]) is int and detail[k]==count for k in ('claim_count','prepared_count','start_count'))
         and type(detail['resource_count']) is int and detail['resource_count']==resources
         and detail['full_prepaid_caps']==asdict(actual_extra)
         and detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256']==owner['history_binding_sha256']
         and observed['expected_database_sha256']==value['database_sha256']==EARLY_RX_PARTIAL_DATABASES[unit],
         'early_rx_partial_actual_rows_changed')
    need(detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']==value['actual_controller_paid_caps']
         ==asdict(local.prior_debt.plus(actual_extra)) and detail['limit']==asdict(local.limits)
         and value['paid_caps']==asdict(local.limits) and value['conservative_unused_enrollment_caps']==asdict(unused),
         'early_rx_partial_full_bound_changed')
    expected=set(EARLY_RX_PARTIAL_OUTCOMES[unit])
    need(len(value['runs'])==len(value['controller_endpoint_refs'])==count
         and {r['run_id'] for r in value['runs']}==expected
         and {r['child']['run_id'] for r in value['controller_endpoint_refs']}==expected
         and set(value['preparation_sha256'])==expected and value['retired_unstarted']==[]
         and value['require_result_complete'] is False
         and value['failed_started_runs']==([] if not count else EARLY_RX_PARTIAL_FAILED), 'early_rx_partial_retention_scope_changed')
    if count:
        expected_resources={r['resource_sha256'] for r in context.descriptor['samples'][:2]}
        need({r['resource_sha256'] for r in detail['resources']}==expected_resources
             and observed['legacy_scoped_rows_complete'] is True and len(observed['legacy_runs'])==count
             and observed['legacy_relation']['scoped_linkage_complete'] is True
             and not observed['legacy_relation']['native_claims_without_scoped_actuals']
             and all(r['wire']['retired']==1 and r['journal']['has_unknown_or_pending'] is False
                     and r['journal']['owner']['fenced'] is False for r in observed['legacy_runs']),
             'early_rx_partial_coverage_unsettled')
    else:need(observed['legacy_runs'] is None and observed['legacy_relation'] is None
              and value['legacy_sha256'] is None,'early_rx_partial_zero_peer_changed')
    context.check()


def qualify_completed_batch_costs(unit,policy,observed,value):
    """Retain the completed six-grant batch exactly once, without credit refunds."""
    from .sf5_batch import BatchContext,KIND
    need(policy.parent_id==COMPLETED_BATCH_PAID and policy.digest==OLD_DIGESTS[29]
         and policy.qualification_kind==KIND and unit in COMPLETED_BATCH_DATABASES,'completed_batch_scope_changed')
    context=BatchContext.from_owner(value['owner'],policy);owner=value['owner'];detail=observed['detail'];local=policy.unit(unit)
    count=0 if unit=='unit-b' else 6;resources=0 if not count else 3
    actual_extra=Cost() if not count else Cost(1536,360000000);unused=Cost()
    need(observed['policy_expired_at_observation'] is True
         and all(type(observed[k]) is int for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
         and observed['observation_original_epoch_us']>=owner['phase_end_epoch_us']>=policy.expires_epoch_us
         and observed['observation_original_monotonic_us']>=owner['native_phase'][unit]['deadline_monotonic_us'],
         'completed_batch_original_clocks_live')
    need(all(type(detail[k]) is int and detail[k]==count for k in ('claim_count','prepared_count','start_count'))
         and type(detail['resource_count']) is int and detail['resource_count']==resources
         and detail['full_prepaid_caps']==asdict(actual_extra)
         and detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256']==owner['history_binding_sha256']
         and observed['expected_database_sha256']==value['database_sha256']==COMPLETED_BATCH_DATABASES[unit],
         'completed_batch_actual_rows_changed')
    need(detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']==value['actual_controller_paid_caps']
         ==asdict(local.prior_debt.plus(actual_extra)) and detail['limit']==asdict(local.limits)
         and value['paid_caps']==asdict(local.limits) and value['conservative_unused_enrollment_caps']==asdict(unused),
         'completed_batch_full_bound_changed')
    expected=set(COMPLETED_BATCH_OUTCOMES[unit])
    need(len(value['runs'])==len(value['controller_endpoint_refs'])==count
         and {r['run_id'] for r in value['runs']}==expected
         and {r['child']['run_id'] for r in value['controller_endpoint_refs']}==expected
         and set(value['preparation_sha256'])==expected and value['retired_unstarted']==[]
         and value['require_result_complete'] is True
         and value['failed_started_runs']==[], 'completed_batch_retention_scope_changed')
    if count:
        expected_resources={r['resource_sha256'] for r in context.descriptor['samples']}
        need({r['resource_sha256'] for r in detail['resources']}==expected_resources
             and observed['legacy_scoped_rows_complete'] is True and len(observed['legacy_runs'])==count
             and observed['legacy_relation']['scoped_linkage_complete'] is True
             and not observed['legacy_relation']['native_claims_without_scoped_actuals']
             and all(r['wire']['retired']==1 and r['journal']['has_unknown_or_pending'] is False
                     and r['journal']['owner']['fenced'] is False for r in observed['legacy_runs']),
             'completed_batch_coverage_unsettled')
    else:need(observed['legacy_runs'] is None and observed['legacy_relation'] is None
              and value['legacy_sha256'] is None,'completed_batch_zero_peer_changed')
    context.check()

def qualify_rx_priority_batch_costs(unit,policy,observed,value):
    """Retain the completed six-grant batch exactly once, without credit refunds."""
    from .sf5_batch import BatchContext,KIND
    need(policy.parent_id==RX_PRIORITY_BATCH_PAID and policy.digest==OLD_DIGESTS[30]
         and policy.qualification_kind==KIND and unit in RX_PRIORITY_BATCH_DATABASES,'rx_priority_batch_scope_changed')
    context=BatchContext.from_owner(value['owner'],policy);owner=value['owner'];detail=observed['detail'];local=policy.unit(unit)
    count=0 if unit=='unit-b' else 6;resources=0 if not count else 3
    actual_extra=Cost() if not count else Cost(1536,360000000);unused=Cost()
    need(observed['policy_expired_at_observation'] is True
         and all(type(observed[k]) is int for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
         and observed['observation_original_epoch_us']>=owner['phase_end_epoch_us']>=policy.expires_epoch_us
         and observed['observation_original_monotonic_us']>=owner['native_phase'][unit]['deadline_monotonic_us'],
         'rx_priority_batch_original_clocks_live')
    need(all(type(detail[k]) is int and detail[k]==count for k in ('claim_count','prepared_count','start_count'))
         and type(detail['resource_count']) is int and detail['resource_count']==resources
         and detail['full_prepaid_caps']==asdict(actual_extra)
         and detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256']==owner['history_binding_sha256']
         and observed['expected_database_sha256']==value['database_sha256']==RX_PRIORITY_BATCH_DATABASES[unit],
         'rx_priority_batch_actual_rows_changed')
    need(detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']==value['actual_controller_paid_caps']
         ==asdict(local.prior_debt.plus(actual_extra)) and detail['limit']==asdict(local.limits)
         and value['paid_caps']==asdict(local.limits) and value['conservative_unused_enrollment_caps']==asdict(unused),
         'rx_priority_batch_full_bound_changed')
    expected=set(RX_PRIORITY_BATCH_OUTCOMES[unit])
    need(len(value['runs'])==len(value['controller_endpoint_refs'])==count
         and {r['run_id'] for r in value['runs']}==expected
         and {r['child']['run_id'] for r in value['controller_endpoint_refs']}==expected
         and set(value['preparation_sha256'])==expected and value['retired_unstarted']==[]
         and value['require_result_complete'] is True
         and value['failed_started_runs']==[], 'rx_priority_batch_retention_scope_changed')
    if count:
        expected_resources={r['resource_sha256'] for r in context.descriptor['samples']}
        need({r['resource_sha256'] for r in detail['resources']}==expected_resources
             and observed['legacy_scoped_rows_complete'] is True and len(observed['legacy_runs'])==count
             and observed['legacy_relation']['scoped_linkage_complete'] is True
             and not observed['legacy_relation']['native_claims_without_scoped_actuals']
             and all(r['wire']['retired']==1 and r['journal']['has_unknown_or_pending'] is False
                     and r['journal']['owner']['fenced'] is False for r in observed['legacy_runs']),
             'rx_priority_batch_coverage_unsettled')
    else:need(observed['legacy_runs'] is None and observed['legacy_relation'] is None
              and value['legacy_sha256'] is None,'rx_priority_batch_zero_peer_changed')
    context.check()




def qualify_gfsk_bundle_costs(unit,policy,observed,value):
    """Exact completed e8aa ten-grant debt and original expired scopes."""
    from .gfsk_bundle_history import retained_context,require_final_bindings
    require_final_bindings()
    need(policy.parent_id==GFSK_BUNDLE_PAID and policy.digest==OLD_DIGESTS[33]
         and policy.qualification_kind==GFSK_BUNDLE_KIND,'completed_gfsk_bundle_scope_changed')
    context=retained_context(value['owner'],policy,unit=unit,observation=observed)
    detail=observed['detail'];local=policy.unit(unit);count=0 if unit=='unit-b' else 10
    extra=Cost() if not count else Cost(2560,600000000)
    need(all(type(detail[k]) is int and detail[k]==count for k in ('claim_count','prepared_count','start_count'))
         and type(detail['resource_count']) is int and detail['resource_count']==(5 if count else 0)
         and detail['full_prepaid_caps']==asdict(extra) and detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256']==value['owner']['history_binding_sha256']
         and observed['expected_database_sha256']==value['database_sha256']==GFSK_BUNDLE_DATABASES[unit],
         'completed_gfsk_bundle_actual_rows_changed')
    need(detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']==value['actual_controller_paid_caps']
         ==value['paid_caps']==detail['limit']==asdict(local.limits)
         and local.limits==local.prior_debt.plus(extra)
         and value['conservative_unused_enrollment_caps']==asdict(Cost()),'completed_gfsk_bundle_caps_changed')
    expected=set(GFSK_BUNDLE_OUTCOMES[unit])
    need(len(value['runs'])==len(value['controller_endpoint_refs'])==count
         and {r['run_id'] for r in value['runs']}==expected
         and {r['child']['run_id'] for r in value['controller_endpoint_refs']}==expected
         and set(value['preparation_sha256'])==expected and value['retired_unstarted']==[]
         and value['require_result_complete'] is True and value['failed_started_runs']==[],
         'completed_gfsk_bundle_endpoint_scope_changed')
    if count:
        stages=context.value['stages'];manifest=context.value['manifest_sha256']
        resources={(r['campaign_id'],r['resource_sha256']):r for r in detail['resources']}
        need(len(resources)==5 and set(resources)=={(s['campaign_id'],manifest) for s in stages},
             'completed_gfsk_bundle_resources_changed')
        for stage in stages:
            row=resources[stage['campaign_id'],manifest]
            need(row['application_expires_epoch_us']==stage['expires_epoch_us']
                 and row['bundle_context_sha256']==policy.bundle_context_sha256,
                 'completed_gfsk_bundle_original_resource_changed')
        need(observed['legacy_scoped_rows_complete'] is True and len(observed['legacy_runs'])==count
             and observed['legacy_relation']['scoped_linkage_complete'] is True
             and not observed['legacy_relation']['native_claims_without_scoped_actuals']
             and all(row['wire']['retired']==1 and row['journal']['has_unknown_or_pending'] is False
                     and row['journal']['owner']['fenced'] is False for row in observed['legacy_runs'])
             and value['legacy_sha256']==sha(canonical(observed['legacy_runs'])),
             'completed_gfsk_bundle_coverage_unsettled')
    else:need(observed['legacy_runs'] is None and observed['legacy_relation'] is None and value['legacy_sha256'] is None,
              'completed_gfsk_bundle_zero_peer_changed')
    context._check()


def qualify_gfsk_complete_costs(unit,policy,observed,value):
    """Retain the completed six-grant batch exactly once, without credit refunds."""
    from .gfsk_batch import BatchContext,KIND
    need(policy.parent_id==GFSK_COMPLETE_PAID and policy.digest==OLD_DIGESTS[32]
         and policy.qualification_kind==KIND and unit in GFSK_COMPLETE_DATABASES,'completed_gfsk_scope_changed')
    context=BatchContext._from_retained_owner(value['owner'],policy,unit=unit,observation=observed);owner=value['owner'];detail=observed['detail'];local=policy.unit(unit)
    count=0 if unit=='unit-b' else 6;resources=0 if not count else 3
    actual_extra=Cost() if not count else Cost(1536,360000000);unused=Cost()
    need(observed['policy_expired_at_observation'] is True
         and all(type(observed[k]) is int for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
         and observed['observation_original_epoch_us']>=owner['phase_end_epoch_us']>=policy.expires_epoch_us
         and observed['observation_original_monotonic_us']>=owner['native_phase'][unit]['deadline_monotonic_us'],
         'completed_gfsk_original_clocks_live')
    need(all(type(detail[k]) is int and detail[k]==count for k in ('claim_count','prepared_count','start_count'))
         and type(detail['resource_count']) is int and detail['resource_count']==resources
         and detail['full_prepaid_caps']==asdict(actual_extra)
         and detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256']==owner['history_binding_sha256']
         and observed['expected_database_sha256']==value['database_sha256']==GFSK_COMPLETE_DATABASES[unit],
         'completed_gfsk_actual_rows_changed')
    need(detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']==value['actual_controller_paid_caps']
         ==asdict(local.prior_debt.plus(actual_extra)) and detail['limit']==asdict(local.limits)
         and value['paid_caps']==asdict(local.limits) and value['conservative_unused_enrollment_caps']==asdict(unused),
         'completed_gfsk_full_bound_changed')
    expected=set(GFSK_COMPLETE_OUTCOMES[unit])
    need(len(value['runs'])==len(value['controller_endpoint_refs'])==count
         and {r['run_id'] for r in value['runs']}==expected
         and {r['child']['run_id'] for r in value['controller_endpoint_refs']}==expected
         and set(value['preparation_sha256'])==expected and value['retired_unstarted']==[]
         and value['require_result_complete'] is True
         and value['failed_started_runs']==[], 'completed_gfsk_retention_scope_changed')
    if count:
        expected_resources={r['resource_sha256'] for r in context.descriptor['samples']}
        need({r['resource_sha256'] for r in detail['resources']}==expected_resources
             and observed['legacy_scoped_rows_complete'] is True and len(observed['legacy_runs'])==count
             and observed['legacy_relation']['scoped_linkage_complete'] is True
             and not observed['legacy_relation']['native_claims_without_scoped_actuals']
             and all(r['wire']['retired']==1 and r['journal']['has_unknown_or_pending'] is False
                     and r['journal']['owner']['fenced'] is False for r in observed['legacy_runs']),
             'completed_gfsk_coverage_unsettled')
    else:need(observed['legacy_runs'] is None and observed['legacy_relation'] is None
              and value['legacy_sha256'] is None,'completed_gfsk_zero_peer_changed')
    context.check()



def qualify_sf5_startup_result(unit,run,result):
    """Exact failed first SF5 request; no useful DATA or complete resource."""
    need(unit in SF5_STARTUP_OUTCOMES and run in SF5_STARTUP_OUTCOMES[unit]
         and sha(canonical(result))==SF5_STARTUP_OUTCOMES[unit][run]
         and result['complete'] is False and result['disarmed_on_exit'] is True
         and result['parent']['started'] is True and result['parent']['parent_id']==SF5_STARTUP_PAID
         and result['parent']['policy_digest']==OLD_DIGESTS[26] and result['parent']['run_id']==SF5_STARTUP_RUN,
         'sf5_startup_result_changed')
    need(result['error']==('WireError:reliable_flow_startup_grant_absent' if unit=='unit-a'
                           else 'WireError:reliable_flow_startup_grant_unanswered')
         and result['attempt_journal']==dict(attempts=0 if unit=='unit-a' else 8,fenced=False,recovered_unknown=0,
             reserved_estimated_airtime_us=0 if unit=='unit-a' else 103888)
         and result['flow']['complete'] is False and result['flow']['received_bytes']==0
         and result['flow']['acked_frames']==0, 'sf5_startup_attempts_changed')


def qualify_sf5_startup_costs(unit,policy,observed,value):
    """Keep the full two-link bound once, separate from actual first prepayment."""
    need(policy.parent_id==SF5_STARTUP_PAID and policy.digest==OLD_DIGESTS[26]
         and policy.qualification_kind is None and unit in SF5_STARTUP_DATABASES,
         'sf5_startup_scope_changed')
    owner=value['owner'];phase=owner['native_phase'][unit];detail=observed['detail'];local=policy.unit(unit)
    profile=RadioProfile(**owner['rf_profile'])
    need(owner['qualification_kind']=='c-bulk-8k-v1' and owner['radio_profile_name']=='sf5-500'
         and asdict(profile)==dict(frequency_hz=921375000,bandwidth_hz=500000,spreading_factor=5,coding_rate=5,
             chip_power_dbm=0,preamble=8,sync_word=0x42,lease_ms=90000,max_boot_attempts=256,
             max_boot_estimated_airtime_us=60000000), 'sf5_startup_original_profile_changed')
    count=0 if unit=='unit-b' else 1;actual_extra=Cost() if not count else Cost(256,60000000)
    full_extra=Cost() if not count else Cost(512,120000000);unused=actual_extra
    expected=set(SF5_STARTUP_OUTCOMES[unit])
    need(observed['policy_expired_at_observation'] is True
         and all(type(observed[k]) is int for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
         and type(owner['phase_end_epoch_us']) is int and type(phase['deadline_monotonic_us']) is int
         and observed['observation_original_epoch_us']>=owner['phase_end_epoch_us']>=policy.expires_epoch_us
         and observed['observation_original_monotonic_us']>=phase['deadline_monotonic_us']
         >=detail['enrollment']['deadline_monotonic_us'], 'sf5_startup_original_clocks_live')
    need(all(type(detail[k]) is int and detail[k]==count for k in ('claim_count','prepared_count','start_count','resource_count'))
         and len(detail['claims'])==len(detail['resources'])==count
         and detail['full_prepaid_caps']==asdict(actual_extra)
         and detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256']==owner['history_binding_sha256']
         and observed['expected_database_sha256']==value['database_sha256']==SF5_STARTUP_DATABASES[unit],
         'sf5_startup_actual_rows_changed')
    need(detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']==value['actual_controller_paid_caps']
         ==asdict(local.prior_debt.plus(actual_extra)) and detail['limit']==asdict(local.limits)
         and local.limits==local.prior_debt.plus(full_extra) and value['paid_caps']==asdict(local.limits)
         and value['conservative_unused_enrollment_caps']==asdict(unused), 'sf5_startup_conservative_bound_changed')
    need(len(value['runs'])==len(value['controller_endpoint_refs'])==count
         and {r['run_id'] for r in value['runs']}==expected
         and {r['child']['run_id'] for r in value['controller_endpoint_refs']}==expected
         and set(value['preparation_sha256'])==expected and value['retired_unstarted']==[]
         and value['require_result_complete'] is False
         and value['failed_started_runs']==([] if not count else [SF5_STARTUP_RUN]), 'sf5_startup_retention_scope_changed')
    if count:
        rows=observed['legacy_runs'];relation=observed['legacy_relation']
        need(len(rows)==count and observed['legacy_scoped_rows_complete'] is True
             and relation['scoped_linkage_complete'] is True and not relation['native_claims_without_scoped_actuals']
             and len(relation['links'])==count and all(r['relation']=='same_endpoint_allocation'
                 and r['no_refund'] is True and r['separate_debt_sum_permitted'] is False for r in relation['links']),
             'sf5_startup_actual_coverage_missing')
        need(all(r['status']=='scoped_rows_observed' and r['wire']['retired']==1
                 and r['journal']['has_unknown_or_pending'] is False and r['journal']['owner']['fenced'] is False
                 for r in rows), 'sf5_startup_journal_unsettled')
        actual=Cost() if unit=='unit-a' else Cost(8,103888)
        need(rows[0]['actual_cost']==rows[0]['journal']['actual_cost']==asdict(actual)
             and len(rows[0]['journal']['attempts'])==(0 if unit=='unit-a' else 8)
             and all(a['state']=='RF_DONE' for a in rows[0]['journal']['attempts']), 'sf5_startup_actual_attempts_changed')
    else:need(observed['legacy_runs'] is None and observed['legacy_relation'] is None
              and value['legacy_sha256'] is None, 'sf5_startup_zero_peer_changed')


def qualify_partial_bundle_result(unit,run,result,*,parent_id=PARTIAL_BUNDLE_PAID):
    """Two exact completed stage runs; whole file/checkpoint remain unproved."""
    need(parent_id in PARTIAL_BUNDLE_SCOPES,'unknown_partial_bundle_result_parent')
    index,_,_,outcomes=PARTIAL_BUNDLE_SCOPES[parent_id]
    need(unit in outcomes and run in outcomes[unit] and sha(canonical(result))==outcomes[unit][run]
         and result['complete'] is (parent_id!=V2_FAILED_BUNDLE_PAID or run==V2_FAILED_BUNDLE_RUNS[0]) and result['disarmed_on_exit'] is True
         and result['parent']['started'] is True and result['parent']['parent_id']==parent_id
         and result['parent']['policy_digest']==OLD_DIGESTS[index] and result['parent']['run_id']==run,
         'partial_bundle_retained_result_changed')
    if parent_id==V2_FAILED_BUNDLE_PAID and run==V2_FAILED_BUNDLE_RUNS[1]:
        need(result['error']==('HardwareError:radio_authority_expired' if unit=='unit-a' else 'WireError:reliable_flow_startup_grant_absent')
             and result['attempt_journal']==dict(attempts=0,fenced=False,recovered_unknown=0,reserved_estimated_airtime_us=0)
             and (unit!='unit-a' or result.get('flow') is None),'failed_bundle_start_shape_changed')


def qualify_partial_bundle_costs(unit,policy,observed,value):
    """Full original five-part bound once; actual two prepaid endpoints separate."""
    need(policy.parent_id in PARTIAL_BUNDLE_SCOPES,'unknown_partial_bundle_parent')
    index,kind,databases,outcomes=PARTIAL_BUNDLE_SCOPES[policy.parent_id]
    need(policy.digest==OLD_DIGESTS[index] and policy.qualification_kind==kind and unit in databases,
         'unknown_partial_bundle_parent')
    detail=observed['detail'];local=policy.unit(unit);count=0 if unit=='unit-b' else 2
    actual_extra=Cost() if not count else Cost(512,120000000)
    full_extra=Cost() if not count else Cost(2560,600000000)
    unused=Cost() if not count else Cost(2048,480000000)
    actual=asdict(local.prior_debt.plus(actual_extra))
    need(observed['policy_expired_at_observation'] is True
         and all(type(observed[k]) is int for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
         and observed['observation_original_epoch_us']>=policy.expires_epoch_us
         and observed['observation_original_monotonic_us']>=detail['enrollment']['deadline_monotonic_us'],
         'partial_bundle_original_clocks_not_expired')
    if kind in ('c-bundle100k-v2','c-bundle100k-v3'):
        owner=value['owner'];phase=owner['native_phase'][unit]
        need(type(owner['phase_end_epoch_us']) is int and type(phase['deadline_monotonic_us']) is int
             and observed['observation_original_epoch_us']>=owner['phase_end_epoch_us']
             and observed['observation_original_monotonic_us']>=phase['deadline_monotonic_us'],
             'v2_partial_bundle_original_phase_clocks_not_expired')
    need(all(type(detail[k]) is int and detail[k]==count for k in ('claim_count','prepared_count','start_count'))
         and type(detail['resource_count']) is int and detail['resource_count']==(1 if count else 0)
         and len(detail['claims'])==count and len(detail['resources'])==(1 if count else 0)
         and detail['full_prepaid_caps']==asdict(actual_extra)
         and detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256']==value['owner']['history_binding_sha256']
         and observed['expected_database_sha256']==value['database_sha256']==databases[unit],
         'partial_bundle_actual_rows_changed')
    need(detail['prior_plus_prepaid_caps']==value['actual_native_paid_caps']
         ==value['actual_controller_paid_caps']==actual and detail['limit']==asdict(local.limits)
         and local.limits==local.prior_debt.plus(full_extra)
         and value['conservative_unused_enrollment_caps']==asdict(unused)
         and value['paid_caps']==asdict(local.limits),'partial_bundle_conservative_bound_changed')
    expected=set(outcomes[unit])
    need(len(value['runs'])==count and {r['run_id'] for r in value['runs']}==expected
         and len(value['controller_endpoint_refs'])==count
         and {r['child']['run_id'] for r in value['controller_endpoint_refs']}==expected
         and set(value['preparation_sha256'])==expected and value['retired_unstarted']==[]
         and value['require_result_complete'] is (policy.parent_id!=V2_FAILED_BUNDLE_PAID)
         and value['failed_started_runs']==([V2_FAILED_BUNDLE_RUNS[1]] if policy.parent_id==V2_FAILED_BUNDLE_PAID and unit!='unit-b' else []),
         'partial_bundle_retention_scope_changed')
    if count:
        need(len(observed['legacy_runs'])==count and observed['legacy_scoped_rows_complete'] is True
             and observed['legacy_relation']['scoped_linkage_complete'] is True
             and not observed['legacy_relation']['native_claims_without_scoped_actuals'],
             'partial_bundle_actual_coverage_missing')
        if policy.parent_id==V2_FAILED_BUNDLE_PAID:
            failed=[row for row in observed['legacy_runs'] if row['run_id']==V2_FAILED_BUNDLE_RUNS[1]]
            need(len(failed)==1 and failed[0]['status']=='scoped_rows_observed' and failed[0]['wire']['retired']==1
                 and failed[0]['journal']['attempts']==[] and failed[0]['journal']['has_unknown_or_pending'] is False
                 and failed[0]['journal']['actual_cost']==asdict(Cost()) and failed[0]['actual_cost']==asdict(Cost()),
                 'failed_bundle_zero_actual_journal_changed')

    else:need(observed['legacy_runs'] is None and observed['legacy_relation'] is None
              and value['legacy_sha256'] is None,'partial_bundle_zero_peer_scope_changed')
    if policy.parent_id in V3_ACCEPTED_PARTS:
        qualify_v3_retained_part(unit,policy,observed,value)



def qualify_v3_retained_part(unit,policy,observed,value):
    """Fixed accepted first-part comparison; never fresh bundle authority."""
    receipt=value['accepted_part'];runs,receipt_sha=V3_ACCEPTED_PARTS[policy.parent_id]
    need(sha(canonical(receipt))==receipt_sha,'retained_v3_part_receipt_changed')
    from .pilot_bundle import BundleContext
    context=BundleContext(value['owner']['bundle_context'],policy)
    part=receipt['bundle_part'];stage=context.value['stages'][0];entry=context.expected['parts'][0]
    need(receipt['unit']=='unit-a' and receipt['publisher_verified'] is True and receipt['reused'] is False
         and receipt['manifest_sha256']==context.value['manifest_sha256'] and receipt['chunk_sha256']==entry['chunk_sha256']
         and receipt['campaign_id']==stage['campaign_id'] and receipt['request_run_id']==runs[0]
         and receipt['response_run_id']==runs[1]
         and part['bundle_context_sha256']==policy.bundle_context_sha256
         and part['part_index']==0 and part['useful_decoded_bytes']==entry['raw_bytes']==23900
         and part['reused_verified_part'] is False and part['whole_bytes']==100000
         and part['whole_sha256']==context.value['whole_sha256']
         and part['native_bundle_complete'] is False and part['native_transport_qualified'] is False,
         'retained_v3_part_binding_changed')
    resources=observed['detail']['resources']
    if unit=='unit-b':need(resources==[],'retained_v3_zero_peer_resource_changed')
    else:
        need(len(resources)==1 and resources[0]['campaign_id']==receipt['campaign_id']
             and resources[0]['resource_sha256']==receipt['manifest_sha256']
             and resources[0]['bundle_context_sha256']==part['bundle_context_sha256']
             and resources[0]['application_expires_epoch_us']==receipt['original_application_expires_epoch_us']
             and resources[0]['bundle_original_deadline_monotonic_us']==observed['detail']['enrollment']['deadline_monotonic_us']
             and (unit!='unit-a' or resources[0]['bundle_original_deadline_monotonic_us']==part['original_whole_deadline_monotonic_us']),
             'retained_v3_actual_resource_changed')


def verify_retained_v3_part_files(reader,unit,policy,observed,value):
    """Independently retained public first-part bytes; no active resource reopen."""
    pins=value['accepted_part_metadata_sha256']
    if unit!='unit-a':
        need(pins=={},'retained_v3_foreign_part_metadata');return
    from .pilot_bundle import BundleContext
    from meshticulum.resources import knowledge_pack as kp,lamplighter as ll,byte_bundle as bundle
    context=BundleContext(value['owner']['bundle_context'],policy);entry=context.expected['parts'][0]
    path=STATE/'campaigns'/policy.parent_id/'bundle';reader.directory(path);reader.directory(path/'parts')
    name=entry['chunk_sha256']+'.json'
    need(type(pins) is dict and set(pins)=={'context.json','parts/'+name}
         and all(type(v) is str and HEX.fullmatch(v) for v in pins.values()),'retained_v3_part_metadata_pins_changed')
    need(set(os.listdir(path))=={'context.json','parts'} and set(os.listdir(path/'parts'))=={name},'retained_v3_part_namespace_changed')
    raw=reader.read(path/'context.json',2048);saved=parse(raw);r=observed['detail']['resources'][0]
    expected=dict(schema=bundle.CONTEXT,manifest_sha256=context.value['manifest_sha256'],bundle_sha256=context.value['whole_sha256'],bundle_bytes=100000,host_boot_id=policy.unit(unit).host_boot_id,expires_epoch_us=context.value['expires_epoch_us'],original_epoch_us=r['bundle_original_epoch_us'],original_monotonic_us=r['bundle_original_monotonic_us'],deadline_monotonic_us=r['bundle_original_deadline_monotonic_us'])
    need(saved==expected and raw==canonical(saved) and sha(raw)==pins['context.json'],'retained_v3_part_original_context_changed')
    raw=reader.read(path/'parts'/name,32740)
    need(sha(raw)==pins['parts/'+name],'retained_v3_part_file_changed')
    record=ll.chunk(raw,context.manifest['chunks'][0]);kp.validate_pack_rules(context.manifest,[record]);part=record['bundle_part'];decoded=kp.decode_bundle_part(part)
    need(part['index']==0 and part['raw_bytes']==len(decoded)==23900 and kp.sha256_hex(decoded)==entry['raw_sha256']
         and part['bundle_sha256']==context.value['whole_sha256'] and part['bundle_bytes']==100000,'retained_v3_part_payload_changed')
    reader.check()


def qualify_partial_costs(unit,observed,value,native_refs):
    """Fixed historical conservative charge; never a grant or native claim."""
    actual=observed['detail']['prior_plus_prepaid_caps']
    need(actual==value['actual_native_paid_caps'] and sha(canonical(value['controller_endpoint_refs']))==PARTIAL_REFS_SHA[unit],
         'partial_actual_or_controller_proof_changed')
    need(native_refs==(value['controller_endpoint_refs'] if unit=='unit-c' else []), 'partial_native_endpoint_projection_changed')
    expected=Cost(256,60000000) if unit=='unit-a' else Cost()
    need(value['conservative_controller_only_caps']==asdict(expected)
         and value['paid_caps']==asdict(Cost(**actual).plus(expected)), 'partial_conservative_cost_changed')

def retired_partial_radio(reader,unit,policy,observed,row,now):
    need(unit=='unit-c' and row['run_id']==PARTIAL_RUN and row['transport']=='lora','partial_radio_scope_changed')
    claim=observed['detail']['claims'][0];target=STATE/'runs'/PARTIAL_RUN
    need(len(observed['detail']['claims'])==1 and claim['prepared'] is True and claim['started'] is False
         and claim['start_row_sha256'] is None,'partial_radio_start_present')
    reader.directory(target);need(set(os.listdir(target))=={'grant.json','active','provision.json','secret.bin','sender.bin'},
                                'partial_radio_runtime_namespace_changed')
    raw=reader.read(target/'grant.json',16384);plan=parse(raw)
    provision_raw=reader.read(target/'provision.json',32768);provision=parse(provision_raw);receipt=provision['parent']
    identity_raw=reader.read(STATE/'parents'/policy.parent_id/'prepared-identities'/(PARTIAL_RUN+'.json'),4096)
    identity=parse(identity_raw);sample=now()
    need(sha(raw)==row['grant_sha256']=='5eccb5834f02ebb8e452fcb7986d4f0a37388febbf999d55abfb2ef094fe6865'
         and sha(provision_raw)=='d2bdd53798dac8ce8ca8e40f0a8fc42c19789d751472be03efc247d18cec7fcf'
         and sha(identity_raw)==row['prepared_identity_sha256']=='a4e7600fe7c6fb4f6cfa87bfb8e7f0880376bb959b311b4b8e9533595a17df05'
         and plan['schema']=='meshticulum.service-run.v1' and plan['unit']==unit and plan['session_id']==PARTIAL_RUN
         and plan['host_boot_id']==policy.unit(unit).host_boot_id==boot() and plan['expires_epoch_us']<sample[0]
         and plan['application_deadline_monotonic_us']<sample[1] and plan['service']['format']=='MR1.request'
         and provision['run_id']==PARTIAL_RUN and provision['unit']==unit and provision['transport']=='lora'
         and receipt['parent_id']==PARTIAL_PAID and receipt['prepared'] is True and receipt['native_claim_id']==claim['native_claim_id']
         and sha(canonical(receipt))==claim['prepared_row_sha256'] and sha(canonical(plan))==claim['native_plan_sha256']
         and identity['directory_identity']==reader.directories[target] and identity['prepared_sha256']==sha(canonical(receipt))
         and identity['provision_sha256']==sha(provision_raw) and reader.read(target/'active',128,lock=True)==b'retired\n',
         'partial_radio_provision_or_identity_changed')
    reader.check();return dict(run_id=PARTIAL_RUN,prepared=True,started=False,retired=True,actual_cost=None,
        full_prepaid_caps=claim['child']['full_caps'],no_refund=True,separate_debt_sum_permitted=False,
        physical_quiescence_observed=False,live_terminal_ownership_observed=False,
        files={str((target/'grant.json').relative_to(STATE)):sha(raw),str((target/'provision.json').relative_to(STATE)):sha(provision_raw),
        str((STATE/'parents'/policy.parent_id/'prepared-identities'/(PARTIAL_RUN+'.json')).relative_to(STATE)):sha(identity_raw)})


def retired_unstarted(reader,unit,policy,observed,row,now):
    """Bind one actual paid/prepared/nonstarted retired TCP endpoint; cost unknown.

    No missing file becomes a zero measurement or process-terminal claim.
    The canonical collector must have read the native append-only start table.
    """
    if policy.parent_id==PARTIAL_PAID:return retired_partial_radio(reader,unit,policy,observed,row,now)
    run=row['run_id'];need(policy.parent_id==LATEST_PAID and unit in ('unit-b','unit-c')
         and run==RETIRED_UNSTARTED,'unexpected_retired_unstarted_scope')
    claims=[c for c in observed['detail']['claims'] if c['child']['run_id']==run]
    need(len(claims)==1 and claims[0]['prepared'] is True and claims[0]['started'] is False
         and claims[0]['start_row_sha256'] is None,'retired_unstarted_native_start_present')
    claim=claims[0];target=STATE/'runs'/run;files={}
    def public(path,maximum):
        raw=reader.read(path,maximum);files[str(path.relative_to(STATE))]=sha(raw);return raw
    grant_raw=public(target/'grant.json',16384);plan=parse(grant_raw)
    provision=parse(public(target/'provision.json',32768));receipt=provision['parent']
    need(_child_ref(receipt['child_caps'])==claim['child'],'retired_unstarted_child_projection_changed')
    meta=receipt['child_caps']['metadata']
    need(meta['transport']=='tcp' and plan['schema']=='meshticulum.ip-service-run.v1'
         and provision['schema']=='meshticulum.provisioned-public-pilot.v2'
         and provision['unit']==unit and provision['run_id']==run and provision['transport']=='tcp'
         and sha(grant_raw)==row['grant_sha256'] and sha(canonical(receipt))==claim['prepared_row_sha256']
         and receipt['native_claim_id']==claim['native_claim_id'] and receipt['child_caps']['cost']==claim['child']['full_caps']
         and receipt['run_id']==run and receipt['parent_id']==policy.parent_id
         and receipt['policy_digest']==policy.digest and receipt['unit']==unit
         and receipt['host_boot_id']==policy.unit(unit).host_boot_id==boot()
         and sha(canonical(plan))==receipt['native_plan_sha256']==claim['native_plan_sha256'],
         'retired_unstarted_provision_rebound')
    normalized=dict(plan,application_deadline_monotonic_us=None)
    need(sha(canonical(normalized))==meta['plan_sha256']==receipt['original_plan_sha256']
         and canonical(plan['limits'])==canonical(meta['profile'])
         and canonical({k:plan['object'][k] for k in ('bytes','sha256')})==canonical(meta['object'])
         and plan['session']['session_id']==run and plan['format']==meta['format']
         and canonical(plan['session']['initiator'])==canonical(asdict(policy.unit(meta['sender']).peer))
         and canonical(plan['session']['responder'])==canonical(asdict(policy.unit(meta['receiver']).peer))
         and canonical(plan['local_peer'])==canonical(asdict(policy.unit(unit).peer)),
         'retired_unstarted_plan_rebound')
    sampled=now();need(plan['expires_epoch_us']<sampled[0]
         and receipt['application_expires_epoch_us']<sampled[0]
         and receipt['application_deadline_monotonic_us']<sampled[1]
         and public(target/'active',128)==b'retired\n','retired_unstarted_live_or_not_retired')
    for name in os.listdir(target):
        need(name not in ('job.json','started','bytes.sqlite','wire.sqlite','attempts.sqlite')
             and not name.endswith(('-wal','-shm','-journal')),'retired_unstarted_runtime_record_present')
    identity_path=STATE/'parents'/policy.parent_id/'prepared-identities'/(run+'.json')
    identity_raw=public(identity_path,4096);identity=parse(identity_raw);reader.directory(target)
    need(sha(identity_raw)==row['prepared_identity_sha256'] and identity['run_id']==run
         and identity['parent_id']==policy.parent_id and identity['policy_digest']==policy.digest
         and identity['unit']==unit and identity['directory_identity']==reader.directories[target]
         and identity['prepared_sha256']==sha(canonical(receipt))
         and identity['provision_sha256']==files[str((target/'provision.json').relative_to(STATE))],
         'retired_unstarted_capture_rebound')
    reader.check();return dict(run_id=run,prepared=True,started=False,retired=True,actual_cost=None,
        full_prepaid_caps=claim['child']['full_caps'],no_refund=True,separate_debt_sum_permitted=False,
        physical_quiescence_observed=False,live_terminal_ownership_observed=False,files=files)


def seal_inputs(reader, unit, known, original, newer):
    """Reobserve complete verified metadata before any own namespace creation."""
    identities={STATE/'parents'/parent/'prepared-identities' for parent in GUARDED_PAID}
    paths=[STATE,STATE/'parents',STATE/'runs',*[STATE/'parents'/v for v in HISTORY],
           *[STATE/v for v in capture_paths(STATE,unit)['directories']],
           *sorted(identities),*[STATE/'runs'/v for v in sorted(newer)]]
    files,dirs={},{}
    for path in paths:
        reader.directory(path);rel=str(path.relative_to(STATE)) if path!=STATE else ''
        entries=sorted(os.listdir(path));dirs[rel]=entries
        if path in identities:
            expected={key[len(rel)+1:] for key in known if key.startswith(rel+'/')}
            need(set(entries)==expected and all(re.fullmatch(r'[0-9a-f]{32}\.json',name)
                 and int(name[:-5],16)!=0 for name in entries),'prepared_identity_namespace_changed_before_seal')
        for name in entries:
            if name in _FILES or path in identities:
                key=(rel+'/' if rel else '')+name;raw=reader.read(path/name,MAX_FILE)
                files[key]=sha(raw)
                need(key not in known or files[key]==known[key],'metadata_changed_before_seal')
    strict_known={key:pin for key,pin in known.items()
                  if not any(key.startswith('runs/'+run+'/') for run in original)}
    need(all(files.get(key)==pin for key,pin in strict_known.items()),'known_metadata_unsealed')
    reader.check()
    return files,dirs


def capture_request_commitment(p):
    """Pure pre-policy schedule validation; no DTO supplies admission."""
    from .pilot_bundle import COMMITTED_CONTRACTS, public_context_commitment
    if type(p.get('qualification_kind')) is str and p['qualification_kind'] in COMMITTED_CONTRACTS:
        commitment=public_context_commitment(p['bundle_context'],parent_id=p['parent_id'])
        need(commitment['qualification_kind']==p['qualification_kind'] and p['bundle_capture_commitment']==commitment
             and (p['phase_start_epoch_us'],p['phase_end_epoch_us'])
                ==(commitment['phase_start_epoch_us'],commitment['phase_end_epoch_us']),
             'v2_capture_commitment_not_reviewed')
        return commitment
    need(p.get('qualification_kind') in (None,'c-bulk32-v1','c-bundle100k-v1')
         and 'bundle_capture_commitment' not in p and 'bundle_context' not in p,
         'unknown_or_mismatched_capture_contract')
    return None


def capture_history(unit, p, runtime):
    parent = p['parent_id']; need(SID.fullmatch(parent) and parent not in HISTORY
                                 and parent not in CAPTURE_ONLY_PARENTS, 'invalid_own_parent')
    need(HEX.fullmatch(p['review_sha256']) and p['facts']['unit'] == unit, 'invalid_capture_review')
    commitment=capture_request_commitment(p)
    original_capture=canonical({k:p.get(k) for k in ('qualification_kind','bundle_context','bundle_capture_commitment',
        'parent_id','phase_start_epoch_us','phase_end_epoch_us')})
    first = clock(); phase = dict(epoch_us=first[0],monotonic_us=first[1],
        deadline_monotonic_us=first[1]+p['phase_end_epoch_us']-first[0])
    def guard():
        need(canonical({k:p.get(k) for k in ('qualification_kind','bundle_context','bundle_capture_commitment',
            'parent_id','phase_start_epoch_us','phase_end_epoch_us')})==original_capture,'capture_original_context_changed')
        runtime.check(); phase_check(unit,p['facts'],p['phase_start_epoch_us'],p['phase_end_epoch_us'],phase,commitment=commitment)
        return dict(phase_end_epoch_us=p['phase_end_epoch_us'])
    with Reader() as reader:
        known, original, newer, reports = full_history(reader,unit,p['history'],guard)
        files, dirs = seal_inputs(reader,unit,known,original,newer)
        reserve_descriptor_capacity(files,dirs,p['history']['legacy_inventory']['rows'])
        strict_raw, strict_pin = capture(STATE,owner_uid=0,expected_files=files,expected_directories=dirs)
        def exact(strict): strict.check(); reader.check(); guard(); return True
        with SealedHistory(STATE,owner_uid=0,seal_bytes=strict_raw,expected_sha256=strict_pin) as strict:
            legacy_raw, _ = legacy_capture(STATE/'runs',owner_uid=0,expected_rows=p['history']['legacy_inventory']['rows'],
                independently_verified=True,exact_namespace_gate=lambda:exact(strict),max_seconds=30)
            exact(strict)
        reader.check(); guard()
        with _Ancestry(STATE/'parents',0) as ancestry:
            need(sorted(os.listdir(ancestry.fds[-1])) == sorted(
                 historical_parent_ids(STATE,unit,clock,require_retained=True,require_unadmitted=True)), 'blind_capture_rerun_refused')
            os.mkdir(parent,0o700,dir_fd=ancestry.fds[-1]); os.fsync(ancestry.fds[-1]); ancestry.check()
        target = STATE/'parents'/parent
        _, binding_pin = install_seals(STATE,parent,unit,strict_raw,legacy_raw,clock=clock)
        record = dict(schema='meshticulum.private-resume-actual-capture.v1',unit=unit,parent_id=parent,
            history=p['history'],history_binding_sha256=binding_pin,facts=p['facts'],native_phase=phase,
            phase_start_epoch_us=p['phase_start_epoch_us'],phase_end_epoch_us=p['phase_end_epoch_us'],
            review_sha256=p['review_sha256'],reports=reports,actual_history_verified=True,
            original_unknown_actual_cost=None,refunds=0,global_debt_complete=False)
        if commitment is not None:
            from .pilot_bundle import COMMITTED_CONTRACTS
            record['schema']=COMMITTED_CONTRACTS[commitment['qualification_kind']][6]
            record['bundle_capture_commitment']=commitment
        raw = canonical(record); write_new(target,'capture.json',raw); runtime.check()
        return dict(actual_history_verified=True,history_binding_sha256=binding_pin,
            capture_record_sha256=sha(raw),native_phase=phase,original_unknown_actual_cost=None,refunds=0,
            full_caps=reports[-1]['paid_caps'],global_debt_complete=False)


def enrolled_action(action, unit, p, runtime):
    policy = ParentPolicy.from_dict(p['policy']); local = policy.unit(unit)
    owner, prior = p['owner'], p['prior']; target = STATE/'parents'/policy.parent_id
    need(policy.parent_id not in HISTORY and policy.parent_id not in CAPTURE_ONLY_PARENTS
         and owner['schema'] == 'meshticulum.finite-public-resume-owner.v1'
         and owner['policy_digest'] == policy.digest and owner['manifest_sha256'] == sha(
             runtime.reader.read(runtime.root/'manifest.json',65536)), 'wrong_own_parent_scope')
    need(runtime.root.name == 'runtime-'+owner['runtime_sha256'][:16]
         and HEX.fullmatch(owner['runtime_sha256']) and owner['review_sha256'] == p.get('review_sha256',owner['review_sha256']),
         'wrong_runtime_or_review_scope')
    with Reader() as reader:
        capture_raw = reader.read(target/'capture.json',2000000)
        need(sha(capture_raw) == owner['capture_record_sha256'], 'actual_capture_record_changed')
        saved = parse(capture_raw); binding = owner['history_binding_sha256']
        need(saved['unit'] == unit and saved['parent_id'] == policy.parent_id
             and saved['actual_history_verified'] is True and saved['history_binding_sha256'] == binding
             == prior['history_binding_sha256'] and saved['review_sha256'] == owner['review_sha256']
             and saved['facts'] == owner['facts'][unit] and saved['native_phase'] == owner['native_phase'][unit]
             and saved['phase_start_epoch_us'] == owner['phase_start_epoch_us']
             and saved['phase_end_epoch_us'] == owner['phase_end_epoch_us'], 'capture_scope_changed')
        need(prior['full_caps'] == saved['history']['previous'][-1]['paid_caps'] == asdict(local.prior_debt)
             and prior['legacy_inventory'] == saved['history']['legacy_inventory']
             and prior['complete_global_debt'] is False and prior['actual_cost'] is None and prior['refunds'] == 0,
             'own_prior_refund_or_debt_changed')
        from .pilot_bundle import validate_capture_commitment
        validate_capture_commitment(owner,saved,policy)
        authorization = owner['authorization']
        need(authorization['human_statement'] == 'run it bro' and authorization['count_authorized'] == 1
             and authorization['public_only'] is True and authorization['private_production'] is False,
             'wrong_reviewed_public_test_direction')
        need(owner['prior_registry'][unit]['observation_sha256'] == sha(canonical(prior)) == local.prior_snapshot_sha256
             and sha(canonical(owner['prior_registry'])) == policy.prior_snapshot_sha256, 'own_prior_registry_changed')
        need(policy.issued_epoch_us == owner['phase_start_epoch_us']
             and policy.expires_epoch_us == owner['phase_end_epoch_us']-5000000,
             'original_parent_window_changed')
        need((owner.get('qualification_kind') in PUBLIC32_KINDS) == (policy.qualification_kind in PUBLIC32_KINDS)
             and (policy.qualification_kind is None or owner.get('qualification_kind') == policy.qualification_kind),
             'public32_policy_kind_not_owner_bound')
        if policy.qualification_kind in BUNDLE_KINDS:
            from .pilot_bundle import BundleContext
            BundleContext(owner['bundle_context'],policy).current(clock())
        rf, rp, ip = owner['rf_record'], owner_radio_profile(owner, policy), IpLimits()
        need(owner['rf_record_sha256'] == '40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9'
             and rf['country'] == 'MY' and rf['C_transmission_allowed'] is True
             and rf['A_B_transmission_allowed'] is True and all(rf['units'][u]['antenna_connected'] is True
             for u in ('unit-a','unit-b','unit-c')) and profile_record(rp) == owner['rf_profile']
             and (owner.get('radio_modem')==GFSK_NAME or (rp.frequency_hz,rp.bandwidth_hz,rp.spreading_factor,rp.coding_rate,rp.chip_power_dbm,
                  rp.preamble,rp.sync_word) in ((921375000,500000,7,5,0,8,0x42),
                                               (921375000,500000,5,5,0,12,0x42))), 'owner_RF_basis_changed')
        radio = Cost(rp.max_boot_attempts,rp.max_boot_estimated_airtime_us)
        tcp = Cost(ip_frames=ip.max_frames,tx_stream_bytes=ip.max_tx_stream_bytes,
                   rx_stream_bytes=ip.max_rx_stream_bytes)
        extra = endpoint_budget(unit,owner.get('qualification_kind'),radio,tcp)
        need(local.limits == local.prior_debt.plus(extra),
             'resume_endpoint_caps_not_exact')
        owner_raw, prior_raw, policy_raw = canonical(owner), canonical(prior), canonical(policy.to_dict())
        last_clock = [clock()]
        checkpoint_actions=('checkpoint-audit','store-handoff','checkpoint')
        remote_check=None
        if action in checkpoint_actions:
            from . import bundle_checkpoint_adapter as adapter
            remote_check=adapter.remote_guard(policy,owner,unit,p,clock)
        def bounds():
            now = clock(); phase = saved['native_phase']
            need(owner['phase_start_epoch_us'] <= now[0] < owner['phase_end_epoch_us']
                 and phase['epoch_us'] <= now[0] and phase['monotonic_us'] <= now[1]
                 < phase['deadline_monotonic_us'] and all(a >= b for a,b in zip(now,last_clock[0]))
                 and boot() == local.host_boot_id, 'native_phase_expired_regressed_or_rebooted')
            last_clock[0] = now
            if remote_check is not None:remote_check()
            reader.check(); return True
        def current_facts():
            bounds(); value = stable(native_facts(unit)); bounds()
            need(value == owner['facts'][unit], 'native_service_lifetime_changed')
            need(value['host_boot_id'] == local.host_boot_id and local.peer.chain_id == 'meshticulum-public-pilot-v1'
                 and local.peer.agent_id == sha(('meshticulum-public-pilot-v1/'+unit+'/'+boot()).encode()), 'own_public_peer_changed')
            return NativeFacts(unit,value['host_boot_id'],PeerID(local.peer.chain_id,local.peer.agent_id))
        def records():
            bounds()
            with Reader() as check:
                need(check.read(target/'owner.json',200000) == owner_raw
                     and check.read(target/'prior.json',200000) == prior_raw
                     and check.read(target/'policy.json',20000) == policy_raw
                     and sha(check.read(target/'history-binding.json',16384)) == binding, 'own_parent_records_changed')
            return True
        def approve(raw):
            records(); value = parse(raw); e = value['enrollment']
            need(value['phase'] in ('enroll','current') and e['schema'] == 'meshticulum.native-parent-enrollment.v2'
                 and canonical(e['policy']) == canonical(policy.to_dict()) and e['history_binding_sha256'] == binding
                 and e['unit'] == unit and e['host_boot_id'] == local.host_boot_id
                 and owner['phase_start_epoch_us'] <= e['original_epoch_us'] < policy.expires_epoch_us
                 and e['deadline_monotonic_us'] == e['original_monotonic_us']+policy.expires_epoch_us-e['original_epoch_us']
                 and e['deadline_monotonic_us'] < saved['native_phase']['deadline_monotonic_us'], 'own_approval_binding_changed')
            records(); return True
        def history_gate():
            current_facts(); return dict(phase_end_epoch_us=owner['phase_end_epoch_us'])
        if action == 'enroll':
            for name, raw in (('owner.json',owner_raw),('prior.json',prior_raw),('policy.json',policy_raw)):
                write_new(target,name,raw)
            # Before enrollment there is no NativeCaps to reopen. Independently
            # recheck old typed rows and protected capture, retaining debt once.
            def initial_prior(raw):
                need(parse(raw) == dict(parent_id=policy.parent_id,policy_digest=policy.digest,
                     unit=asdict(local),prior_snapshot_sha256=policy.prior_snapshot_sha256), 'native_prior_callback_rebound')
                full_history(reader,unit,saved['history'],history_gate,own_parent=policy.parent_id)
                records(); return True
            caps = NativeCaps.enroll(target/'native.sqlite',policy,unit=unit,clock=clock,
                facts=current_facts,approve=approve,prior_check=initial_prior,history_binding_sha256=binding)
            try:
                need(caps.current() is True and caps.history_binding_sha256 == binding, 'own_enrollment_not_current')
                return dict(parent_id=policy.parent_id,policy_digest=policy.digest,snapshot=caps.snapshot())
            finally: need(caps.close() is True, 'own_enrollment_close_uncertain')
        records()
        strict=parse(reader.read(target/'history-identity-seal.json',2000000))
        legacy=parse(reader.read(target/'legacy-grant-seal.json',2000000))
        reserve_descriptor_capacity(strict['files'],strict['directories'],legacy['rows'])
        serial_owner=primitives=serial_close=None;checkpoint_result=None
        def bounded_runtime_check():
            bounds();value=runtime.check();bounds();return value
        if action in checkpoint_actions:
            from . import bundle_checkpoint_adapter as adapter
            adapter.context_for(unit,policy,owner,p['checkpoint_index'])
        try:
            if action=='checkpoint':
                # Retain a genuine low serial FD BEFORE large protected history
                # fences. Owner/records/policy were independently checked above.
                records();serial_owner,primitives=adapter.open_serial(unit,policy,owner,p['checkpoint_index'])
                from .bundle_radio_checkpoint import primitives_current,checkpoint
                serial_close=primitives_current(primitives)[4].__get__(serial_owner)
                records()
            with ResumeParentGuard(STATE,policy,unit,owner_sha256=sha(owner_raw),prior_sha256=sha(prior_raw),
                    binding_sha256=binding,preparation_sha256=p['preparation_sha256'],
                    management_phase='postterminal' if action=='audit' or action in checkpoint_actions else 'prelaunch',clock=clock,
                    facts=current_facts,approve=approve,runtime_check=bounded_runtime_check) as guard:
                need(guard.caps.history_binding_sha256 == binding, 'native_enrollment_history_tag_changed')
                snapshot = guard.caps.snapshot()
                if action=='checkpoint':
                    checkpoint_result=checkpoint(guard,serial_owner=serial_owner,primitives=primitives,
                        next_index=p['checkpoint_index'],handoff_sha256=p['handoff_sha256'])
        finally:
            if serial_close is not None:serial_close()
        if action == 'checkpoint':
            records();return dict(parent_id=policy.parent_id,policy_digest=policy.digest,snapshot=snapshot,
                checkpoint=checkpoint_result,arm_commands=0,tx_commands=0,native_transport_permission=False)
        if action == 'audit' or action in checkpoint_actions:
            full_history(reader,unit,saved['history'],history_gate,own_parent=policy.parent_id,
                         own_runs=tuple(p['preparation_sha256']))
            terminal = terminal_observation(reader,unit,policy,owner,p['preparation_sha256'],p.get('expected_cut_run'))
        else: terminal = None
        extra={}
        if action in ('checkpoint-audit','store-handoff'):
            children=adapter.terminal_children(reader,unit,policy,terminal)
            if action=='store-handoff':
                records();extra=adapter.store(reader,unit,policy,owner,p,terminal,children);records()
            else:extra=dict(terminal_children=children)
        records(); return dict(parent_id=policy.parent_id,policy_digest=policy.digest,snapshot=snapshot,
                               terminal_observation=terminal,**extra)


def terminal_observation(reader, unit, policy, owner, preparations, expected_cut_run=None):
    """Scoped actual endpoint linkage after exclusive native ownership ended."""
    runs = []
    for run in preparations:
        target = STATE/'runs'/run
        grant_raw = reader.read(target/'grant.json',16384); grant = parse(grant_raw)
        provision = parse(reader.read(target/'provision.json',32768))
        result = parse(reader.read(target/'result.json',300000))
        context = provision['parent']['grant_context_sha256']
        tcp = grant['schema'] == 'meshticulum.ip-service-run.v1'; binding = context
        need(result['parent']['parent_id'] == policy.parent_id and result['parent']['run_id'] == run
             and result['parent']['started'] is True, 'own_terminal_parent_rebound')
        if not tcp:
            profile=profile_from_plan(grant,board_id=1)
            if unit == 'unit-c':
                binding = sha(b'meshticulum.hat-attempts.v1\0'+bytes.fromhex(context)+boot().encode('ascii')+profile.encode(board_id=2))
            else:
                radio = result['radio']
                binding = sha(b'meshticulum.radio-attempts.v1\0'+bytes.fromhex(context)
                    +bytes.fromhex(radio['mcu_boot'])+bytes([1])+radio['firmware'].encode('ascii')+profile.encode(board_id=1))
            need(result['disarmed_on_exit'] is True, 'own_radio_exit_not_disarmed')
        runs.append(dict(run_id=run,host_boot_id=boot(),transport='tcp' if tcp else 'lora',
            grant_sha256=sha(grant_raw),grant_context_sha256=context,attempt_binding_sha256=binding))
    end = min(clock()[0]+35000000,owner['phase_end_epoch_us'])
    legacy = DebtScope.from_dict(dict(schema='meshticulum.public-debt-scope.v1',unit=unit,
        current_host_boot_id=boot(),runtime_sha256=owner['runtime_sha256'],manifest_sha256=owner['manifest_sha256'],
        observation_expires_epoch_us=end,runs=runs)) if runs else None
    database = reader.read(STATE/'parents'/policy.parent_id/'native.sqlite',MAX_FILE)
    observed = collect(STATE,Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,
        policy.parent_id,policy.digest,sha(database),unit,boot(),end),clock=clock,boot_reader=boot,legacy_scope=legacy).to_dict()
    need(observed['status'] == 'scoped_rows_observed' and observed['scoped_rows_complete']
         and not observed['pending_unknown'], 'own_terminal_parent_uncertain')
    qualify_terminal(observed,owner,preparations)
    if runs:
        relation = observed['legacy_relation']
        need(relation is not None and not relation['native_claims_without_scoped_actuals']
             and len(relation['links']) == len(runs) and all(r['status'] == 'scoped_rows_observed'
             and r['wire']['retired'] == 1
             for r in observed['legacy_runs']), 'own_terminal_journals_unsettled')
        for row in observed['legacy_runs']:
            if row['journal']['has_unknown_or_pending']:
                qualify_known_cut(reader,unit,policy,owner,observed,row,expected_cut_run)
    reader.check(); return observed


def qualify_terminal(observed, owner, preparations):
    detail = observed['detail']
    need(detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256'] == owner['history_binding_sha256']
         and detail['claim_count'] == detail['prepared_count'] == detail['start_count'] == len(preparations)
         and {c['child']['run_id'] for c in detail['claims']} == set(preparations), 'own_terminal_enrollment_or_claims_changed')
    if preparations:
        relation = observed['legacy_relation']
        need(relation is not None and relation['scoped_linkage_complete'] is True
             and not relation['native_claims_without_scoped_actuals'] and len(relation['links']) == len(preparations)
             and {r['run_id'] for r in relation['links']} == set(preparations)
             and all(r['relation'] == 'same_endpoint_allocation' and r['no_refund'] is True
             and r['separate_debt_sum_permitted'] is False for r in relation['links']), 'own_terminal_endpoint_relation_unqualified')


def qualify_cut_journal(journal):
    """Typed known-byte fence predicate; never clears or edits journal state."""
    j=journal['owner'];attempts=journal['attempts']
    need(journal['has_unknown_or_pending'] is True and j['fenced'] is True
         and type(j['rx_pending']) is int and j['rx_pending']==0
         and type(j['rx_unknown']) is int and j['rx_unknown']==0
         and type(j['rx_settled']) is int and j['rx_settled']==512
         and attempts and all(a['state']=='KERNEL_ACCEPTED' and a['lane']=='ACK'
             and type(a['written_bytes']) is int and a['written_bytes']==a['reserved_bytes']>0 for a in attempts),
         'fenced_cut_has_unsettled_bytes')
    return None


def qualify_known_cut(reader,unit,policy,owner,observed,row,expected_run):
    """Exact intentional receiver cutoff; original fenced flag stays TRUE.

    This is scoped retired evidence, not a repaired journal or reusable credit.
    All full prepaid caps remain charged; no missing/unknown byte is forgiven.
    """
    need(unit=='unit-c' and expected_run is not None and row['run_id']==expected_run
         and row['transport']=='tcp' and row['status']=='scoped_rows_observed'
         and row['wire']['retired']==1,'unexpected_fenced_cut_scope')
    run=row['run_id'];qualify_cut_journal(row['journal'])
    if policy.parent_id in GUARDED_PAID:
        attempts=row['journal']['attempts']
        need(run==KNOWN_CUTS[policy.parent_id] and len(attempts)==1 and attempts[0]['reserved_bytes']==74
             and attempts[0]['written_bytes']==74 and row['actual_cost']==dict(radio_attempts=0,
                 estimated_airtime_us=0,ip_frames=1,tx_stream_bytes=74,rx_stream_bytes=512),
             'historical_fenced_cut_cost_changed')
    target=STATE/'runs'/run;raw=reader.read(target/'result.json',300000);result=parse(raw)
    provision=parse(reader.read(target/'provision.json',32768));receipt=provision['parent']
    claims=[c for c in observed['detail']['claims'] if c['child']['run_id']==run]
    need(len(claims)==1 and claims[0]['started'] is True and claims[0]['prepared'] is True
         and sha(canonical(receipt))==claims[0]['prepared_row_sha256']
         and _child_ref(receipt['child_caps'])==claims[0]['child'],'fenced_cut_parent_projection_changed')
    meta=receipt['child_caps']['metadata'];loss=result['test_tcp_loss']
    need(sha(raw)==row['files']['result.json']['sha256'] and result['complete'] is False
         and result.get('error')=='IpError:explicit_public_pilot_tcp_loss'
         and loss['injected'] is True and type(loss['requested_rx_stream_bytes']) is int
         and type(loss['known_rx_stream_bytes']) is int
         and loss['requested_rx_stream_bytes']==loss['known_rx_stream_bytes']==512
         and result['byte_journal']['fenced'] is True and not result.get('service_object_durable')
         and result['session_id']==run and result['service_format']==meta['format']=='MR1.response'
         and meta['sends'] is False and meta['unit']==unit and meta['receiver']==unit
         and meta['sender']=='unit-b' and meta['transport']=='tcp' and meta['object']['bytes']>512
         and result['parent']==dict(receipt,schema='meshticulum.native-parent-start.v1',started=True)
         and row['admitted_caps']==row['conservative_unknown_caps']==claims[0]['child']['full_caps'],
         'fenced_cut_result_or_credit_changed')
    try: (target/'receiver.bin').lstat()
    except FileNotFoundError: pass
    else: raise RuntimeError('fenced_cut_receiver_body_exists')
    plan=parse(reader.read(target/'grant.json',16384))
    need(sha(canonical(plan))==receipt['native_plan_sha256'] and plan['format']=='MR1.response'
         and plan['session']['session_id']==run,'fenced_cut_plan_changed')
    from .pilot_ssh import INVOKE
    python=owner.get('native_python',{}).get(unit)
    if python is None and KNOWN_CUTS.get(policy.parent_id)==run: python='/usr/bin/python3'
    need(type(python) is str and python.startswith('/'),'fenced_cut_native_python_unbound')
    argv=[python,'-I','-B','-c',INVOKE,'/opt/meshticulum-bench/runtime-'+owner['runtime_sha256'][:16]+'/bench_node.py',
          owner['manifest_sha256'],'ip-service-run',unit,'--run-id',run,'--test-rx-cutoff','512']
    command=sha(b'\0'.join(a.encode() for a in argv)+b'\0')
    need(row['terminal_metadata']['command_sha256']==command
         and result['native_job']['command_sha256']==command,'fenced_cut_native_argv_changed')
    reader.check();return dict(run_id=run,classification='intentional_known_512_byte_receiver_cut',
                               fenced_preserved=True,refunds=0,full_caps_retained=True)


def inspect_paid_parent(unit,p,runtime):
    """Read-only typed rollover evidence, independently of expired admission.

    No enrollment/current constructor, radio, grant or ledger write occurs.
    Started membership comes from actual protected native rows, not input DTOs.
    Missing unstarted cost stays null and full prepaid debt remains charged.
    """
    policy=ParentPolicy.from_dict(p['policy']);owner=p['owner'];prior=p['prior'];local=policy.unit(unit)
    need(policy.parent_id in GUARDED_PAID and policy.digest==GUARDED_PAID[policy.parent_id]
         and local.host_boot_id==boot(),'wrong_paid_parent_scope')
    end=clock()[0]+35000000;target=STATE/'parents'/policy.parent_id
    with Reader() as reader:
        for name,value in (('policy.json',policy.to_dict()),('owner.json',owner),('prior.json',prior)):
            need(reader.read(target/name,200000)==canonical(value),'paid_parent_input_changed')
        sampled=clock();phase=owner['native_phase'][unit]
        need(sampled[0]>=owner['phase_end_epoch_us']>policy.expires_epoch_us
             and sampled[1]>=phase['deadline_monotonic_us'],'paid_parent_phase_not_expired')
        before=stable(native_facts(unit));database=reader.read(target/'native.sqlite',MAX_FILE)
        scope=Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,policy.parent_id,
                    policy.digest,sha(database),unit,boot(),end)
        initial=collect(STATE,scope,clock=clock,boot_reader=boot).to_dict()
        need(initial['status']=='scoped_rows_observed' and initial['scoped_rows_complete']
             and initial['policy_expired_at_observation'] and not initial['pending_unknown']
             and initial['detail']['approval_bytes_match_enrollment'] is True
             and initial['detail']['enrollment']['history_binding_sha256']==owner['history_binding_sha256'],
             'paid_parent_actual_rows_unavailable')
        latest_preparation_files(reader,unit,policy,initial,p['preparation_sha256'])
        started=[];unstarted=[]
        from .pilot_job import inspect as inspect_job
        for claim in initial['detail']['claims']:
            meta=claim['child'];run=meta['run_id'];child=STATE/'runs'/run
            grant_raw=reader.read(child/'grant.json',16384);grant=parse(grant_raw)
            provision=parse(reader.read(child/'provision.json',32768));context=provision['parent']['grant_context_sha256']
            row=dict(run_id=run,host_boot_id=boot(),transport=meta['transport'],grant_sha256=sha(grant_raw),
                     grant_context_sha256=context,attempt_binding_sha256=context)
            need(reader.read(child/'active',128,lock=True)==b'retired\n','paid_child_not_retired')
            if claim['started'] is False:
                row['prepared_identity_sha256']=p['preparation_sha256'][run];unstarted.append(row);continue
            status=inspect_job(child,unit=unit,run_id=run,boot=boot())
            need(status['job_record_found'] is True and status['live'] is False,'paid_started_job_not_terminal')
            result=parse(reader.read(child/'result.json',300000))
            if policy.parent_id in PUBLIC32_PARENTS:qualify_public32_result(unit,run,result,parent_id=policy.parent_id)
            if meta['transport']=='lora':
                profile=profile_from_plan(grant,board_id=1)
                if unit=='unit-c':binding=sha(b'meshticulum.hat-attempts.v1\0'+bytes.fromhex(context)+boot().encode('ascii')+profile.encode(board_id=2))
                else:
                    radio=result['radio'];binding=sha(b'meshticulum.radio-attempts.v1\0'+bytes.fromhex(context)
                        +bytes.fromhex(radio['mcu_boot'])+bytes([1])+radio['firmware'].encode('ascii')+profile.encode(board_id=1))
                need(result['disarmed_on_exit'] is True,'paid_radio_exit_unqualified')
                row['attempt_binding_sha256']=binding
            started.append(row)
        qualify_history_claims(policy,unit,initial,started,unstarted)
        legacy=DebtScope.from_dict(dict(schema='meshticulum.public-debt-scope.v1',unit=unit,
            current_host_boot_id=boot(),runtime_sha256=owner['runtime_sha256'],manifest_sha256=owner['manifest_sha256'],
            observation_expires_epoch_us=end,runs=started)) if started else None
        actual=collect(STATE,scope,clock=clock,boot_reader=boot,legacy_scope=legacy).to_dict()
        need(actual['status']=='scoped_rows_observed' and actual['scoped_rows_complete'] and not actual['pending_unknown']
             and actual['detail']==initial['detail'],'paid_parent_rows_changed_during_inspection')
        if policy.parent_id==BULK_PAID and unit=='unit-c':
            failed=next(row for row in actual['legacy_runs'] if row['run_id']==BULK_RECEIVER_RUN)
            raw=reader.read(STATE/'runs'/BULK_RECEIVER_RUN/'result.json',300000)
            qualify_bulk_receiver(failed,parse(raw),raw)
        if policy.parent_id==MIXED_PAID and unit=='unit-c':
            failed=next(row for row in actual['legacy_runs'] if row['run_id']==MIXED_RECEIVER_RUN)
            raw=reader.read(STATE/'runs'/MIXED_RECEIVER_RUN/'result.json',300000)
            qualify_mixed_receiver(failed,parse(raw),raw)
        unknown=[retired_unstarted(reader,unit,policy,actual,row,clock) for row in unstarted]
        reasons=[]
        if started:
            relation=actual['legacy_relation']
            qualified=(relation is not None and relation['scoped_linkage_complete'] is True
                 and len(relation['links'])==len(started) and {r['run_id'] for r in relation['native_claims_without_scoped_actuals']}
                     =={r['run_id'] for r in unstarted}
                 and all(r['status']=='scoped_rows_observed' and r['wire']['retired']==1
                         and r['journal']['has_unknown_or_pending'] is False for r in actual['legacy_runs']))
            if not qualified:
                try:
                    need(relation is not None and relation['scoped_linkage_complete'] is True
                         and len(relation['links'])==len(started)
                         and {r['run_id'] for r in relation['native_claims_without_scoped_actuals']}
                             =={r['run_id'] for r in unstarted},'paid_started_relation_unqualified')
                    for row in actual['legacy_runs']:
                        need(row['status']=='scoped_rows_observed' and row['wire']['retired']==1,
                             'paid_started_row_unqualified')
                        if row['journal']['has_unknown_or_pending']:
                            qualify_known_cut(reader,unit,policy,owner,actual,row,KNOWN_CUTS[policy.parent_id])
                except Exception as error: reasons.append(str(error))
        need(stable(native_facts(unit))==before,'paid_parent_inspection_facts_changed');reader.check();runtime.check()
        return dict(parent_id=policy.parent_id,policy_digest=policy.digest,observation=actual,
                    started_runs=started,retired_unstarted=unstarted,retired_unstarted_evidence=unknown,
                    eligible_for_rollover=False,scoped_retirement_checks_satisfied=not reasons,
                    rollover_refusal_reasons=reasons,
                    physical_handoff_proven=False,complete_global_debt=False,native_ledger_writes=False)


def response_metadata(action, unit, result):
    """Compose invariant envelope without rejecting matching writer metadata.

    Action results may repeat exact invariant fields; conflicting values or
    types are refused rather than silently overridden. A response is metadata,
    never native admission, capture provenance or transport qualification.
    """
    envelope = dict(schema=CONTRACT_SCHEMA,unit=unit,action=action,radio_opened=action=='checkpoint',
                    private_production=False,global_debt_complete=False)
    need(type(result) is dict and all(key not in result or
         (type(result[key]) is type(value) and result[key] == value)
         for key, value in envelope.items()), 'native_resume_result_invariant_conflict')
    return result | envelope


def cache_recipient(unit, payload):
    """C remains default; an A public recipient must be selected explicitly."""
    recipient=payload.get('recipient','unit-c')
    need(type(recipient) is str and recipient in ('unit-a','unit-c') and unit==recipient,
         'cache_observer_requires_selected_public_recipient')


def dispatch(action, unit, payload, *, runtime_root, manifest_sha256):
    """Actual root entry; import alone performs no native operation."""
    need(os.geteuid() == 0 and unit in ('unit-a','unit-b','unit-c')
         and action in ('capture','enroll','guard','audit','observe-cache','inspect-paid-parent','checkpoint-audit','store-handoff','checkpoint')
         and type(payload) is dict and len(canonical(payload)) <= 2000000, 'invalid_native_resume_RPC')
    with Runtime(runtime_root,manifest_sha256) as runtime:
        if action == 'capture': result = capture_history(unit,payload,runtime)
        elif action in ('enroll','guard','audit','checkpoint-audit','store-handoff','checkpoint'): result = enrolled_action(action,unit,payload,runtime)
        elif action=='inspect-paid-parent': result=inspect_paid_parent(unit,payload,runtime)
        else:
            cache_recipient(unit,payload)
            from .public_cache_observer import observe_native
            before = stable(native_facts(unit))
            raw = [base64.b64decode(payload[k],validate=True) for k in ('manifest','signature','trust')]
            result = dict(observation=observe_native(*raw,payload['pins'],tuple(payload['wanted'])))
            need(before == stable(native_facts(unit)), 'cache_observation_facts_changed')
        runtime.check()
    return response_metadata(action,unit,result)
