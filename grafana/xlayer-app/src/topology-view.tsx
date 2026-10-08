import React from 'react';
import type {ResourceNode,ResourceEdge} from './infrastructure';
import {topologyEndpoint} from './infrastructure';

export function TopologyView({nodes,edges,selected,onSelect}:{nodes:ResourceNode[];edges:ResourceEdge[];selected?:string;onSelect:(node:ResourceNode)=>void}){
 const hosts=nodes.filter(node=>!node.device&&node.gpu===undefined);
 const groups=[{name:'Compute / GPU',nodes:hosts.filter(n=>n.kind==='compute'&&!/network|fabric/i.test(n.role))},
  {name:'Network context',nodes:hosts.filter(n=>/network|fabric/i.test(n.role))},
  {name:'Storage · declared DS / MDS',nodes:hosts.filter(n=>n.kind==='storage'&&!/network|fabric/i.test(n.role))}];
 const positions=new Map<string,{x:number;y:number}>();
 groups.forEach((group,col)=>group.nodes.forEach((node,row)=>positions.set(node.key,{x:col*260+15,y:65+row*56})));
 const height=Math.max(240,85+Math.max(...groups.map(g=>g.nodes.length),1)*56);
 const find=(edge:ResourceEdge,id:string)=>topologyEndpoint(nodes,edge,id);
 return <div className="xlt-topology"><div className="xlt-topology-legend"><span>Configured · dashed relation</span><span>Observed · exporter sample</span><span>Unknown · mapping / source missing</span></div>
 {!nodes.length?<p className="xlt-empty">No configured inventory or exporter observations. Supply topology and registered targets; no relationship is inferred.</p>:<div className="xlt-topology-scroll"><svg viewBox={`0 0 790 ${height}`} role="img" aria-label="Configured cluster topology; no observed operation edges">
 {groups.map((group,col)=><g key={group.name}><rect className="xlt-topology-group" x={col*260+4} y={8} width={252} height={height-18} rx={7}/><text className="xlt-topology-title" x={col*260+18} y={35}>{group.name}</text></g>)}
 {edges.map(edge=>{const from=find(edge,edge.source),to=find(edge,edge.destination),a=from&&positions.get(from.key),b=to&&positions.get(to.key);if(!a||!b||from?.key===to?.key)return null;return <path key={edge.key} className="xlt-topology-edge" d={`M ${a.x+112} ${a.y+20} C ${a.x+190} ${a.y+20}, ${b.x-40} ${b.y+20}, ${b.x+5} ${b.y+20}`}><title>{edge.relation} · configured, not instrumented</title></path>;})}
 {groups.flatMap(group=>group.nodes).map(node=>{const position=positions.get(node.key)!;return <g key={node.key} role="button" tabIndex={0} aria-label={`Inspect resource ${node.component} (${node.kind})`} onClick={()=>onSelect(node)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onSelect(node);}}} className={`xlt-topology-node ${selected===node.key?'selected':''}`} transform={`translate(${position.x},${position.y})`}><rect width={230} height={44} rx={5}/><text x={10} y={17}>{node.component.length>27?node.component.slice(0,24)+'…':node.component}</text><text className="xlt-topology-meta" x={10} y={33}>{node.kind} · {node.status==='observed'?'Exporter observed':node.status==='scrape_down'?'Scrape down':'Configured · unknown'}</text><title>{node.cluster} / {node.kind} / {node.component} · {node.role} · owner {node.resourceNode||'unmapped'}</title></g>;})}
 </svg></div>}
 <p className="xlt-muted">Configured relationships are operator declarations. Metric observations do not validate edges, resource attribution or causality. Device components remain available in the inventory.</p>
 </div>;
}
