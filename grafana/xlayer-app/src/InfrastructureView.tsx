import React from 'react';
import { Context, appLink } from './context';
import { Resource, infrastructure, resourceSelection } from './infrastructure';

export type InfrastructureModel=ReturnType<typeof infrastructure>;
export function InfrastructureView({data,context,onSelect,metrics,links,unavailable}:{data:InfrastructureModel;context:Context;onSelect:(node:Resource)=>void;
 metrics:(node:Resource)=>React.ReactNode;links:(node:Resource,c:Context)=>React.ReactNode;unavailable?:string}){
 const selected=data.nodes.find(n=>n.key===context.variables.infra_component?.[0]);
 const visible=data.nodes.filter(n=>['compute','storage'].includes(n.kind)&&['node','fabric','unknown'].includes(n.type)).slice(0,24);
 const columns=[visible.filter(n=>n.kind==='compute'&&n.type!=='fabric'),visible.filter(n=>n.type==='fabric'),visible.filter(n=>n.kind==='storage'&&n.type!=='fabric')];
 const positions=new Map<string,{x:number;y:number}>();columns.forEach((column,index)=>column.forEach((node,i)=>positions.set(node.key,{x:20+index*295,y:24+i*64})));
 const height=Math.max(180,...columns.map(col=>col.length*64+30));
 const devices=selected?.resource?data.nodes.filter(n=>n.cluster===selected.cluster&&n.resource===selected.resource&&n.kind===selected.kind):[];
 return <><section><h3>Configured Resource Topology</h3><p className="xlt-notice">Configured relationships are operator declarations. Collector available means returned scrape/age evidence, not node health, physical connectivity or Run ownership.</p>
  {unavailable&&<p className="xlt-empty">{unavailable} · inventory is unavailable; no healthy state is inferred.</p>}
  {!data.nodes.length?<p className="xlt-empty">No topology inventory in this interval. Connect configured topology before selecting a resource.</p>:<div className="xlt-scroll xlt-topology"><svg viewBox={`0 0 900 ${height}`} width="900" height={height} role="group" aria-label="Configured GPU, network fabric and storage topology">
   {data.edges.filter(e=>positions.has(e.source)&&positions.has(e.destination)).map((edge,index)=>{const a=positions.get(edge.source)!,b=positions.get(edge.destination)!;return <g key={index}><title>{edge.relation} · {edge.state} relationship; connectivity not observed</title><line x1={a.x+125} y1={a.y+32} x2={b.x+125} y2={b.y+32} stroke="currentColor" strokeOpacity=".25" strokeDasharray="5 4"/></g>})}
   {visible.map(node=>{const p=positions.get(node.key)!;return <g key={node.key} role="button" tabIndex={0} aria-label={`Inspect ${node.id}`} aria-pressed={node.key===selected?.key}
    onClick={()=>onSelect(node)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(node);}}} className="xlt-topology-node">
    <title>{node.id} · {node.kind}/{node.role} · mapping {node.mapping} · collector {node.collector}</title>
    <rect x={p.x} y={p.y} width="250" height="52" rx="5" className={node.key===selected?.key?'xlt-topology-selected':''}/>
    <text x={p.x+12} y={p.y+18}>{node.id.length>28?node.id.slice(0,25)+'…':node.id}</text><text x={p.x+12} y={p.y+33} className="xlt-topology-meta">{node.kind} · {node.role} · {node.mapping}</text><text x={p.x+12} y={p.y+46} className="xlt-topology-meta">Collector: {node.collector}</text>
   </g>})}
  </svg></div>}
  <p className="xlt-muted">Dashed lines: Configured. Endpoint missing: Unknown. No edge is marked as observed connectivity. Devices use explicit resource_node/GPU/interface/device mappings; publisher names are separate.</p>
  {data.limited&&<p className="xlt-notice">Inventory exceeds the compact view limit; use canonical topology tables to inspect all declarations.</p>}
 </section>
 {selected&&<section><h3>Selected Resource · {selected.id}</h3><p>{selected.cluster} · {selected.kind}/{selected.role} · mapping {selected.mapping} · collector {selected.collector}</p>
  {selected.mapping!=='configured'?<p className="xlt-empty">Resource mapping is unknown or conflicting. No resource query or device attribution is created from the publisher identity.</p>:<>
   <label>Node / device<select aria-label="Infrastructure resource" value={selected.key} onChange={e=>{const node=data.nodes.find(n=>n.key===e.target.value);if(node)onSelect(node)}}>{devices.map(node=><option key={node.key} value={node.key}>{node.id} · {node.type}</option>)}</select></label>
   <div className="xlt-device-map"><b>{selected.resource} · declared resource map</b><div>{devices.filter(node=>node.type!=='node').map(node=><button key={node.key} aria-pressed={node.key===selected.key} onClick={()=>onSelect(node)}>{node.gpu!==undefined?`GPU ${node.gpu}`:node.interface||node.device||node.id}<small>{node.type} · configured</small></button>)}</div></div>
   <p className="xlt-muted">Node-wide metrics and device samples retain their source scope. Availability does not prove metric coverage; query failure, stale and missing remain distinct from zero.</p>
   <div className="xlt-actions">{links(selected,resourceSelection(context,selected))}<a href={appLink('investigate',resourceSelection(context,selected))}>Investigate selected Step →</a></div>
   <div className="xlt-infra-metrics">{metrics(selected)}</div>
  </>}
 </section>}
 <details className="xlt-completed-detail"><summary>Component Inventory · {data.nodes.length} declarations</summary><div className="xlt-scroll"><table><thead><tr><th>Component</th><th>Resource identity</th><th>Scope / Quality</th><th>Next</th></tr></thead><tbody>{data.nodes.map(node=><tr key={node.key}><td>{node.id}<small>{node.cluster} · {node.kind}/{node.role}</small></td><td>{node.resource||'Unknown'}<small>{node.gpu!==undefined?`GPU ${node.gpu}`:node.interface||node.device||''}</small></td><td>Configured · {node.mapping}<small>Collector {node.collector}</small></td><td><button onClick={()=>onSelect(node)}>Select resource →</button></td></tr>)}</tbody></table></div></details>
 </>;
}
