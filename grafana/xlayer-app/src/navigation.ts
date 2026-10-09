import { Context, appLink } from './context';

export const WORKSPACE_PAGES = [
  {route:'overview',title:'Overview',description:'Completed Run KPIs, recorded execution and related resource observations.',kicker:'01 / OBSERVE'},
  {route:'analyze',title:'Analyze',description:'Phase windows, comparable workers and source-qualified subsystem signals.',kicker:'02 / ANALYZE'},
  {route:'investigate',title:'Investigate',description:'Current / baseline changes, candidates and supporting, counter or missing evidence.',kicker:'03 / DIAGNOSE'},
  {route:'timeline',title:'Timeline',description:'Measured spans, approximate Step boundaries and sampled metrics retain distinct precision.',kicker:'04 / CORRELATE'},
  {route:'deep-dive',title:'Deep Dive',description:'Inspect the selected candidate across existing subsystem and backend metric layers.',kicker:'05 / DEEP DIVE'},
  {route:'infrastructure',title:'Infrastructure',description:'Explore configured compute, network and storage resources with observed collector coverage.',kicker:'06 / INFRASTRUCTURE'},
  {route:'logs',title:'Logs & Events',description:'Node-local logs and explicit recorded events; application and log directory identities stay separate.',kicker:'07 / LOGS'},
] as const;

export function themeContext(context:Context, theme:'light'|'dark'):Context {
  return {...context, theme};
}
export function navigationLink(route:string,context:Context):string {return appLink(route,context);}
