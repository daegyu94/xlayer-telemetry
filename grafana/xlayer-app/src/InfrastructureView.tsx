import React, { useId, useState } from 'react';
import { Context, appLink } from './context';
import { Resource, infrastructure, resourceSelection } from './infrastructure';
import { topologyLayout, TopologyGroup } from './topology-layout';

function ResourceGlyph({node}:{node:Resource}) {
 const column=node.type==='fabric'?1:node.kind==='storage'||node.type==='ssd'?2:0;
 return <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
  {column===0?<><rect x="4" y="4" width="12" height="12" rx="2"/><rect x="7" y="7" width="6" height="6" rx="1"/>{[7,13].map(value=><path key={value} d={`M ${value} 1 V 4 M ${value} 16 V 19 M 1 ${value} H 4 M 16 ${value} H 19`}/>)}</>
   :column===1?<><rect x="6" y="1" width="8" height="5" rx="1"/><path d="M 10 6 V 11 M 3 11 H 17 M 3 11 V 14 M 17 11 V 14"/><rect x="0" y="14" width="6" height="5" rx="1"/><rect x="14" y="14" width="6" height="5" rx="1"/></>
   :<><rect x="2" y="2" width="16" height="7" rx="1.5"/><rect x="2" y="11" width="16" height="7" rx="1.5"/><path d="M 5 5.5 H 11 M 5 14.5 H 11 M 15 5.5 H 15.1 M 15 14.5 H 15.1"/></>}
 </svg>;
}
function CollectorState({node,compact=false}:{node:Resource;compact?:boolean}) {
 const labels={available:'Available',stale:'Stale',down:'Down',unknown:'Unknown',ambiguous:'Ambiguous'};
 return <span className="xlt-cluster-map-state" data-state={node.collector} title={`Collector ${node.collector} · scrape 응답과 sample age로 확인한 수집 상태입니다. 장비나 workload가 정상이라는 뜻은 아닙니다.`}>{compact?labels[node.collector]:`Collector ${node.collector}`}</span>;
}
function ResourceCard({node,selectedKey,onSelect}:{node:Resource;selectedKey?:string;onSelect:(node:Resource)=>void}) {
 return <button className="xlt-cluster-map-node" data-kind={node.type==='fabric'?'network':node.kind} data-resource-key={node.key} aria-label={`Inspect ${node.id}`} aria-pressed={selectedKey===node.key} onClick={()=>onSelect(node)}>
  <span className="xlt-cluster-map-icon"><ResourceGlyph node={node}/></span>
  <span className="xlt-cluster-map-node-text"><strong>{node.id}</strong><small>{node.cluster} · {node.kind}/{node.role}</small><small>Mapping {node.mapping}</small></span>
  <CollectorState node={node} compact/>
 </button>;
}
export type InfrastructureModel=ReturnType<typeof infrastructure>;
export function InfrastructureView({data,context,onSelect,metrics,links,unavailable}:{data:InfrastructureModel;context:Context;onSelect:(node:Resource)=>void;metrics:(node:Resource)=>React.ReactNode;links:(node:Resource,c:Context)=>React.ReactNode;unavailable?:string}) {
 const id=useId().replace(/:/g,''),[expanded,setExpanded]=useState(false),[search,setSearch]=useState('');
 const model=topologyLayout(data.nodes,data.edges,{expanded,selectedKey:context.variables.infra_component?.[0]});
 const selected=model.selectedResource,group=(name:TopologyGroup['id'])=>model.groups.find(item=>item.id===name)!;
 const devices=selected?.resource?data.nodes.filter(node=>node.cluster===selected.cluster&&node.resource===selected.resource&&node.kind===selected.kind):[];
 const storageTotal=group('mds').total+group('ds').total+group('storageUnknown').total;
 const card=(node:Resource)=><ResourceCard key={node.key} node={node} selectedKey={selected?.key} onSelect={onSelect}/>;
 const showGroup=(value:TopologyGroup,gridClass='')=><><div className={`xlt-cluster-map-nodes ${gridClass}`}>{value.visible.map(card)}</div>{!value.total&&<p className="xlt-cluster-map-empty">반환된 inventory에 Configured 항목이 없습니다.</p>}{value.hiddenCount>0&&!expanded&&<button className="xlt-cluster-map-more" onClick={()=>setExpanded(true)}>+ {value.hiddenCount} 건 더 있음 · 전체 보기: {value.title}</button>}</>;
 const availability=data.nodes.filter(node=>!['gpu','nic','ssd'].includes(node.type));
 const available=availability.filter(node=>node.collector==='available').length,stale=availability.filter(node=>node.collector==='stale').length;
 const unknown=availability.filter(node=>node.collector==='unknown').length,down=availability.filter(node=>node.collector==='down').length,ambiguous=availability.filter(node=>node.collector==='ambiguous').length;
 const inventory=data.nodes.filter(node=>`${node.id} ${node.cluster} ${node.kind} ${node.role} ${node.resource||''}`.toLowerCase().includes(search.toLowerCase()));
 const endpoint=(key:string)=>{const node=data.nodes.find(value=>value.key===key);if(node)return `${node.cluster} / ${node.kind} / ${node.id}`;try{const identity=JSON.parse(key);return Array.isArray(identity)?identity.join(' / '):key;}catch{return key;}};
 return <div className="xlt-infrastructure">
  {!!data.nodes.length&&<div className="xlt-cluster-map-overview">
   <div><small>Compute</small><strong>{group('compute').total} declarations</strong><span>설정된 Compute component</span></div>
   <div><small>Network</small><strong>{group('network').total} identities</strong><span>cluster / namespace / component key별 identity를 구분합니다.</span></div>
   <div><small>Storage</small><strong>{group('mds').total} MDS · {group('ds').total} DS</strong><span>{group('storageUnknown').total} 건은 Storage role이 확인되지 않았습니다.</span></div>
   <div><small>Collector evidence</small><strong>{available} available · {stale} stale</strong><span>{unknown} unknown · {down} down · {ambiguous} ambiguous · 장비 정상 상태를 뜻하지 않습니다.</span></div>
  </div>}
  <div className="xlt-infra-workspace">
   <section className="xlt-cluster-map" aria-labelledby={`${id}-map-title`}>
    <header className="xlt-cluster-map-header"><div><h3 id={`${id}-map-title`}>Configured Resource Topology</h3><p>Compact Layered Cluster Map · Configured inventory를 표시합니다. Physical connectivity는 확인되지 않았습니다.</p></div><div className="xlt-cluster-map-mode" role="group" aria-label="Topology density"><button aria-pressed={!expanded} onClick={()=>setExpanded(false)}>Compact</button><button aria-pressed={expanded} onClick={()=>setExpanded(true)}>Expanded</button></div></header>
    {unavailable&&<p className="xlt-empty">{unavailable} · inventory를 확인할 수 없습니다. 정상 상태를 추정하지 않습니다.</p>}
    {!data.nodes.length?<p className="xlt-empty">이 시간 범위에 topology inventory가 없습니다. Configured topology를 연결한 뒤 resource를 선택하세요.</p>:<div className="xlt-cluster-map-body">
     <div className="xlt-cluster-map-layer" data-layer="compute"><div className="xlt-cluster-map-layer-header"><h4>01 · Compute / GPU &amp; Rollout</h4><span>{group('compute').visible.length} / {group('compute').total} declarations</span></div>{showGroup(group('compute'),'xlt-cluster-map-compute-grid')}</div>
     <div className="xlt-cluster-map-grouping" role="note"><span>Layout grouping · 관측된 network hop이 아닙니다</span></div>
     <div className="xlt-cluster-map-layer" data-layer="network"><div className="xlt-cluster-map-layer-header"><h4>02 · Network / Configured identities</h4><span>{group('network').visible.length} / {group('network').total} identities</span></div>{showGroup(group('network'),'xlt-cluster-map-network-grid')}<p className="xlt-cluster-map-scope">이름이 같아도 cluster / namespace별 identity를 구분합니다. 동일한 physical Fabric으로 확인된 관계가 아닙니다.</p></div>
     <div className="xlt-cluster-map-grouping" role="note"><span>Layout grouping · 관측된 network hop이 아닙니다</span></div>
     <div className="xlt-cluster-map-layer" data-layer="storage"><div className="xlt-cluster-map-layer-header"><h4>03 · Storage / MDS + DS</h4><span>{storageTotal} declarations</span></div><div className="xlt-cluster-map-storage-groups">
      <div><h5>Metadata Servers · {group('mds').total}</h5>{showGroup(group('mds'))}</div>
      <div><h5>Data Servers · {group('ds').total}</h5>{showGroup(group('ds'),'xlt-cluster-map-storage-grid')}</div>
     </div>{group('storageUnknown').total>0&&<div className="xlt-cluster-map-unclassified"><h5>Unclassified Storage Roles · {group('storageUnknown').total}</h5>{showGroup(group('storageUnknown'),'xlt-cluster-map-storage-grid')}</div>}</div>
     {group('other').total>0&&<div className="xlt-cluster-map-layer" data-layer="other"><div className="xlt-cluster-map-layer-header"><h4>Other Configured Components</h4><span>{group('other').total} declarations</span></div>{showGroup(group('other'),'xlt-cluster-map-compute-grid')}</div>}
     {model.selectedDevice&&selected&&<div className="xlt-cluster-map-selected-device"><h5>Selected Device · explicit resource mapping</h5>{card(selected)}</div>}
    </div>}
    <p className="xlt-cluster-map-boundary"><strong>Measurement boundary.</strong> 계층 순서는 화면 배치를 위한 구분입니다. 실제 운영자가 선언한 edge는 Relationship Ledger에서 확인합니다. Collector available은 node 정상 상태, physical link 또는 Run별 I/O ownership을 보장하지 않습니다.</p>
    {(data.limited||model.inventoryLimited)&&<p className="xlt-notice">반환된 inventory와 화면 표시는 최대 256개 component로 제한합니다. 표시 한도로 생략된 선언과 missing metric sample을 구분하세요.</p>}
   </section>
   <aside className="xlt-resource-inspector" aria-label="Resource inspector">
    <div className="xlt-resource-inspector-header"><small>RESOURCE INSPECTOR</small>{selected?<><h3>Selected Resource · {selected.id}</h3><CollectorState node={selected}/></>:<h3>Select a resource</h3>}</div>
    {!selected?<p className="xlt-empty">Map이나 Inventory에서 구성된 component를 선택하세요. Resource를 자동 선택하거나 metric 값을 추정하지 않습니다.</p>:<div className="xlt-resource-inspector-body">
     <dl className="xlt-resource-inspector-identity">{[['Identity',selected.id],['Cluster',selected.cluster],['Namespace',selected.kind],['Type',selected.type],['Role',selected.role],['Mapping',selected.mapping],['Resource node',selected.resource||'Unknown'],['Device',selected.gpu!==undefined?`GPU ${selected.gpu}`:selected.interface||selected.device||'Not configured'],['Collector',selected.collector],['Relation','Configured · physical path는 확인되지 않았습니다.']].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
     {selected.mapping!=='configured'?<p className="xlt-empty">Resource mapping이 없거나 서로 충돌합니다. Publisher identity로 resource query나 device attribution을 생성하지 않습니다.</p>:<>
      <label className="xlt-resource-inspector-select">Node / device<select aria-label="Infrastructure resource" value={selected.key} onChange={event=>{const node=data.nodes.find(value=>value.key===event.target.value);if(node)onSelect(node);}}>{devices.map(node=><option key={node.key} value={node.key}>{node.id} · {node.type}</option>)}</select></label>
      <div className="xlt-device-map"><b>{selected.resource} · 선언된 resource mapping</b><div>{devices.filter(node=>node.type!=='node').map(node=><button key={node.key} aria-pressed={node.key===selected.key} onClick={()=>onSelect(node)}>{node.gpu!==undefined?`GPU ${node.gpu}`:node.interface||node.device||node.id}<small>{node.type} · configured</small></button>)}</div></div>
      <div className="xlt-resource-inspector-links">{links(selected,resourceSelection(context,selected))}<a href={appLink('investigate',resourceSelection(context,selected))}>선택한 Step 조사 →</a><a href={`#${id}-metrics`}>Resource metric 보기 ↓</a></div>
     </>}
     <a className="xlt-resource-inspector-inventory-link" href={`#${id}-inventory`} onClick={()=>{const details=document.getElementById(`${id}-inventory`);if(details instanceof HTMLDetailsElement)details.open=true;}}>Component Inventory에서 확인 ↓</a>
     <p className="xlt-cluster-map-scope">Node / device / shared-service scope를 구분합니다. Availability만으로 metric coverage, exact phase attribution 또는 causality를 판단하지 않습니다.</p>
    </div>}
   </aside>
  </div>
  {selected&&selected.mapping==='configured'&&<section className="xlt-infra-resource-metrics" id={`${id}-metrics`}><h3>Cluster Resource Metrics · {selected.id}</h3><p className="xlt-muted">기존 panel의 node / device / interface / service scope를 유지합니다. 서로 다른 I/O 계층을 하나의 request path로 해석하지 않으며 Missing / Stale / Query Failure / 측정값 0을 구분합니다.</p><div className="xlt-infra-metrics">{metrics(selected)}</div></section>}
  {!!model.relations.length&&<details className="xlt-completed-detail"><summary>Relationship Ledger · {model.relations.length} declarations</summary><div className="xlt-scroll"><table><thead><tr><th>Source</th><th>Destination</th><th>Relation</th><th>Scope / Evidence</th></tr></thead><tbody>{model.relations.map(item=><tr key={item.index}><td>{endpoint(item.edge.source)}</td><td>{endpoint(item.edge.destination)}</td><td>{item.edge.relation}</td><td>{item.state}<small>{item.source&&item.destination?'운영자가 선언한 관계입니다. Physical connectivity는 관측하지 않았습니다.':'반환된 inventory에 endpoint가 없습니다.'}</small></td></tr>)}</tbody></table></div></details>}
  <details className="xlt-completed-detail" id={`${id}-inventory`}><summary>Component Inventory · {data.nodes.length} declarations</summary><label className="xlt-infra-inventory-search">Component / role 검색<input aria-label="Search infrastructure inventory" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Component, cluster, role 또는 resource node"/></label><div className="xlt-scroll"><table><thead><tr><th>Component</th><th>Resource identity</th><th>Scope / Quality</th><th>Next</th></tr></thead><tbody>{inventory.map(node=><tr key={node.key}><td>{node.id}<small>{node.cluster} · {node.kind}/{node.role}</small></td><td>{node.resource||'Unknown'}<small>{node.gpu!==undefined?`GPU ${node.gpu}`:node.interface||node.device||''}</small></td><td>Configured · {node.mapping}<small>Collector {node.collector}</small></td><td><button onClick={()=>onSelect(node)}>Resource 선택 →</button></td></tr>)}</tbody></table></div></details>
 </div>;
}
