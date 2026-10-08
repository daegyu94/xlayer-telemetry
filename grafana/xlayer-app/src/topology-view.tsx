import React from 'react';
import type {ResourceNode,ResourceEdge} from './infrastructure';
import {topologyEndpoint} from './infrastructure';

export function TopologyView({nodes,edges,selected,onSelect,variant}:{variant?:'workspace';nodes:ResourceNode[];edges:ResourceEdge[];selected?:string;onSelect:(node:ResourceNode)=>void}){
 const hosts=nodes.filter(node=>!node.device&&node.gpu===undefined);
 const groups=[{name:'Compute / GPU',nodes:hosts.filter(n=>n.kind==='compute'&&!/network|fabric/i.test(n.role))},
  {name:'Network context',nodes:hosts.filter(n=>/network|fabric/i.test(n.role))},
  {name:'Storage · declared DS / MDS',nodes:hosts.filter(n=>n.kind==='storage'&&!/network|fabric/i.test(n.role))}];
 if(variant==='workspace')return <WorkspaceTopology nodes={nodes} edges={edges} selected={selected} onSelect={onSelect}/>;
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

function WorkspaceTopology({nodes,edges,selected,onSelect}:{nodes:ResourceNode[];edges:ResourceEdge[];selected?:string;onSelect:(node:ResourceNode)=>void}){
 const hosts=nodes.filter(node=>!node.device&&node.gpu===undefined);
 const compute=hosts.filter(node=>node.kind==='compute'&&!/network|fabric/i.test(node.role));
 const storage=hosts.filter(node=>node.kind==='storage'&&!/network|fabric/i.test(node.role)).sort((a,b)=>Number(/meta|mds/i.test(b.role))-Number(/meta|mds/i.test(a.role))||a.component.localeCompare(b.component));
 const network=hosts.filter(node=>/network|fabric/i.test(node.role));
 const shown=[...compute.slice(0,4),...network.slice(0,2),...storage.slice(0,6)];
 const positions=new Map<string,{x:number;y:number;width:number;height:number}>();
 compute.slice(0,4).forEach((node,i)=>positions.set(node.key,{x:30+i%2*190,y:75+Math.floor(i/2)*100,width:177,height:83}));
 network.slice(0,2).forEach((node,i)=>positions.set(node.key,{x:440,y:115+i*68,width:185,height:58}));
 storage.slice(0,6).forEach((node,i)=>positions.set(node.key,{x:665+i%2*200,y:66+Math.floor(i/2)*75,width:185,height:62}));
 if(!nodes.length)return <div className="xlt-topology"><p className="xlt-empty">No configured inventory or exporter observations. Supply topology and registered targets; no relationship is inferred.</p></div>;
 return <div className="xlt-topology xlt-topology-reference"><div className="xlt-topology-legend"><span>Configured · dashed relationship</span><span>Observed · exporter sample, not health</span><span>Unknown · mapping / source missing</span></div><div className="xlt-topology-scroll"><svg viewBox="0 0 1080 315" role="img" aria-label="Configured cluster topology; no observed operation edges">
 <rect className="xlt-topology-group xlt-topology-compute" x="12" y="12" width="400" height="285" rx="7"/>
 <rect className="xlt-topology-group xlt-topology-storage" x="649" y="12" width="420" height="285" rx="7"/>
 <text className="xlt-topology-title" x="28" y="40">GPU / Compute · {compute.length} configured nodes</text>
 <text className="xlt-topology-title" x="665" y="40">Storage · {storage.length} declared DS / MDS</text>
 <text className="xlt-topology-title" x="444" y="95">Network Fabric</text>
 {edges.map(edge=>{const from=topologyEndpoint(nodes,edge,edge.source),to=topologyEndpoint(nodes,edge,edge.destination),a=from&&positions.get(from.key),b=to&&positions.get(to.key);if(!a||!b)return null;return <path className="xlt-topology-edge" key={edge.key} d={`M${a.x+a.width} ${a.y+a.height/2} C${a.x+a.width+45} ${a.y+a.height/2}, ${b.x-45} ${b.y+b.height/2}, ${b.x} ${b.y+b.height/2}`}><title>{edge.relation} · configured, not instrumented</title></path>;})}
 {shown.map(node=>{const at=positions.get(node.key)!;return <g key={node.key} role="button" tabIndex={0} aria-label={`Inspect resource ${node.component} (${node.kind})`} className={`xlt-topology-node ${selected===node.key?'selected':''}`} transform={`translate(${at.x},${at.y})`} onClick={()=>onSelect(node)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onSelect(node);}}}><rect width={at.width} height={at.height} rx="5"/><circle className={`xlt-observation-dot ${node.status}`} cx={at.width-14} cy="14" r="4"/><path className="xlt-resource-glyph" d="M11 13h17v25H11zM15 18h9M15 25h9M15 33h3"/><text x="37" y="27">{node.component.length>19?node.component.slice(0,17)+'…':node.component}</text><text className="xlt-topology-meta" x="12" y={at.height-15}>{node.kind} · {node.role.length>18?node.role.slice(0,16)+'…':node.role}</text><title>{node.cluster} / {node.kind} / {node.component} · owner {node.resourceNode||'unmapped'} · {node.status}; not a health verdict</title></g>;})}
 </svg></div><small className="xlt-topology-limit">{shown.length} shown / {hosts.length} host or fabric components. Inventory contains all resources. No observed operation edges or health verdict.</small></div>;
}
