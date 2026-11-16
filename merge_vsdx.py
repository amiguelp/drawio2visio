#!/usr/bin/env python3
"""Une varios .vsdx de una sola página en un único .vsdx con una página por fichero.

    python merge_vsdx.py salida.vsdx pagina1.vsdx "Nombre 1" pagina2.vsdx "Nombre 2" ...

Cada página conserva su tamaño de papel, sus imágenes (renombradas para que no
choquen) y sus maestros (el conector dinámico, que se comparte si es el mismo).
"""
import html, re, sys, zipfile

PAGE_CT = 'application/vnd.ms-visio.page+xml'
MASTER_CT = 'application/vnd.ms-visio.master+xml'
REL_PAGE = 'http://schemas.microsoft.com/visio/2010/relationships/page'
REL_MASTER = 'http://schemas.microsoft.com/visio/2010/relationships/master'


def rels(xml):
    """{Id: (Type, Target)} de un fichero .rels (comillas simples o dobles)."""
    out = {}
    for m in re.finditer(r'<Relationship\b([^>]*)/?>', xml):
        a = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', m.group(1)))
        out[a['Id']] = (a.get('Type', ''), a.get('Target', ''))
    return out


def rels_xml(items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + ''.join('<Relationship Id="%s" Type="%s" Target="%s"/>' % (i, t, html.escape(g, True)) for i, t, g in items)
            + '</Relationships>')


def merge(dst, inputs):
    """inputs: [(ruta_vsdx, nombre_página)]"""
    zs = [zipfile.ZipFile(p) for p, _ in inputs]
    base = zs[0]
    files = {}                      # nombre -> bytes del paquete final
    for it in base.infolist():
        n = it.filename
        if n.startswith(('visio/pages/', 'visio/media/', 'visio/masters/')):
            continue
        files[n] = base.read(n)

    masters = {}                    # Master ID -> (nombre fichero final, xml <Master>)
    media_n = [0]
    page_entries, page_rels = [], []

    for k, (z, (path, name)) in enumerate(zip(zs, inputs), start=1):
        names = set(z.namelist())
        # ---- maestros de este fichero: id local de rel -> fichero final
        local_master = {}           # '../masters/masterX.xml' (del fichero) -> nombre final
        if 'visio/masters/masters.xml' in names:
            mx = z.read('visio/masters/masters.xml').decode('utf-8')
            mrels = rels(z.read('visio/masters/_rels/masters.xml.rels').decode('utf-8'))
            for blk in re.findall(r'<Master\b.*?</Master>', mx, re.S):
                mid = re.search(r'\bID=["\'](\d+)["\']', blk).group(1)
                rid = re.search(r'<Rel r:id=["\']([^"\']+)["\']', blk).group(1)
                target = mrels[rid][1]
                if mid not in masters:
                    fname = 'master%d.xml' % (len(masters) + 1)
                    files['visio/masters/' + fname] = z.read('visio/masters/' + target)
                    mr = 'visio/masters/_rels/%s.rels' % target
                    if mr in names:
                        files['visio/masters/_rels/%s.rels' % fname] = z.read(mr)
                    masters[mid] = (fname, blk)
                local_master['../masters/' + target] = masters[mid][0]
        # ---- página
        page = z.read('visio/pages/page1.xml')
        prels = rels(z.read('visio/pages/_rels/page1.xml.rels').decode('utf-8')) \
            if 'visio/pages/_rels/page1.xml.rels' in names else {}
        new_rels = []
        for rid, (typ, tgt) in prels.items():
            if typ == REL_MASTER:
                tgt = '../masters/' + local_master.get(tgt, tgt.split('/')[-1])
            elif tgt.startswith('../media/'):
                media_n[0] += 1
                ext = tgt.rsplit('.', 1)[-1]
                newname = 'image%d.%s' % (media_n[0], ext)
                files['visio/media/' + newname] = z.read('visio/media/' + tgt[len('../media/'):])
                tgt = '../media/' + newname
            new_rels.append((rid, typ, tgt))
        files['visio/pages/page%d.xml' % k] = page
        files['visio/pages/_rels/page%d.xml.rels' % k] = rels_xml(new_rels).encode('utf-8')
        # ---- entrada en pages.xml (conserva PageSheet: tamaño de papel)
        px = z.read('visio/pages/pages.xml').decode('utf-8')
        entry = re.search(r'<Page\b.*?</Page>', px, re.S).group(0)
        nm = html.escape(name or 'Página %d' % k, True)
        entry = re.sub(r'<Page\b[^>]*>', '<Page ID="%d" NameU="%s" Name="%s">' % (k - 1, nm, nm), entry, count=1)
        entry = re.sub(r'<Rel r:id=["\'][^"\']+["\']\s*/>', '<Rel r:id="rId%d"/>' % k, entry)
        page_entries.append(entry)
        page_rels.append(('rId%d' % k, REL_PAGE, 'page%d.xml' % k))

    # ---- pages.xml
    head = re.match(r'(.*?<Pages\b[^>]*>)', base.read('visio/pages/pages.xml').decode('utf-8'), re.S).group(1)
    files['visio/pages/pages.xml'] = (head + ''.join(page_entries) + '</Pages>').encode('utf-8')
    files['visio/pages/_rels/pages.xml.rels'] = rels_xml(page_rels).encode('utf-8')

    # ---- masters.xml
    if masters:
        mhead = None
        for z in zs:
            if 'visio/masters/masters.xml' in z.namelist():
                mhead = re.match(r'(.*?<Masters\b[^>]*>)', z.read('visio/masters/masters.xml').decode('utf-8'), re.S).group(1)
                break
        blocks, mrel = [], []
        for i, (mid, (fname, blk)) in enumerate(masters.items(), start=1):
            blocks.append(re.sub(r'<Rel r:id=["\'][^"\']+["\']\s*/>', "<Rel r:id='rId%d'/>" % i, blk))
            mrel.append(('rId%d' % i, REL_MASTER, fname))
        files['visio/masters/masters.xml'] = (mhead + ''.join(blocks) + '</Masters>').encode('utf-8')
        files['visio/masters/_rels/masters.xml.rels'] = rels_xml(mrel).encode('utf-8')

    # ---- [Content_Types].xml: overrides de páginas y maestros
    ct = files['[Content_Types].xml'].decode('utf-8')
    ct = re.sub(r"<Override PartName=['\"]/visio/(pages/page\d+|masters/master\d+)\.xml['\"][^>]*/>", '', ct)
    extra = ''.join("<Override PartName='/visio/pages/page%d.xml' ContentType='%s'/>" % (k, PAGE_CT)
                    for k in range(1, len(inputs) + 1))
    extra += ''.join("<Override PartName='/visio/masters/%s' ContentType='%s'/>" % (f, MASTER_CT) for f, _ in masters.values())
    if masters and '/visio/masters/masters.xml' not in ct:
        extra += "<Override PartName='/visio/masters/masters.xml' ContentType='application/vnd.ms-visio.masters+xml'/>"
    files['[Content_Types].xml'] = ct.replace('</Types>', extra + '</Types>').encode('utf-8')

    # ---- document.xml.rels: debe apuntar a masters.xml solo si hay maestros
    dr = files['visio/_rels/document.xml.rels'].decode('utf-8')
    if not masters:
        dr = re.sub(r"<Relationship\b[^>]*masters/masters\.xml[^>]*/>", '', dr)
        files['visio/_rels/document.xml.rels'] = dr.encode('utf-8')
        files['[Content_Types].xml'] = re.sub(r"<Override PartName=['\"]/visio/masters/masters\.xml['\"][^>]*/>", '',
                                              files['[Content_Types].xml'].decode('utf-8')).encode('utf-8')
    if masters and 'masters/masters.xml' not in dr:
        dr = dr.replace('</Relationships>', "<Relationship Id='rIdM' Type='http://schemas.microsoft.com/visio/2010/relationships/masters' Target='masters/masters.xml'/></Relationships>")
        files['visio/_rels/document.xml.rels'] = dr.encode('utf-8')

    order = ['[Content_Types].xml'] + sorted(n for n in files if n != '[Content_Types].xml')
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zo:
        for n in order:
            zo.writestr(n, files[n])


if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) < 3 or len(a) % 2 == 0:
        raise SystemExit(__doc__)
    merge(a[0], [(a[i], a[i + 1]) for i in range(1, len(a), 2)])
    print('unido:', a[0], '(%d páginas)' % ((len(a) - 1) // 2))
