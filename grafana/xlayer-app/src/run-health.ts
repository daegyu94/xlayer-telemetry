import {RecordRow, numeric} from './context';
import {completedStepChoices} from './completed-steps';

/** Only rank within the same execution boundary and full producer identity. */
export function slowSteps(rows:RecordRow[], selected?:RecordRow):RecordRow[] {
 const keys=['cluster','run_id','observer_node','node','producer','role','worker_id','boundary_scope','execution_mode'];
 const cohort=(row:RecordRow)=>JSON.stringify(keys.map(key=>row[key]??null));
 const valid=completedStepChoices(rows).filter(row=>numeric(row.step_duration_seconds)!==undefined&&Number(row.step_duration_seconds)>=0);
 const anchor=selected||valid[0];
 if(!anchor||!anchor.boundary_scope)return [];
 if(!selected&&new Set(valid.map(cohort)).size!==1)return [];
 return valid.filter(row=>cohort(row)===cohort(anchor)).sort((a,b)=>Number(b.step_duration_seconds)-Number(a.step_duration_seconds));
}

export function investigationStatus(candidates:RecordRow[], state?:string, clock?:string, diagnosed=false, available=true) {
 if(!available)return {label:'Diagnosis unavailable',tone:'unknown',note:'선택적 diagnosis source가 설정되지 않았습니다. Metrics와 완료 이력에서 조사를 시작하세요.'};
 if(state==='Error')return {label:'Diagnosis query failed',tone:'unknown',note:'저장 진단을 읽지 못했습니다. Query coverage에서 오류를 확인하세요.'};
 if(!state||state==='Loading'||state==='NotStarted')return {label:'Loading evidence',tone:'unknown',note:'선택한 구간의 진단을 확인하고 있습니다.'};
 if(clock==='unknown'||clock==='unsafe')return {label:'Correlation withheld',tone:'unknown',note:'Clock 검증이 부족합니다. Raw signal은 볼 수 있지만 구간 간 원인 비교는 보류합니다.'};
 const strong=candidates.filter(c=>c.state==='strong_signal');
 if(strong.length)return {label:`${strong.length} bottleneck candidate${strong.length===1?'':'s'}`,tone:'attention',note:'강한 supporting signal이 기록됐습니다. 반대 근거와 누락 정보를 함께 검토하세요.'};
 if(candidates.some(c=>c.state==='missing_evidence'))return {label:'Evidence incomplete',tone:'unknown',note:'판정에 필요한 관측이 부족합니다. 누락된 source와 scope를 먼저 확인하세요.'};
 if(candidates.length)return {label:'Review recorded signals',tone:'observed',note:'저장된 후보를 검토하세요. 후보 상태는 workload 전체의 health 판정이 아닙니다.'};
 if(diagnosed)return {label:'No candidates reported',tone:'observed',note:'저장된 진단에서 후보가 보고되지 않았습니다. Coverage와 누락 정보를 확인하세요. 전체 workload의 정상 판정은 아닙니다.'};
 return {label:'No diagnosis recorded',tone:'unknown',note:'이 구간의 진단이 없습니다. 기록 부재를 정상 상태로 해석하지 않습니다.'};
}

/** Choose a useful starting panel; its canonical query still owns scope/units. */
export function candidateMetric(component:unknown):string|undefined {
 return ({compute:'GPU',gpu:'GPU',rollout:'vLLM',vllm:'vLLM',ray:'Ray',sandbox:'Sandbox',storage:'Connector RPC',network:'Network'} as Record<string,string>)[String(component)];
}
