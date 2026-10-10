"""Build an isolated modified GEOS for DLL replacement validation, not release."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from native_compliance import digest

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a new test build directory')
    source = ROOT / '.verification/third-party-sources/geos-3.13.1.tar.bz2'
    if digest(source) != 'df2c50503295f325e7c8d7b783aca8ba4773919cde984193850cf9e361dfd28c':
        raise ValueError('Wrong GEOS source')
    args.output.mkdir(parents=True)
    with tarfile.open(source) as archive:
        archive.extractall(args.output / 'source', filter='data')
    tree = args.output / 'source/geos-3.13.1'
    cpp = tree / 'capi/geos_ts_c.cpp'
    text = cpp.read_text()
    original = 'snprintf(version, 256, "%s", GEOS_CAPI_VERSION);'
    assert original in text
    cpp.write_text(text.replace(original, 'snprintf(version, 256, "%s", GEOS_CAPI_VERSION "-seekdex-replacement-test");', 1))
    # GCC exposes an upstream missing export when building a Windows shared C API.
    header = tree / 'include/geos/algorithm/distance/DistanceToPoint.h'
    header.write_text(header.read_text().replace('#pragma once', '#pragma once\n\n// Modified for Seekdex isolated replacement test: export missing MinGW symbol.\n#include <geos/export.h>').replace('class DistanceToPoint {', 'class GEOS_DLL DistanceToPoint {'))
    cmake, compiler = shutil.which('cmake'), shutil.which('g++')
    if not cmake or not compiler:
        raise RuntimeError('Compatible CMake/MinGW compiler unavailable; build is BLOCKED')
    build = args.output / 'build'
    command = [cmake, '-S', str(tree), '-B', str(build), '-G', 'Ninja',
               '-DCMAKE_BUILD_TYPE=Release', '-DBUILD_SHARED_LIBS=ON', '-DBUILD_TESTING=OFF',
               '-DCMAKE_CXX_COMPILER=' + compiler, '-DCMAKE_SHARED_LINKER_FLAGS=-static-libgcc -static-libstdc++']
    with (args.output / 'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=log, check=True)
        subprocess.run([cmake, '--build', str(build), '--target', 'geos_c', '-j', '4'], stdout=log, stderr=log, check=True)
    result = {'original_source_sha256': digest(source), 'configure': command,
              'compiler': subprocess.check_output([compiler, '--version']).decode(),
              'modified_files': {str(p.relative_to(tree)): digest(p) for p in (cpp, header)},
              'dlls': {p.name: digest(p) for p in (build / 'bin').glob('*.dll')},
              'scope': 'Test only; never substituted into the public build by this script'}
    (args.output / 'build-evidence.json').write_text(json.dumps(result, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
