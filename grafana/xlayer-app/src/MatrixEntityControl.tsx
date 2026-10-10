import React from 'react';

export function MatrixEntityControl({title,selectedKey,entities,onChange}:{title:string;selectedKey?:string;
 entities:{key:string;label:string}[];onChange:(value:string)=>void}){
 const unavailable=!!selectedKey&&!entities.some(row=>row.key===selectedKey);
 return <>{(entities.length>1||!!selectedKey)&&<select className="xlt-entity-select" aria-label={`${title} entity`} value={selectedKey||''} onChange={event=>onChange(event.target.value)}>
  <option value="">Entity를 선택하세요 ({entities.length})</option>
  {unavailable&&<option value={selectedKey}>선택한 Entity가 현재 구간에 없습니다</option>}
  {entities.map(row=><option key={row.key} value={row.key}>{row.label}</option>)}
 </select>}{unavailable&&<small role="status">선택한 Entity가 현재 filter에 없습니다. <button onClick={()=>onChange('')}>선택 해제</button></small>}</>;
}
