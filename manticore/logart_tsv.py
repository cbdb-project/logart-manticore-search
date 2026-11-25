#!/usr/bin/env python3
"""Stream gazetteer.contents_myisam_ft as TSV for Manticore `tsvpipe`.

Output columns (order must match manticore.conf):  id <TAB> content <TAB> book_id <TAB> page_no

- Reads MySQL with `mysql --quick -N -B` (row streaming, constant memory).
- Undoes mysql batch-mode escaping (\\n, \\t, \\0, \\\\ ...) -> whitespace / literal.
- Inserts a space after each phrase-boundary punctuation char, because Manticore
  only honours `phrase_boundary` when the char is followed by a separator
  (CJK text normally has none). The stored text in MySQL is untouched; this only
  affects what goes into the index.

Backslashes are built with chr(92) on purpose (keeps the escaping unambiguous
when this file is copied around through shells and editors).

Credentials: a [client] option file, default /etc/manticoresearch/logart-mysql.cnf
(owner manticore, mode 600). Override with env LOGART_MYSQL_CNF.
"""
import os
import re
import subprocess
import sys

BS = chr(92)
TAB = chr(9)
NL = chr(10)

CNF = os.environ.get('LOGART_MYSQL_CNF', '/etc/manticoresearch/logart-mysql.cnf')
SQL = 'SELECT id, content, book_id, page_no FROM contents_myisam_ft'

UNESC = {'n': ' ', 't': ' ', 'r': ' ', '0': '', 'Z': '', BS: BS}
UNESC_RE = re.compile(re.escape(BS) + '(.)', re.S)

# Must stay identical to `phrase_boundary` in manticore.conf
BOUND = [0x3002, 0xFF0C, 0x3001, 0xFF1B, 0xFF1A, 0xFF01, 0xFF1F, 0x300C, 0x300D,
         0x300E, 0x300F, 0xFF08, 0xFF09, 0x300A, 0x300B, 0x3008, 0x3009, 0x2C,
         0x2E, 0x3B, 0x3A, 0x21, 0x3010, 0x3011, 0x3014, 0x3015, 0x5B, 0x5D,
         0x28, 0x29, 0xFF3B, 0xFF3D]
TR = {c: chr(c) + ' ' for c in BOUND}
# Raw control chars survive `mysql -B` (it only escapes \n \t \0 \\); a raw CR or
# U+2028 inside content makes tsvpipe see a short row ("not all columns found")
# and abort the whole build. For indexing they are just whitespace.
for c in list(range(0, 32)) + [0x7F, 0x85, 0x2028, 0x2029]:
    TR[c] = ' '


def unescape(s):
    return UNESC_RE.sub(lambda m: UNESC.get(m.group(1), m.group(1)), s)


def main():
    cmd = ['mysql', '--defaults-extra-file=' + CNF, '--default-character-set=utf8mb4',
           '--quick', '-N', '-B', 'gazetteer', '-e', SQL]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    out = sys.stdout.buffer
    n = 0
    for raw in p.stdout:
        line = raw.decode('utf-8', errors='replace').rstrip(NL)
        parts = line.split(TAB)
        if len(parts) != 4:
            sys.stderr.write('skip malformed row: %r\n' % line[:80])
            continue
        doc_id, content, book_id, page_no = parts
        content = unescape(content).translate(TR)
        out.write((doc_id + TAB + content + TAB + book_id.translate(TR).strip() + TAB + page_no.strip() + NL).encode('utf-8'))
        n += 1
    rc = p.wait()
    sys.stderr.write('logart_tsv: %d rows, mysql exit %d\n' % (n, rc))
    sys.exit(rc)


if __name__ == '__main__':
    main()
