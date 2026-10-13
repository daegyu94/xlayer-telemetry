import { Context, appLink } from './context';

export const WORKSPACE_PAGES = [
  {route:'demo',title:'Scenario Explorer',description:'Synthetic scenario로 전체 조사 흐름을 확인합니다.'},
  {route:'overview',title:'Run Overview',description:'완료 Step의 KPI와 기록된 실행, 관련 resource 관측을 함께 확인합니다.'},
  {route:'analyze',title:'Analyze',description:'Phase window, 비교 가능한 Worker, source별 Subsystem signal을 확인합니다.'},
  {route:'investigate',title:'Investigation',description:'Current / Baseline 변화와 Candidate의 Supporting / Counter / Missing Evidence를 조사합니다.'},
  {route:'timeline',title:'Timeline',description:'Measured span, approximate Step boundary, sampled metric의 서로 다른 시간 정밀도를 구분합니다.'},
  {route:'deep-dive',title:'Deep Dive',description:'선택한 Candidate를 기존 Subsystem / Backend Metric과 연결해 조사합니다.'},
  {route:'infrastructure',title:'Infrastructure',description:'Configured Compute / Network / Storage 구성과 관측된 Collector coverage를 확인합니다.'},
  {route:'logs',title:'Logs & Events',description:'Node-local log와 명시적으로 기록된 event를 확인합니다. Application identity와 log directory identity는 구분합니다.'},
  {route:'runs',title:'Run Explorer',description:'연결된 Subsystem과 Cluster resource를 확인하고, 조사할 Run을 선택합니다.'},
] as const;

export function themeContext(context:Context, theme:'light'|'dark'):Context {
  return {...context, theme};
}
export function navigationLink(route:string,context:Context):string {return appLink(route,context);}

/** Product destinations are separate from compatible detail routes. */
export const PRIMARY_PAGES = [
 {route:'runs',title:'Run Explorer'}, {route:'overview',title:'Run Overview'},
 {route:'investigate',title:'Investigation'}, {route:'infrastructure',title:'Infrastructure'},
] as const;
export const INVESTIGATION_PAGES = [
 {route:'investigate',title:'Candidates & evidence'}, {route:'analyze',title:'Phases & workers'},
 {route:'timeline',title:'Timeline'}, {route:'deep-dive',title:'Metrics'}, {route:'logs',title:'Logs & events'},
] as const;
export function primaryRoute(page:string):string {
 if(page==='demo')return 'demo';
 return INVESTIGATION_PAGES.some(p=>p.route===page)?'investigate':
  PRIMARY_PAGES.some(p=>p.route===page)?page:'infrastructure';
}
