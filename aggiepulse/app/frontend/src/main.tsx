import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import * as maplibregl from 'maplibre-gl';
import {Map as GLMap, Marker, Popup} from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';

type Provenance = 'MEASURED · APC' | 'DERIVED · GPS' | 'PREDICTED' | 'SIMULATED';
export function Badge({kind}: {kind: Provenance}) { return <span className="badge">{kind}</span>; }
type Route = {route_id: string; route_short_name: string; route_long_name: string; route_color: string};
type Vehicle = {vehicle_id: string; route?: string; route_id?: string; lat: number; lon: number; onboard: number | null; capacity: number | null; load_factor: number | null; gps_age_s: number | null; ts_gps: string | null};
async function get<T,>(url: string): Promise<T> {const r = await fetch(url); if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json();}
const pages = ['Live map', 'Forecast', 'Events', 'Plan my trip', 'History'];
function App() {
 const [page,setPage] = useState('Live map');
 return <><header><div className="brand">Aggie<span>Pulse</span><small>TEXAS A&M TRANSIT</small></div><nav aria-label="Main navigation">{pages.map(p=><button key={p} className={page===p?'active':''} onClick={()=>setPage(p)}>{p}</button>)}</nav></header>{page==='Live map'?<LiveMap/>:<main className="placeholder"><span className="eyebrow">COMING NEXT</span><h1>{page}</h1><p>{({Forecast:'Route crowding forecasts and the factors behind them.',Events:'Campus events and their expected transit impact.','Plan my trip':'Find a departure with room to ride.',History:'Compare predictions against observed passenger counts.'} as Record<string,string>)[page]}</p><p>This page is a placeholder. Forecast API samples are labelled <Badge kind="SIMULATED"/>.</p><button onClick={()=>setPage('Live map')}>Back to live map</button></main>}</>;
}
function LiveMap() {
 const container=useRef<HTMLDivElement>(null), map=useRef<GLMap|null>(null), markers=useRef<Marker[]>([]);
 const [routes,setRoutes]=useState<Route[]>([]),[selected,setSelected]=useState<string[]>([]),[vehicles,setVehicles]=useState<Vehicle[]>([]),[error,setError]=useState(''),[mapError,setMapError]=useState(''),[ready,setReady]=useState(false),[updated,setUpdated]=useState<string>('');
 useEffect(()=>{let alive=true; get<Route[]>('/api/routes').then(r=>{if(alive){setRoutes(r);setSelected(r.map(x=>x.route_id));}}).catch(e=>alive&&setError(String(e)));return()=>{alive=false;};},[]);
 useEffect(()=>{
  if(!container.current)return;
  const m=new maplibregl.Map({container:container.current,style:'https://tiles.openfreemap.org/styles/positron',center:[-96.340,30.615],zoom:14});map.current=m;
  m.addControl(new maplibregl.NavigationControl(),'top-right');
  m.on('error',()=>setMapError('Some map resources could not load. Check your connection.'));
  m.on('load',async()=>{try{
   const [shapes,stops]=await Promise.all([get<any>('/static/routes.geojson'),get<any>('/static/stops.geojson')]);
   if(map.current!==m)return;
   m.addSource('routes',{type:'geojson',data:shapes});m.addLayer({id:'routes',type:'line',source:'routes',paint:{'line-color':['case',['==',['get','route_color'],''],'#500000',['concat','#',['get','route_color']]],'line-width':4,'line-opacity':0.8}});
   m.addSource('stops',{type:'geojson',data:stops});m.addLayer({id:'stops',type:'circle',source:'stops',paint:{'circle-radius':4,'circle-color':'#fff','circle-stroke-color':'#500000','circle-stroke-width':1.5}});
   m.on('click','stops',e=>{const f=e.features?.[0];if(!f)return;const p=f.properties;const body=document.createElement('div');const title=document.createElement('strong');title.textContent=p.stop_name;body.append(title);const text=document.createElement('p');text.textContent=`Stop ${p.stop_code || p.stop_id} · GTFS schedule`;body.append(text);const served=document.createElement('p');served.textContent=`Routes: ${p.routes}`;body.append(served);new Popup().setLngLat(e.lngLat).setDOMContent(body).addTo(m);});
   m.on('mouseenter','stops',()=>{m.getCanvas().style.cursor='pointer';});m.on('mouseleave','stops',()=>{m.getCanvas().style.cursor='';});setReady(true);
  }catch(e){setMapError(String(e));}});
  return()=>{markers.current.forEach(x=>x.remove());m.remove();map.current=null;};
 },[]);
 useEffect(()=>{let alive=true; const controller=new AbortController();async function poll(){try{const r=await fetch('/api/vehicles/latest',{signal:controller.signal});if(!r.ok)throw new Error(`Vehicle feed unavailable (${r.status})`);const v=await r.json();if(alive){setVehicles(v);setUpdated(new Date().toLocaleTimeString());setError('');}}catch(e){if(alive)setError(String(e));}}poll();const timer=setInterval(poll,15000);return()=>{alive=false;controller.abort();clearInterval(timer);};},[]);
 useEffect(()=>{if(!ready||!map.current)return;const m=map.current;m.setFilter('routes',['in',['get','route_id'],['literal',selected]]);
  m.setFilter('stops',(['any',...selected.map(id=>['in',id,['get','routes']])] as maplibregl.ExpressionSpecification));
  markers.current.forEach(x=>x.remove());markers.current=[];
  vehicles.filter(v=>{const r=routes.find(r=>r.route_id===v.route_id||r.route_short_name===v.route);return !r||selected.includes(r.route_id);}).forEach(v=>{
   const el=document.createElement('button');el.className='bus';el.style.borderColor=v.load_factor==null?'#7b8490':v.load_factor<.5?'#238451':v.load_factor<.75?'#ce8b15':'#cf3f46';el.textContent='▣';el.setAttribute('aria-label',`Bus ${v.vehicle_id}`);
   const body=document.createElement('div');const title=document.createElement('strong');title.textContent=`Bus ${v.vehicle_id} · Route ${v.route || v.route_id || 'unknown'}`;body.append(title);
   const load=document.createElement('p');load.textContent=`${v.onboard ?? '—'} / ${v.capacity ?? '—'} onboard `;const measured=document.createElement('span');measured.className='badge';measured.textContent='MEASURED · APC';load.append(measured);body.append(load);
   const age=document.createElement('p');const seconds=v.gps_age_s;age.textContent=`GPS age: ${seconds!=null&&Number.isFinite(seconds)?Math.round(seconds)+' s':'unknown'} `;const derived=document.createElement('span');derived.className='badge';derived.textContent='DERIVED · GPS';age.append(derived);body.append(age);
   markers.current.push(new Marker({element:el}).setLngLat([v.lon,v.lat]).setPopup(new Popup({offset:20}).setDOMContent(body)).addTo(m));
  });
 },[ready,selected,vehicles,routes]);
 return <main className="live"><aside><span className="eyebrow">CAMPUS IN MOTION</span><h1>Live map</h1><p className="intro">Find your route. See room on board.</p><div className="status"><span className="dot"/> {error?'Feed unavailable':updated?'Feed connected':'Connecting…'}</div>{updated&&<p className="update">Updated {updated} <Badge kind="DERIVED · GPS"/></p>}<h2>Routes <span className="gtfs">GTFS schedule</span></h2><div className="actions"><button onClick={()=>setSelected(routes.map(r=>r.route_id))}>Show all</button><button onClick={()=>setSelected([])}>Hide all</button></div><div className="route-list">{routes.map(r=><label key={r.route_id}><input type="checkbox" checked={selected.includes(r.route_id)} onChange={()=>setSelected(s=>s.includes(r.route_id)?s.filter(x=>x!==r.route_id):[...s,r.route_id])}/><span className="swatch" style={{background:'#'+(r.route_color||'500000')}}/><strong>{r.route_short_name}</strong><span>{r.route_long_name}</span></label>)}</div><footer>Schedule data © TAMU Transportation Services.<br/>APC readings are reported by TripSpark.</footer></aside><section className="map-wrap" aria-label="Live campus bus map"><div ref={container} className="map"/>{(error||mapError)&&<div className="error" role="status">{error||mapError}</div>}<div className="legend"><strong>Onboard load</strong><Badge kind="MEASURED · APC"/><div><span style={{color:'#238451'}}>●</span> Under 50% <span style={{color:'#ce8b15'}}>●</span> 50–74% <span style={{color:'#cf3f46'}}>●</span> 75%+ <span style={{color:'#7b8490'}}>●</span> No data</div></div>{updated&&!vehicles.length&&!error&&<div className="empty">No buses in the latest local snapshot.</div>}</section></main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
