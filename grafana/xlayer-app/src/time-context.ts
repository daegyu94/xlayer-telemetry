import type {Context} from './context';

export const MAX_DATE_MS=8.64e15;
export function validDateMs(value:unknown):value is number {
 return typeof value==='number'&&Number.isFinite(value)&&Math.abs(value)<=MAX_DATE_MS;
}
export function validAbsoluteWindow(start:unknown,end:unknown,maxDuration=Infinity):boolean {
 return validDateMs(start)&&validDateMs(end)&&end>start&&end-start<=maxDuration;
}
export function validTimezone(timezone:string):boolean {
 if(timezone==='browser')return true;
 try{new Intl.DateTimeFormat('en',{timeZone:timezone.toLowerCase()==='utc'?'UTC':timezone});return true;}catch{return false;}
}
/** Formatting changes presentation only; query bounds and source timestamps remain intact. */
export function formatContextTime(value:unknown,timezone:string,mode:'time'|'date-time'='date-time'):string {
 if(!validDateMs(value)||!validTimezone(timezone))return 'Time unknown';
 const timeZone=timezone==='browser'?undefined:timezone.toLowerCase()==='utc'?'UTC':timezone;
 return new Intl.DateTimeFormat('en-GB',{timeZone,hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZoneName:'short',
  ...(mode==='date-time'?{year:'numeric',month:'short',day:'2-digit'} as const:{})}).format(value);
}
/** The caller supplies Grafana's parser so date math keeps native semantics. */
export function timeContextIssue(context:Context,parse:(value:string,roundUp:boolean)=>number|undefined):string|undefined {
 if(!validTimezone(context.timezone))return 'Timezone이 유효하지 않습니다. Time range를 다시 선택하세요.';
 try{
  const from=parse(context.from,false),to=parse(context.to,true);
  if(!validDateMs(from)||!validDateMs(to))return 'Time range의 날짜가 유효하지 않습니다. 시작·종료 시간을 다시 선택하세요.';
  if(to<=from)return 'Time range의 종료 시간이 시작 시간보다 늦어야 합니다.';
 }catch{return 'Time range의 날짜가 유효하지 않습니다. 시작·종료 시간을 다시 선택하세요.';}
 return undefined;
}
