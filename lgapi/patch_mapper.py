#!/usr/bin/env python3
"""Generate the Manticore version of lg-api's PageTextMapper.xml from YOUR copy of the original.

    python patch_mapper.py <PageTextMapper.orig.xml> <PageTextMapper.xml>

The original mapper is part of lg-api (MPIWG) and is NOT distributed with this project.
Extract it from your own lg-api.jar (see build_patched_jar.py, which does extract + patch +
repack in one step).

Changes (v3):
 1. <sql id="mt_binds">: two OGNL <bind>s that turn the backend's MySQL-boolean keyword
    string into Manticore syntax (OR between phrases unless a '+' is present, etc.) and strip
    characters that would break the nested string literals.
 2. <sql id="keywords_filters"> (used by every select that needs page content: export,
    comparison, tasks, ...): MATCH ... AGAINST -> cs.id IN (SELECT id FROM mt_pages ...).
 3. FAST path for the hot selects getCount / getStatistics / search, taken when
    keywords != null and regexp == null (search: and not sorted by section_name/content):
    the FEDERATED table mt_pages itself is aliased as `cs` (it returns id, book_id, page_no
    from Manticore), so MySQL never touches contents_myisam_ft for counting / grouping /
    sorting. search then reads content only for the rows of the requested page and looks up
    section names only for those rows. The middle derived table is aliased `cs` too,
    because the backend appends ', cs.id' to sortField as a tie-breaker.
    Otherwise the ORIGINAL select runs unchanged except for (2).

Asserts that every original fragment it replaces occurs exactly once, so a changed upstream
mapper fails loudly instead of producing a wrong patch.
Backslashes are built with chr(92) on purpose (keeps the escaping readable and robust).
"""
import sys

BS = chr(92)
DQ = chr(34)
MAX_MATCHES = 5000000   # must exceed the total number of pages in the corpus
VERSION = 'v3'


def _once(text, old):
    n = text.count(old)
    assert n == 1, 'expected exactly one occurrence, found %d of: %r' % (n, old[:80])


def _block(text, sel_id):
    a = text.index('<select id="%s"' % sel_id)
    b = text.index('</select>', a) + len('</select>')
    return a, b


MT_QUERY = ("'SELECT id, book_id, page_no FROM pages WHERE MATCH(''${mtKw}'') "
            "LIMIT 0,%d OPTION max_matches=%d'" % (MAX_MATCHES, MAX_MATCHES))
FAST = 'keywords != null and regexp == null'
# the backend passes sortField with its own tie-breaker appended, e.g. 'page_no asc, cs.id'
FAST_SEARCH = FAST + (" and (sortField == null or (sortField.indexOf('section_name') &lt; 0"
                      " and sortField.indexOf('content') &lt; 0))")

MT_BINDS = (
    '<sql id="mt_binds">\n'
    "    <bind name=\"mtKw0\" value='keywords.replace(\"" + BS + BS + BS + DQ + "\", \"\")"
    ".replace(\"" + BS + BS + "\", \"\").replace(\"&apos;\", \"\").replaceAll(\"[|()^$]\", \" \")' />\n"
    "    <bind name=\"mtKw\" value='mtKw0.contains(&apos;+\"&apos;) ? "
    "mtKw0.replaceAll(&apos;(^| )\"[^\"]*\"&apos;, \"\").replace(&apos;+\"&apos;, &apos; \"&apos;) : "
    "mtKw0.replace(&apos;\" \"&apos;, &apos;\" | \"&apos;)' />\n"
    '</sql>\n\n'
    '<sql id="mt_where">\n'
    '    <include refid="mt_binds" />\n'
    '    WHERE cs.query = ' + MT_QUERY + '\n'
    '</sql>\n\n'
)

SECTION_BY_T = '''       (Select GROUP_CONCAT( concat(si.level ,'-', si.name) ORDER BY si.revision_id desc SEPARATOR ', ')
          From sections_index si
         Where si.book_id = t.bookId and
               t.page_no between si.start_page and si.end_page) as section_name'''


def patch(s):
    """Return the patched mapper text for the original mapper text `s`."""
    # --- (2) keywords_filters: IN (federated) --------------------------------------------
    old_kw = "MATCH (content) AGAINST ('${keywords}' IN BOOLEAN MODE)"
    _once(s, old_kw)
    s = s.replace(old_kw,
                  '<include refid="mt_binds" />\n'
                  '            cs.id IN (SELECT mtp.id FROM mt_pages mtp WHERE mtp.query = ' + MT_QUERY + ')')
    _once(s, '<sql id="keywords_filters">')
    s = s.replace('<sql id="keywords_filters">', MT_BINDS + '<sql id="keywords_filters">')

    # --- (3a) getCount -------------------------------------------------------------------
    a, b = _block(s, 'getCount')
    orig = s[a:b]
    body = orig[orig.index('>') + 1: orig.rindex('</select>')]
    new = (orig[:orig.index('>') + 1] + '\n<choose>\n<when test="' + FAST + '">\n'
           'Select count(*)\n  From mt_pages cs\n           inner join books bs\n              on bs.id = cs.book_id\n'
           '  <include refid="mt_where" />\n  <include refid="search_filters" /> ;\n'
           '</when>\n<otherwise>' + body + '</otherwise>\n</choose>\n</select>')
    s = s[:a] + new + s[b:]

    # --- (3b) getStatistics: same body, cs = mt_pages on the fast path --------------------
    a, b = _block(s, 'getStatistics')
    st = s[a:b]
    _once(st, 'From contents_myisam_ft cs')
    st = st.replace('From contents_myisam_ft cs',
                    'From <choose><when test="' + FAST + '">mt_pages</when>'
                    '<otherwise>contents_myisam_ft</otherwise></choose> cs')
    _once(st, '<include refid="keywords_filters" />')
    st = st.replace('<include refid="keywords_filters" />',
                    '<choose><when test="' + FAST + '"><include refid="mt_where" /></when>'
                    '<otherwise><include refid="keywords_filters" /></otherwise></choose>')
    s = s[:a] + st + s[b:]

    # --- (3c) search -----------------------------------------------------------------------
    a, b = _block(s, 'search')
    orig = s[a:b]
    head = orig[:orig.index('>') + 1]
    body = orig[len(head): orig.rindex('</select>')]
    fast = head + '''
<choose>
<when test="''' + FAST_SEARCH + '''">
Select t.*,
       <if test="get_section_ids != null">
       (Select GROUP_CONCAT( si.id SEPARATOR ',')
          From sections_index si
         Where si.book_id = t.bookId and
               t.page_no between si.start_page and si.end_page) as section_ids,
       </if>
''' + SECTION_BY_T + '''
  From (
Select cs.*, ct.content
  From (
Select cs.id, bs.id as bookId, bs.name as book_name,
       bs.level1, bs.level2, bs.dynasty, bs.book_year, bs.edition_year,
       bs.period, bs.admin_type, bs.edition, bs.author, bs.volume,
       bs.source, cs.page_no
  From mt_pages cs
           inner join books bs
              on bs.id = cs.book_id
  <include refid="mt_where" />
       <include refid="search_filters" />
       <if test="sortField != null">
 Order by ${sortField} ${sortOrder}
       </if>
 limit ${page}, ${rows}
       ) cs
           inner join contents_myisam_ft ct
              on ct.id = cs.id
       <if test="sortField != null">
 Order by ${sortField} ${sortOrder}
       </if>
       ) t;
</when>
<otherwise>''' + body + '''</otherwise>
</choose>
</select>'''
    return s[:a] + fast + s[b:]


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    src, dst = argv[1], argv[2]
    s = open(src, encoding='utf-8').read()
    open(dst, 'w', encoding='utf-8', newline='').write(patch(s))
    print('patched', VERSION)


if __name__ == '__main__':
    main(sys.argv)
