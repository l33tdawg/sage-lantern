"""Build the opt-in offline lab targets from the repository's locked cache."""
import hashlib
from pathlib import Path
import subprocess
import tarfile

REPO=Path(__file__).resolve().parents[3]
ROOT=REPO/'work/resident-asr-bakeoff-20261002'
ARCHIVE=REPO/'software/cache/sources/whisper.cpp-v1.8.6.tar.gz'
MODEL=REPO/'software/cache/models/ggml-base.bin'
def check(path,wanted):
    if hashlib.sha256(path.read_bytes()).hexdigest()!=wanted:raise RuntimeError('locked_artifact_mismatch')
def main():
    check(ARCHIVE,'f8e632016ceae556f3132a16c7f704be1e7715595041f474fa81a2b64c1abf7c')
    check(MODEL,'60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe')
    ROOT.mkdir(exist_ok=True)
    src=ROOT/'upstream/whisper.cpp-1.8.6'
    if not src.exists():
        with tarfile.open(ARCHIVE) as archive:archive.extractall(ROOT/'upstream',filter='data')
    cli=src/'examples/cli/cli.cpp'
    check(cli,'f32bae642af030b0bf4abb18425532767b1b2ae0c33174e7ae741166df2b37f2')
    text=cli.read_text()
    start='            whisper_full_params wparams = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);'
    end='            whisper_print_user_data user_data'
    if text.count(start)!=1 or text.count(end)!=1:raise RuntimeError('parameter_block_ambiguous')
    generated=ROOT/'build/rook-generated';generated.mkdir(parents=True,exist_ok=True)
    (generated/'cli_parameters.inc').write_text(text[text.index(start):text.index(end)]+'\n')
    cmake=['cmake','-S',str(src),'-B',str(ROOT/'build'),'-DCMAKE_BUILD_TYPE=Release',
           '-DBUILD_SHARED_LIBS=OFF','-DWHISPER_BUILD_TESTS=OFF','-DWHISPER_BUILD_EXAMPLES=ON',
           '-DGGML_METAL=OFF','-DGGML_BLAS=OFF',
           '-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES='+str(Path(__file__).with_name('lab-target.cmake'))]
    subprocess.run(cmake,check=True)
    subprocess.run(['cmake','--build',str(ROOT/'build'),'--config','Release','--target',
                    'whisper-cli','rook-resident-whisper','-j','6'],check=True)
if __name__=='__main__':main()
