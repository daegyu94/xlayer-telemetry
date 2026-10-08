import type {Context,Destination} from './context';
import {APP_BASE} from './context';
import {DEEP_DIVE_SPECS} from './presentation';
import {MATRIX_SPECS} from './matrix-contract';

export type UiVersion='classic'|'workspace';
export const PAGES=['overview','analyze','investigate','deep-dive','infrastructure','logs'] as const;
export type Page=(typeof PAGES)[number]|'timeline';
export type PanelRef={dashboard:Destination;panel:number;refs?:string[]};
export const PAGE_LABELS:Record<Page,string>={overview:'Overview',analyze:'Analyze',investigate:'Investigate','deep-dive':'Deep Dive',infrastructure:'Infrastructure',logs:'Logs & Events',timeline:'Timeline'};
export const INFRASTRUCTURE_PANELS:PanelRef[]=[
 {dashboard:'compute',panel:1},{dashboard:'compute',panel:6},{dashboard:'compute',panel:30},
 {dashboard:'compute',panel:8},{dashboard:'compute',panel:9},{dashboard:'compute',panel:42},
 {dashboard:'storage',panel:111},{dashboard:'storage',panel:112},{dashboard:'storage',panel:113},
 {dashboard:'storage',panel:114},{dashboard:'storage',panel:115},{dashboard:'storage',panel:1},
 {dashboard:'storage',panel:2},{dashboard:'storage',panel:30},{dashboard:'storage',panel:31},
 {dashboard:'storage',panel:4},
];
export const LOGS_PANELS:PanelRef[]=[{dashboard:'logs',panel:1},{dashboard:'timeline',panel:10},{dashboard:'timeline',panel:2},{dashboard:'timeline',panel:3}];
// One inventory for both presentations; SQL/PromQL/LogQL remain canonical.
export function pagePanels(page:Page):PanelRef[]{
 if(page==='infrastructure')return [{dashboard:'compute',panel:70},{dashboard:'compute',panel:71},{dashboard:'compute',panel:72},...INFRASTRUCTURE_PANELS];
 if(page==='logs')return LOGS_PANELS;
 if(page==='deep-dive')return [...DEEP_DIVE_SPECS.filter(s=>s.dashboard&&s.panel!==undefined).map(s=>({dashboard:s.dashboard!,panel:s.panel!})),...[101,102,103,104,111,112,113,114,115].map(panel=>({dashboard:'storage' as const,panel}))];
 if(page==='analyze')return Object.values(MATRIX_SPECS).map(s=>({dashboard:s.dashboard,panel:s.panel,refs:s.refs}));
 if(page==='investigate')return [...[2,3,4,6].map(panel=>({dashboard:'summary' as const,panel})),{dashboard:'timeline',panel:2}];
 if(page==='timeline')return [2,3,4,5,9,10].map(panel=>({dashboard:'timeline',panel}));
 return [{dashboard:'overview',panel:30},{dashboard:'timeline',panel:2},{dashboard:'timeline',panel:10},{dashboard:'stage',panel:2},{dashboard:'overview',panel:31},{dashboard:'overview',panel:34},{dashboard:'stage',panel:4},{dashboard:'overview',panel:33},{dashboard:'stage',panel:28}];
}
export function versionFromPath(path:string):UiVersion{return path.startsWith(APP_BASE+'/v2')?'workspace':'classic';}
export function switchVersion(context:Context,version:UiVersion):Context{return {...context,uiVersion:version};}
export function sceneRoutes(){return (['classic','workspace'] as const).flatMap(version=>[...PAGES,'timeline' as const].map(page=>({version,page,path:`${APP_BASE}${version==='workspace'?'/v2':''}/${page}`,panels:pagePanels(page)})));}
