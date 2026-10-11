import {Context,readContext,numeric} from './context';
import {validAbsoluteWindow} from './time-context';
export type DemoScenario={id:string;title:string;category:string;mode:string;question:string;expected:string;workflow:string[];expected_results?:{duration?:number;candidate?:string|null;runs?:number;boundary?:string;composition:string[]}};
export type DemoObservation={scenario:string;step:number;boundary_scope:string;context:Record<string,string>};
export type DemoCatalog={schema_version:1;data_origin:'synthetic';selected:string;cycle:number;status:string;generation?:number;error?:string;validation?:Record<string,{state:string;checks:string[];message?:string}>;scenarios:DemoScenario[];observations:DemoObservation[];limitations:string;boundary_tests:{id:string;title:string;runner:string;layer:string}[]};
export function parseDemoCatalog(raw:any):DemoCatalog {
 if(raw?.schema_version!==1||raw.data_origin!=='synthetic'||!Array.isArray(raw.scenarios)||raw.scenarios.length>12||!Array.isArray(raw.observations)||raw.observations.length>28)throw new Error('Invalid synthetic scenario catalog');
 if(raw.scenarios.some((s:any)=>!s||typeof s.id!=='string'||!/^[a-z-]{1,40}$/.test(s.id)||['title','category','question','expected'].some(k=>typeof s[k]!=='string')||!Array.isArray(s.workflow)||s.workflow.some((v:any)=>typeof v!=='string')))throw new Error('Invalid scenario definition');
 if(new Set(raw.scenarios.map((s:any)=>s.id)).size!==raw.scenarios.length)throw new Error('Duplicate scenario identity');
 if(!Array.isArray(raw.boundary_tests)||raw.boundary_tests.length>12||raw.boundary_tests.some((r:any)=>!r||['id','title','runner','layer'].some(k=>typeof r[k]!=='string')))throw new Error('Invalid scenario validation coverage');
 return {...raw,observations:raw.observations.filter((r:any)=>r&&raw.scenarios.some((s:any)=>s.id===r.scenario)&&Number.isSafeInteger(r.step)&&r.step>=0&&r.context&&['run_id','cluster','source_node','record_id','from','to'].every(k=>typeof r.context[k]==='string'&&r.context[k].trim().length>0)&&['run_id','cluster','source_node','record_id'].every(k=>!['.*','$__all','*'].includes(r.context[k]))&&validAbsoluteWindow(numeric(r.context.from),numeric(r.context.to)))};
}
export function demoObservationContext(row:DemoObservation,current:Context):Context {
 // Switching scenarios starts a new investigation. No old worker/candidate leaks.
 const context=readContext('');const c=row.context;
 return {...context,from:c.from,to:c.to,timezone:current.timezone,theme:current.theme,variables:{cluster:[c.cluster],run_id:[c.run_id],source_node:[c.source_node],node:[c.source_node],record_id:[c.record_id],...(c.demo_generation?{demo_generation:[c.demo_generation]}:{})}};
}

export function demoLandingContext(current:Context):Context {
 return {...readContext(''),theme:current.theme,timezone:current.timezone};
}
