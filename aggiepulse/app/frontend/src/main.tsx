import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import Pages from './Pages';
import FreshnessChip from './FreshnessChip';
import {Status} from './ui';
import {PAGES, Page, pageFromHash, hashForPage} from './format';

const LiveMap=React.lazy(()=>import('./LiveMap'));
function App() {
 const [page,setPage] = useState<Page>(() => pageFromHash(location.hash));
 const firstRender = useRef(true);
 useEffect(() => {
  const onHashChange = () => setPage(pageFromHash(location.hash));
  window.addEventListener('hashchange', onHashChange);
  return () => window.removeEventListener('hashchange', onHashChange);
 }, []);
 useEffect(() => {
  if (firstRender.current) { firstRender.current = false; return; }
  document.title = `${page} · AggiePulse`;
  const frame = requestAnimationFrame(() => { const heading = document.querySelector<HTMLElement>('main h1'); if (heading) { if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1'); heading.focus(); } });
  return () => cancelAnimationFrame(frame);
 }, [page]);
 return <><header><div className="brand">Aggie<span>Pulse</span><small>TEXAS A&M TRANSIT</small></div><FreshnessChip/><nav aria-label="Main navigation">{PAGES.map(p=><a key={p} href={hashForPage(p)} className={page===p?'active':''} aria-current={page===p?'page':undefined}>{p}</a>)}</nav></header>{page==='Live map'?<React.Suspense fallback={<Status loading/>}><LiveMap/></React.Suspense>:<Pages page={page}/>}</>;
}
createRoot(document.getElementById('root')!).render(<App/>);
