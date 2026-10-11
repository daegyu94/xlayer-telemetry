import type {PanelData} from '@grafana/data';

/** Reserve native header/legend/axis space as well as readable state lanes. */
export function timelinePanelHeight(data:PanelData|undefined){
 const lanes=(data?.series||[]).reduce((total,frame)=>total+frame.fields.filter(field=>field.type!=='time').length,0);
 return Math.max(320,120+lanes*30);
}
