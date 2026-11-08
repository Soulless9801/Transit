import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import Pages from './Pages';
import FreshnessChip from './FreshnessChip';

const LiveMap=React.lazy(()=>import('./LiveMap'));
const pages = ['Live map', 'Forecast', 'Events', 'Plan my trip', 'History'];
function App() {
 const [page,setPage] = useState('Live map');
 const firstRender = useRef(true);
 useEffect(() => {
  if (firstRender.current) { firstRender.current = false; return; }
  document.title = `${page} · AggiePulse`;
  const frame = requestAnimationFrame(() => { const heading = document.querySelector<HTMLElement>('main h1'); if (heading) { if (!heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1'); heading.focus(); } });
  return () => cancelAnimationFrame(frame);
 }, [page]);
 return <><header><div className="brand">Aggie<span>Pulse</span><small>TEXAS A&M TRANSIT</small></div><FreshnessChip/><nav aria-label="Main navigation">{pages.map(p=><button key={p} className={page===p?'active':''} aria-current={page===p?'page':undefined} onClick={()=>setPage(p)}>{p}</button>)}</nav></header>{page==='Live map'?<React.Suspense fallback={<p>Loading map…</p>}><LiveMap/></React.Suspense>:<Pages page={page}/>}</>;
}
createRoot(document.getElementById('root')!).render(<App/>);
