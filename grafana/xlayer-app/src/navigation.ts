import { Context, appLink } from './context';

export const WORKSPACE_PAGES = [
  {route:'overview',title:'Overview',description:'완료 Step의 KPI와 기록된 실행, 관련 resource 관측을 함께 확인합니다.',kicker:'01 / OBSERVE'},
  {route:'analyze',title:'Analyze',description:'Phase window, 비교 가능한 Worker, source별 Subsystem signal을 확인합니다.',kicker:'02 / ANALYZE'},
  {route:'investigate',title:'Investigate',description:'Current / Baseline 변화와 Candidate의 Supporting / Counter / Missing Evidence를 조사합니다.',kicker:'03 / DIAGNOSE'},
  {route:'timeline',title:'Timeline',description:'Measured span, approximate Step boundary, sampled metric의 서로 다른 시간 정밀도를 구분합니다.',kicker:'04 / CORRELATE'},
  {route:'deep-dive',title:'Deep Dive',description:'선택한 Candidate를 기존 Subsystem / Backend Metric과 연결해 조사합니다.',kicker:'05 / DEEP DIVE'},
  {route:'infrastructure',title:'Infrastructure',description:'Configured Compute / Network / Storage 구성과 관측된 Collector coverage를 확인합니다.',kicker:'06 / INFRASTRUCTURE'},
  {route:'logs',title:'Logs & Events',description:'Node-local log와 명시적으로 기록된 event를 확인합니다. Application identity와 log directory identity는 구분합니다.',kicker:'07 / LOGS'},
] as const;

export function themeContext(context:Context, theme:'light'|'dark'):Context {
  return {...context, theme};
}
export function navigationLink(route:string,context:Context):string {return appLink(route,context);}
