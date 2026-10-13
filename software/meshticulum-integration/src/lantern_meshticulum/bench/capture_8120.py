"""Exact expired 8120 metadata; C never committed enrollment, A/B only captured.

Retain every observed file without adding a paid parent or changing old debt.
"""
import re
from .history_metadata_fence import _need

PARENT='8120145bab3fd1e6ac0115edbfe754ad'
REVIEW_SHA256='77d8b80672cdc453aa2aeebdcf5c0b7dc905af848d6402c453ea40f447d18a2b'
RESULT_SHA256='38f487b280e75d051a053eeb3536c717b76cd1ae6d800bc2d87687ffe5a0be70'
H42_RECEIPT_SHA256='68f7a680452b111ba6f826ff4566dd7a25e8299d7682e7979bd095730e87f489'
START=1791885222123632
END=1791886577123632
WHOLE_END=1791886572123632
ABSOLUTE_END=1791886673065294
EXPIRY_COMPLETION_SHA256='a996e21a8d632d4b26c2baf623a1ec8f981ebc66344de678352b0f3371de7aa2'
EMPTY_DATABASE_SHA256='352a9bb23c329c5a9bf235baf56917358b5b64b03f1a11d261fadae9c3cdb721'
EMPTY_SHA256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
OBSERVATION_SHA256={'unit-a': '1cd731e6b2bef940ed96521cef00ed40d6679d4350f871ff6a1fa916311d72e1', 'unit-b': 'e62ab1e8f7fe31ef8d7d621dcde26c2a729576bd056189dad8bc4ac5cc95b58e', 'unit-c': '48122f0c112bc8366a895e51e81c25ca294535dfe794d246816d8cb16c488493'}
UNITS={'unit-a': {'boot': '34b27358-8e12-44e7-a39f-410af5dbc491',
            'files': {'capture.json': '81a2aa89fc245efccb795023c2e97c80fdfa2fe028292bc3fddbcd160923aae2',
                      'history-binding.json': 'bbe8adc28e071888f6891172497f651029e09dc9bedf4d37b21f2eabbb4e505f',
                      'history-identity-seal.json': '078ecb706002df590c9f626860cd991918d49b18552f9a46841fdb00641dfce4',
                      'legacy-grant-seal.json': '0dfaff049fb4321872fc68c2b29460673805d72f728c8e3e8b3133e9a3c2082e'},
            'history_sha256': '40b1304283f2497e98e36c8ad852017b0e63462487c5a80cef3d29c24f13819b',
            'phase': {'deadline_monotonic_us': 345929831263,
                      'epoch_us': 1791885236236287,
                      'monotonic_us': 344588943918}},
 'unit-b': {'boot': 'dfe04f99-f765-45f0-aba7-27b8eaad902b',
            'files': {'capture.json': 'b48d9f7905767b971e4c1ae7cf35c6af5c3ee2c7cb4acc9105ea467f5d747aff',
                      'history-binding.json': '6011d4981c42d08128a7d5ea007e2fc64266d9ef8f95eb7392c40a697a4d57b1',
                      'history-identity-seal.json': '282c524e79ed752ce07c9c98eb3b3f07acf86b756c437a5990e02bba717d6609',
                      'legacy-grant-seal.json': '95539e6026afa5b68d01686e15c50661b2d3d1cca5079957d3c682e69f05859a'},
            'history_sha256': 'dc5fd99de5448b55b20464b0974ddf8c034d85e06c92b44062b2a87feb3ecc47',
            'phase': {'deadline_monotonic_us': 345880593780,
                      'epoch_us': 1791885236488197,
                      'monotonic_us': 344539958345}},
 'unit-c': {'boot': '21f9c178-6090-40d9-9359-ecf1c7b950b1',
            'files': {'capture.json': '32a6ba42a913c8d6699184e803d970e0978779303407750dce8cf037a9c8956c',
                      'history-binding.json': '603673cf57e488377fa180a8a3e0f25109c6482acd55d09deabc42a0b9a13d48',
                      'history-identity-seal.json': 'c5fe9c0b195663036f4222415c8d27537ed59bd9c3320d7e0affd15884cddb28',
                      'legacy-grant-seal.json': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc',
                      'native.sqlite': '352a9bb23c329c5a9bf235baf56917358b5b64b03f1a11d261fadae9c3cdb721',
                      'native.sqlite.approval': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                      'native.sqlite.owner': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                      'owner.json': '6be36dea809df15e4aa3a8eef073e5367539544c5e5ece23aa434790e5d0847d',
                      'policy.json': 'cff145fba6a5f1fda9bd3578bca3ad1415c47b09c1d3125c00865a4abaa60ee8',
                      'prior.json': 'f76927b7965eef2d041ca0a13049f0cd7a3144a851e583e5f373d1a8e0018687'},
            'history_sha256': '250beb10011b56dd689e1e3350c7212b3fbed069f476106755be7054c77c1b03',
            'phase': {'deadline_monotonic_us': 342869843352,
                      'epoch_us': 1791885236307395,
                      'monotonic_us': 341529027115}}}



def row(unit):
    _need(unit in ('unit-a','unit-b','unit-c'),'invalid_8120_unit')
    value=UNITS[unit];files=value['files']
    expected={'capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json'}
    if unit=='unit-c':
        expected|={'owner.json','prior.json','policy.json','native.sqlite','native.sqlite.owner','native.sqlite.approval'}
        _need(files.get('native.sqlite')==EMPTY_DATABASE_SHA256
            and files.get('native.sqlite.owner')==files.get('native.sqlite.approval')==EMPTY_SHA256,
            '8120_uncommitted_database_changed')
    _need(type(EXPIRY_COMPLETION_SHA256) is str and re.fullmatch('[0-9a-f]{64}',EXPIRY_COMPLETION_SHA256)
        and type(files) is dict and set(files)==expected
        and all(type(pin) is str and re.fullmatch('[0-9a-f]{64}',pin) for pin in files.values())
        and type(value['phase']) is dict,'pending_8120_original_expiry')
    return value
