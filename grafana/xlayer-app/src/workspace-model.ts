import type {RecordRow} from './context';
import {phaseWindow} from './semantics';

/** Each observed call keeps its execution identity; never union overlapping workers. */
export function timelineCalls(spans:RecordRow[],selected:RecordRow|undefined){
 if(!selected)return [];
 return spans.map(span=>({span,window:phaseWindow([span],selected,String(span.phase||''))}))
  .filter(row=>row.window.status==='observed')
  .sort((a,b)=>(a.window.start||0)-(b.window.start||0));
}
