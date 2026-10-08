import React from 'react';
import type {Page} from './pages';

export const WORKSPACE_COPY: Record<Page, {title:string;description:string;icon:string}> = {
 overview:{title:'Run Overview',description:'실행 context, 에이전트 RL과 인프라 관측을 한눈에 확인하세요.',icon:'home'},
 analyze:{title:'Analyze',description:'Agent RL의 단계와 subsystem 관측을 비교하고 느린 worker를 조사하세요.',icon:'chart'},
 investigate:{title:'Investigate',description:'현재 구간과 baseline, timeline, supporting·counter·missing evidence를 비교하세요.',icon:'search'},
 'deep-dive':{title:'Deep Dive',description:'선택한 candidate의 실제 metric과 관측 근거를 심층 조사하세요.',icon:'database'},
 infrastructure:{title:'Infrastructure / 클러스터 인프라',description:'설정된 compute·network·storage 구성과 실제 resource 관측을 확인하세요.',icon:'topology'},
 logs:{title:'Logs & Events',description:'원본 로그와 event를 검색하고 Run·Step context로 이동하세요.',icon:'logs'},
 timeline:{title:'Cross-Layer Timeline',description:'정밀도가 다른 span·Step·sample 관측을 같은 구간에서 확인하세요.',icon:'chart'},
};
const paths:Record<string,string>={
 home:'M3 11 12 3l9 8M5 10v11h5v-7h4v7h5V10',
 chart:'M3 3v18h18M6 15l5-7 4 4 6-9',
 search:'M16 16l6 6M19 10a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
 database:'M3 5c0-5 18-5 18 0s-18 5-18 0v14c0 5 18 5 18 0V5M3 12c0 5 18 5 18 0',
 topology:'M12 6v6M5 18v-6h14v6M8 3h8v5H8zM2 18h6v5H2zM9 18h6v5H9zM16 18h6v5h-6z',
 logs:'M5 2h14v20H5zM8 7h8M8 12h8M8 17h8',
 cpu:'M5 5h14v14H5zM9 9h6v6H9zM8 1v4M16 1v4M8 19v4M16 19v4M1 8h4M1 16h4M19 8h4M19 16h4',
 user:'M18 7a6 6 0 1 1-12 0 6 6 0 0 1 12 0M2 23v-4c0-6 20-6 20 0v4',
 pulse:'M1 13h5l3-10 6 18 3-8h5',
 arrow:'M5 12h14M13 6l6 6-6 6',
};
export function WorkspaceIcon({name,size=24}:{name:string;size?:number}){
 return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]||paths.database}/></svg>;
}
export function WorkspaceMark(){return <svg aria-hidden="true" viewBox="0 0 40 34" width="36" height="32"><path fill="#02c7ee" d="M1 1h14l8 9-9 10zM28 1h12L14 33H1zM25 24l9 9H20z"/></svg>;}
export function signalTitle(signal:string){
 const names:Record<string,string>={step_duration_seconds:'Step time',rollout_duration_seconds:'Rollout duration',gpu_utilization_percent:'GPU utilization',threefs_p99_latency:'3FS max reported p99',threefs_throughput_bytes_per_second:'3FS reported throughput',storage_device_busy_ratio:'Device busy',vllm_requests_waiting:'vLLM waiting',network_utilization_ratio:'Network utilization'};
 return names[signal]||signal;
}
