import {DASHBOARD_UIDS,type Destination} from './context';
import type {Catalog,Dashboard} from './catalog';

export type DashboardIssue={state:'not-provisioned'|'query-failed';status?:number};
const REQUIRED:Destination[]=['overview','stage','compute','storage'];
export class CatalogLoadError extends Error {
 constructor(public issues:Partial<Record<Destination,DashboardIssue>>){
  super('필수 Dashboard를 불러오지 못했습니다: '+REQUIRED.filter(key=>issues[key]).map(key=>`${DASHBOARD_UIDS[key]} (${issueText(issues[key]!)})`).join(', ')+'. Provisioning과 Grafana 권한·상태를 확인하세요.');
 }
}
export function issueText(issue:DashboardIssue):string {
 return issue.state==='not-provisioned'?'not provisioned':issue.status?`HTTP ${issue.status}`:'query failed';
}
/** Each native request has its own failure boundary; optional sources cannot reject core. */
export async function loadDashboardCatalog(request:(uid:string)=>Promise<{dashboard?:Dashboard}>):Promise<Catalog> {
 const issues:Partial<Record<Destination,DashboardIssue>>={};
 const entries=await Promise.all((Object.entries(DASHBOARD_UIDS) as [Destination,string][]).map(async([key,uid])=>{
  try{
   const result=await request(uid);
   if(!result?.dashboard){issues[key]={state:'query-failed'};return [key,undefined];}
   return [key,result.dashboard];
  }catch(error:any){
   const status=typeof error?.status==='number'?error.status:undefined;
   issues[key]={state:status===404?'not-provisioned':'query-failed',...(status?{status}:{})};
   return [key,undefined];
  }
 }));
 const catalog=Object.fromEntries(entries) as Catalog;
 if(REQUIRED.some(key=>!catalog[key]))throw new CatalogLoadError(issues);
 return {...catalog,issues};
}
