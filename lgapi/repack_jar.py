#!/usr/bin/env python3
"""Replace one entry of a Spring Boot fat jar, keeping everything else byte-identical.

    python repack_jar.py <src.jar> <new PageTextMapper.xml> <dst.jar>

- Keeps the launch-script prefix that "fully executable" Spring Boot jars carry before the
  zip data (needed when the jar is started as an init.d service via a symlink).
- Keeps every entry's compression method (Spring Boot requires nested jars to be STORED).
- Self-checks: same entry list, only the target entry differs, same compression, same prefix.
"""
import sys
import zipfile

NAME = 'BOOT-INF/classes/de/mpiwg/lgapi/mapper/gazetteer/PageTextMapper.xml'


def repack(src, newxml, dst, name=NAME):
    zin = zipfile.ZipFile(src)
    prefix_len = min(i.header_offset for i in zin.infolist())
    with open(src, 'rb') as f:
        prefix = f.read(prefix_len)
    with open(dst, 'wb') as out:
        out.write(prefix)
        with zipfile.ZipFile(out, 'w') as zout:
            for info in zin.infolist():
                data = newxml if info.filename == name else zin.read(info.filename)
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                zi.create_system = info.create_system
                zi.extra = info.extra
                zout.writestr(zi, data)
    # verify
    a, b = zipfile.ZipFile(src), zipfile.ZipFile(dst)
    assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()], 'entry list changed'
    diff = [i.filename for i in a.infolist() if a.read(i.filename) != b.read(i.filename)]
    ct = [i.filename for i in a.infolist()
          if a.getinfo(i.filename).compress_type != b.getinfo(i.filename).compress_type]
    with open(dst, 'rb') as f:
        prefix_ok = f.read(prefix_len) == prefix
    print('entries', len(a.infolist()), '| content differs:', diff, '| compress_type differs:', ct,
          '| prefix (%d bytes) kept:' % prefix_len, prefix_ok)
    assert diff in ([], [name]) and not ct and prefix_ok, 'self-check failed'


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    repack(sys.argv[1], open(sys.argv[2], 'rb').read(), sys.argv[3])
