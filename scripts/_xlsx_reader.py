import zipfile,re,json,os,sys
import xml.etree.ElementTree as ET
ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def col(ref):
    s=re.match(r'[A-Z]+',ref).group(); n=0
    for c in s: n=n*26+ord(c)-64
    return n-1
def read(path):
    z=zipfile.ZipFile(path)
    ss=[]
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns):
            ss.append(''.join(t.text or '' for t in si.iter('{%s}t'%ns['m'])))
    wb=ET.fromstring(z.read('xl/workbook.xml'))
    rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    rm={r.get('Id'):r.get('Target') for r in rels}
    out={}
    for sh in wb.find('m:sheets',ns):
        rid=sh.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        t=rm[rid]; t=t.lstrip('/'); t=t if t.startswith('xl/') else 'xl/'+t
        rows=[]
        for r in ET.fromstring(z.read(t)).iter('{%s}row'%ns['m']):
            row=[]
            for c in r.findall('m:c',ns):
                i=col(c.get('r')); 
                while len(row)<=i: row.append('')
                v=c.find('m:v',ns); ty=c.get('t')
                if ty=='s' and v is not None: val=ss[int(v.text)]
                elif ty=='inlineStr': val=''.join(x.text or '' for x in c.iter('{%s}t'%ns['m']))
                else: val=v.text if v is not None else ''
                row[i]=val
            rows.append(row)
        out[sh.get('name')]=rows
    return out
def _cli():
    d=read(sys.argv[1]); json.dump(d,open(sys.argv[2],'w',encoding='utf-8'),ensure_ascii=False)
    for k,v in d.items(): print(k,len(v))


if __name__ == "__main__":
    _cli()
