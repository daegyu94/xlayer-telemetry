import type {Destination,RecordRow} from './context';

export function boundaryPresentation(row?:RecordRow){
 if(row?.boundary_scope==='trainer_update')return {label:'Trainer update',timeLabel:'Update time',note:'Async trainer-update boundary; concurrent rollout/tool execution is not included or owned by this update.'};
 if(row?.boundary_scope==='rl_step')return {label:'Step',timeLabel:'Step time',note:''};
 return {label:'Observation',timeLabel:'Reported duration',note:'Boundary scope is not reported; step/rollout ownership is not inferred.'};
}
export function entitySelectionHint(labels:Record<string,string>[]){
 const differs=(keys:string[])=>keys.some(key=>new Set(labels.map(row=>row[key])).size>1);
 if(differs(['run_id']))return 'Choose Run';
 if(differs(['worker_id']))return 'Choose Worker';
 if(differs(['gpu','gpu_uuid']))return 'Choose GPU';
 if(differs(['engine','engine_id','instance','model_name']))return 'Choose Engine / endpoint';
 if(differs(['node','nodename']))return 'Choose resource node';
 if(differs(['producer','role']))return 'Choose producer / role';
 if(differs(['verl_stage','phase','reported_key']))return 'Distinct reported stages · select a completed record';
 return 'Distinct label entities · no implicit aggregation';
}
export function compactEntity(labels:Record<string,string>){
 const node=labels.node||labels.nodename;
 const detail=labels.gpu!==undefined?`GPU ${labels.gpu}`:labels.device||labels.worker_id||labels.engine||labels.State||labels.operation;
 return [node,detail].filter(Boolean).join(' · ')||labels.instance||labels.SessionName||'Entity not reported';
}
export const DEEP_DIVE_SPECS:Array<{label:string;dashboard?:Destination;panel?:number;note:string}>=[
 {label:'Connector RPC',dashboard:'stage',panel:60,note:'Store RPC p95 · seconds · operation/status/engine · rolling shared-service context.'},
 {label:'DFS batch',dashboard:'stage',panel:66,note:'Client DFS batch read/write and D2H staging p95 · microseconds converted to seconds · distinct operations.'},
 {label:'DFS bytes',dashboard:'stage',panel:64,note:'Successful client KV-key bytes/s; not physical block-device bandwidth.'},
 {label:'Failures',dashboard:'stage',panel:67,note:'Failed/skipped keys, error RPC and master admission requests are separate populations; never summed.'},
 {label:'3FS evidence',note:'Saved diagnosis · maximum reported per-entity p99; raw unit must be reported. No direct ClickHouse query is added.'},
 {label:'Local I/O mean',dashboard:'storage',panel:30,note:'Node/device completed I/O mean · seconds/operation; not connector/3FS p99 or Run-attributed I/O.'},
 {label:'GPU',dashboard:'compute',panel:2,note:'Sampled device utilization; not workload MFU.'},
 {label:'vLLM',dashboard:'stage',panel:9,note:'Sampled shared-engine waiting queue.'},
 {label:'Ray',dashboard:'stage',panel:22,note:'Shared session task states; not attributed to this update.'},
 {label:'Sandbox',dashboard:'stage',panel:12,note:'Worker/cgroup I/O pressure; not a physical SSD latency.'},
];
export function detailTabIndex(label?:string):number{return Math.max(0,DEEP_DIVE_SPECS.findIndex(spec=>spec.label===label));}
