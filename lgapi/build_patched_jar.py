#!/usr/bin/env python3
"""One step: extract PageTextMapper.xml from YOUR original lg-api.jar, patch it, repack.

    python build_patched_jar.py <original lg-api.jar> <output lg-api.jar> [--keep-xml DIR]

Prints md5 of input/output jars and of the original/patched mapper so you can record them.
Refuses to run on a jar that is already patched (it would assert anyway).
--keep-xml DIR writes PageTextMapper.orig.xml / PageTextMapper.patched.xml into DIR for review
(keep DIR out of git: these files are MPIWG code, see README "Licensing").
"""
import hashlib
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patch_mapper import patch, VERSION  # noqa: E402
from repack_jar import repack, NAME       # noqa: E402


def md5(b):
    return hashlib.md5(b).hexdigest()


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    keep = None
    if '--keep-xml' in argv:
        keep = argv[argv.index('--keep-xml') + 1]
        args.remove(keep)
    if len(args) != 2:
        sys.exit(__doc__)
    src, dst = args
    orig = zipfile.ZipFile(src).read(NAME).decode('utf-8')
    if 'mt_pages' in orig:
        sys.exit('input jar already contains the Manticore patch; use the original (pre-manticore) jar')
    patched = patch(orig)
    repack(src, patched.encode('utf-8'), dst)
    if keep:
        os.makedirs(keep, exist_ok=True)
        for fn, txt in (('PageTextMapper.orig.xml', orig), ('PageTextMapper.patched.xml', patched)):
            with open(os.path.join(keep, fn), 'w', encoding='utf-8', newline='') as f:
                f.write(txt)
    with open(src, 'rb') as f:
        src_md5 = md5(f.read())
    with open(dst, 'rb') as f:
        dst_md5 = md5(f.read())
    print('patch', VERSION)
    print('input  jar md5', src_md5, '| mapper md5', md5(orig.encode('utf-8')))
    print('output jar md5', dst_md5, '| mapper md5', md5(patched.encode('utf-8')))


if __name__ == '__main__':
    main(sys.argv)
