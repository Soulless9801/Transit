import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import Pages from './Pages';

type Provenance = 'MEASURED · APC' | 'DERIVED · GPS' | 'PREDICTED' | 'SIMULATED';
export function Badge({kind}: {kind: Provenance}) { return <span className="badge">{kind}</span>; }
type Route = {route_id: string; route_short_name: string; route_long_name: string; route_color: string};
type Vehicle = {vehicle_id: string; route?: string; route_id?: string; lat: number; lon: number; onboard: number | null; capacity: number | null; load_factor: number | null; gps_age_s: number | null; ts_gps: string | null};
async function get<T,>(url: string): Promise<T> {const r = await fetch(url); if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json();}
const LiveMap=React.lazy(()=>import('./LiveMap'));
const pages = ['Live map', 'Forecast', 'Events', 'Plan my trip', 'History'];
function App() {
 const [page,setPage] = useState('Live map');
 return <><header><div className="brand">Aggie<span>Pulse</span><small>TEXAS A&M TRANSIT</small></div><nav aria-label="Main navigation">{pages.map(p=><button key={p} className={page===p?'active':''} onClick={()=>setPage(p)}>{p}</button>)}</nav></header>{page==='Live map'?<React.Suspense fallback={<p>Loading map…</p>}><LiveMap/></React.Suspense>:<Pages page={page}/>}</>;
}
createRoot(document.getElementById('root')!).render(<App/>);
