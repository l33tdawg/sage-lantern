from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse,unquote
root=Path(__file__).resolve().parents[1]/'dist/client'
class Page(HTMLParser):
 def __init__(self):super().__init__();self.ids=set();self.urls=[];self.h1=0
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if 'id' in a:self.ids.add(a['id'])
  if tag=='h1':self.h1+=1
  self.urls.extend(a[k] for k in ('href','src') if k in a)
pages={}
for name in ('index.html','hardware/index.html','commons/index.html','foundation/index.html'):
 p=Page();p.feed((root/name).read_text());pages[name]=p
 assert p.h1==1,(name,p.h1)
for name,page in pages.items():
 for value in page.urls:
  u=urlparse(value)
  if u.scheme or u.netloc:continue
  path=unquote(u.path)
  target=root/path.lstrip('/') if path else root/name
  if target.is_dir():target/='index.html'
  assert target.is_file(),(name,value)
  if u.fragment:
   rel=str(target.relative_to(root))
   assert rel in pages and u.fragment in pages[rel].ids,(name,value,'missing fragment')
home=(root/'index.html').read_text();hardware=(root/'hardware/index.html').read_text();foundation=(root/'foundation/index.html').read_text()
assert '<em>better postcode</em>' in home
assert 'sharing a notebook' not in home
assert '/images/hardware-20260930.jpg' in home and '/images/hardware-20260930.jpg' in hardware
assert 'dhillon@sage.ngo' in home and 'dhillon@sage.ngo' in hardware
assert '6.5' in hardware
assert 'https://sage-foundation.net/hardware/' in hardware
# 2026-10-01 owner direction: sage-foundation.net is the primary domain and
# sage.ngo and sage-commons.org are secondary, so every page must consolidate
# to the primary canonical host. The only allowed sage.ngo reference in page
# HTML is the public contact address.
for name in pages:
    html=(root/name).read_text()
    assert f'<link rel="canonical" href="https://sage-foundation.net' in html, name
    assert 'sage.ngo' not in html.replace('dhillon@sage.ngo',''), name
assert 'https://sage-foundation.net/sitemap.xml' in (root/'robots.txt').read_text()
sitemap=(root/'sitemap.xml').read_text()
assert sitemap.count('https://sage-foundation.net/')==4 and 'sage.ngo' not in sitemap
assert 'Untitled site' not in home+hardware
assert 'dhillon@sage.ngo' in foundation
# Owner instruction 2026-09-21: the per-part licence set is withdrawn until it
# is signed off, so the check now pins the withdrawal rather than the claims.
# Re-instate a positive assertion only when a signed-off licence returns.
for identifier in ('CERN-OHL-S', 'GPL-3.0', 'Apache-2.0', 'CC BY-SA'):
    assert identifier not in foundation, identifier
    assert identifier not in (root / 'commons/index.html').read_text(), identifier
assert 'signed off' in foundation
assert '/foundation/' in home
print('PASS: all four pages, section anchors, local assets, contact, italic copy, hardware direction, licence map and canonical domain.')
