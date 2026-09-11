'use client';
import Link from 'next/link';
import { ArrowUpRight, Menu } from 'lucide-react';
import { LanternMark } from '@/components/lantern-mark';
import { Button } from '@/components/ui/button';
import { Dialog, DialogTrigger, DialogContent, DialogTitle, DialogDescription, DialogClose } from '@/components/ui/dialog';
const links=[['/#mission','The mission'],['/#lantern','Meet Lantern'],['/hardware/','Hardware'],['/#campaign','The ambition']];
export function SiteHeader() {
 return <header className="site-header wrap">
  <Link prefetch={false} className="brand" href="/" aria-label="SAGE home"><LanternMark/><span>SAGE<span className="brand-caption">FOUNDATION INITIATIVE</span></span></Link>
  <nav aria-label="Main navigation">{links.map(([href,label])=><Link prefetch={false} key={href} href={href}>{label}</Link>)}</nav>
  <Button className="cta nav-cta" render={<Link prefetch={false} href="/#involve" aria-label="Build with us"/>} nativeButton={false}>Build with us <ArrowUpRight/></Button>
  <Dialog><DialogTrigger className="mobile-menu" aria-label="Open navigation"><Menu/></DialogTrigger><DialogContent className="menu-panel"><DialogTitle>Explore SAGE</DialogTitle><DialogDescription>Intelligence. For all of us.</DialogDescription>{[...links,['/#involve','Build with us']].map(([href,label])=><DialogClose key={href} render={<Link prefetch={false} href={href} aria-label={label}/>} className="menu-link">{label}<ArrowUpRight/></DialogClose>)}</DialogContent></Dialog>
 </header>;
}
export function SiteFooter(){return <footer className="wrap"><div className="footer-top"><Link prefetch={false} className="brand" href="/" aria-label="SAGE home"><LanternMark/><span>SAGE</span></Link><p>Democratising intelligence.</p><nav aria-label="Footer navigation"><Link prefetch={false} href="/#mission">Mission</Link><Link prefetch={false} href="/hardware/">Hardware</Link><Link prefetch={false} href="/#progress">Progress</Link><Link prefetch={false} href="/sage-mission-brief.txt" download>Mission brief <ArrowUpRight size={13}/></Link></nav></div><div className="footer-bottom"><span>© 2026 SAGE Foundation initiative</span><span>Foundation in formation · Lantern in development</span><span>Made for a world of possibility.</span></div></footer>}
