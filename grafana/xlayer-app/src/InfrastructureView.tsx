import React, { useId } from 'react';
import { Context, appLink } from './context';
import { Resource, infrastructure, resourceSelection } from './infrastructure';
import { topologyLayout } from './topology-layout';

function ResourceGlyph({ column, x, y }: { column: number; x: number; y: number }) {
 return <g transform={`translate(${x}, ${y})`} className="xlt-topology-glyph" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
  {column===0?<><rect x="4" y="4" width="12" height="12" rx="2"/><rect x="7" y="7" width="6" height="6" rx="1"/>{[7,13].map(value=><React.Fragment key={value}><path d={`M ${value} 1 V 4 M ${value} 16 V 19 M 1 ${value} H 4 M 16 ${value} H 19`}/></React.Fragment>)}</>
   :column===1?<><rect x="6" y="1" width="8" height="5" rx="1"/><path d="M 10 6 V 11 M 3 11 H 17 M 3 11 V 14 M 17 11 V 14"/><rect x="0" y="14" width="6" height="5" rx="1"/><rect x="14" y="14" width="6" height="5" rx="1"/></>
   :<><rect x="2" y="2" width="16" height="7" rx="1.5"/><rect x="2" y="11" width="16" height="7" rx="1.5"/><path d="M 5 5.5 H 11 M 5 14.5 H 11 M 15 5.5 H 15.1 M 15 14.5 H 15.1"/></>}
 </g>;
}

export type InfrastructureModel=ReturnType<typeof infrastructure>;
export function InfrastructureView({data,context,onSelect,metrics,links,unavailable}:{data:InfrastructureModel;context:Context;onSelect:(node:Resource)=>void;
 metrics:(node:Resource)=>React.ReactNode;links:(node:Resource,c:Context)=>React.ReactNode;unavailable?:string}){
 const diagramId=useId().replace(/:/g,'');
 const selected=data.nodes.find(n=>n.key===context.variables.infra_component?.[0]);
 const layout=topologyLayout(data.nodes,data.edges);
 const devices=selected?.resource?data.nodes.filter(n=>n.cluster===selected.cluster&&n.resource===selected.resource&&n.kind===selected.kind):[];
 const endpointLabel=(key:string)=>{const node=data.nodes.find(value=>value.key===key);if(node)return node.id;try{const identity=JSON.parse(key);return Array.isArray(identity)?String(identity[2]||key):key;}catch{return key;}};
 return <><section><h3>Configured Resource Topology</h3><p className="xlt-notice" id={`${diagramId}-scope`}>Configured relationships are operator declarations. Collector available means returned scrape/age evidence, not node health, physical connectivity or Run ownership.</p>
  {unavailable&&<p className="xlt-empty">{unavailable} · inventory is unavailable; no healthy state is inferred.</p>}
  {!data.nodes.length?<p className="xlt-empty">No topology inventory in this interval. Connect configured topology before selecting a resource.</p>:<div className="xlt-scroll xlt-topology"><svg viewBox={`0 0 ${layout.width} ${layout.height}`} width={layout.width} height={layout.height} style={{maxWidth:'100%',height:'auto',minWidth:760}} preserveAspectRatio="xMinYMin meet" role="group" aria-label="Configured GPU, network fabric and storage topology" aria-describedby={`${diagramId}-scope`}>
   <rect width={layout.width} height={layout.height} rx="7" fill="var(--xl-soft)"/>
   {layout.columns.map(column=><g key={column.column} aria-label={column.title}>
    <text x={column.x} y={column.y} className="xlt-topology-heading" style={{fill:'var(--xl-muted)',fontSize:11,fontWeight:750,letterSpacing:1.2}}>{column.title}</text>
   </g>)}
   {layout.edges.map(route=><g key={route.index} className="xlt-topology-edge"><title>{endpointLabel(route.edge.source)} → {endpointLabel(route.edge.destination)} · {route.edge.relation} · {route.edge.state} declaration; connectivity not observed</title>
    <path d={route.path} fill="none" stroke="#a2b1c5" strokeWidth="1.8" strokeDasharray={route.edge.state==='unknown'?'2 4':'6 5'} vectorEffect="non-scaling-stroke"/>
   </g>)}
   {layout.nodes.map(box=>{const node=box.node;return <g key={node.key} role="button" tabIndex={0} aria-label={`Inspect ${node.id}`} aria-pressed={node.key===selected?.key}
    onClick={()=>onSelect(node)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(node);}}} className="xlt-topology-node">
    <title>{node.id} · {node.kind}/{node.role} · mapping {node.mapping} · collector {node.collector}</title>
    <rect x={box.x} y={box.y} width={box.width} height={box.height} rx="7" className={node.key===selected?.key?'xlt-topology-selected':''}/>
    <rect x={box.x+11} y={box.y+13} width="36" height="39" rx="5" style={{fill:box.column===0?'var(--xl-selected)':box.column===1?'color-mix(in srgb, #8474bb 10%, var(--xl-bg))':'color-mix(in srgb, var(--xl-teal) 9%, var(--xl-bg))',stroke:'none'}}/>
    <ResourceGlyph column={box.column} x={box.x+19} y={box.y+22}/>
    <text x={box.x+56} y={box.y+19} className="xlt-topology-meta" style={{fontSize:9}}>{box.roleLines.map((line,i)=><tspan key={i} x={box.x+56} dy={i?11:0}>{line}</tspan>)}</text>
    <text x={box.x+56} y={box.y+38+(box.roleLines.length-1)*11} style={{fontSize:12,fontWeight:650}}>{box.nameLines.map((line,i)=><tspan key={i} x={box.x+56} dy={i?15:0}>{line}</tspan>)}</text>
    <text x={box.x+56} y={box.y+box.height-10} className="xlt-topology-meta">{`Collector: ${node.collector}`}</text>
   </g>})}
  </svg></div>}
  <p className="xlt-muted">Dashed curves are declared relationships, not observed traffic or request flow. Source → destination direction remains in the relationship tooltip and ledger. Missing endpoints remain Unknown. Devices use explicit resource_node/GPU/interface/device mappings; publisher identity remains separate.</p>
  {(data.limited||layout.limited)&&<p className="xlt-notice">The compact diagram shows at most 24 resources. Additional declarations remain in the component inventory and relationship ledger.</p>}
  {!!data.edges.length&&<details className="xlt-completed-detail"><summary>Relationship Ledger · {data.edges.length} declarations · {layout.edges.length} drawn</summary><div className="xlt-scroll"><table><thead><tr><th>Source</th><th>Destination</th><th>Relation</th><th>State / Diagram</th></tr></thead><tbody>{data.edges.map((edge,index)=>{const omitted=layout.unroutedEdges.find(value=>value.index===index);return <tr key={index}><td title={edge.source}>{endpointLabel(edge.source)}</td><td title={edge.destination}>{endpointLabel(edge.destination)}</td><td>{edge.relation}</td><td>{edge.state}<small>{omitted?.reason||'Declared path shown · connectivity not observed'}</small></td></tr>;})}</tbody></table></div></details>}
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
