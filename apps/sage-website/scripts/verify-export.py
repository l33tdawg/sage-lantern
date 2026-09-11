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
for name in ('index.html','hardware/index.html'):
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
home=(root/'index.html').read_text();hardware=(root/'hardware/index.html').read_text()
assert '<em>better postcode</em>' in home
assert 'sharing a notebook' not in home
assert '/images/hardware.jpg' in home and '/images/hardware.jpg' in hardware
assert 'dhillon@sage.ngo' in home and 'dhillon@sage.ngo' in hardware
assert '6.5' in hardware
assert 'https://sage.ngo/hardware/' in hardware
assert 'Untitled site' not in home+hardware
print('PASS: both pages, section anchors, local assets, contact, italic copy, hardware direction and canonical domain.')
