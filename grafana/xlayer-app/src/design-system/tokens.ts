import {GrafanaTheme2,colorManipulator} from '@grafana/data';

/** Semantic roles come from Grafana. Verify text against every XLayer surface. */
export function designTokens(theme:GrafanaTheme2){
 const c=theme.colors;
 const surfaces={bg:c.background.primary,soft:c.background.secondary,canvas:c.background.canvas,selected:c.background.secondary};
 const neutral=theme.isDark?{text:"#f1f5f9",secondary:"#d5deeb",muted:"#b8c5d8"}
  :{text:"#111827",secondary:"#1f2937",muted:"#374151"};
 const readable=(color:string,minimum=4.5)=>Object.values(surfaces).every(bg=>colorManipulator.getContrastRatio(color,bg,c.background.canvas)>=minimum)?color:c.text.primary;
 return {...surfaces,text:readable(neutral.text,7),secondary:readable(neutral.secondary,7),muted:readable(neutral.muted,7),
  primary:readable(c.primary.text),warning:readable(c.warning.text),danger:readable(c.error.text),teal:readable(c.success.text),
  border:theme.isDark?c.border.weak:'#7c8797',focus:c.primary.main,small:Math.max(12,theme.typography.fontSize-2),supporting:14,body:theme.typography.fontSize,
  radius:theme.shape.radius.default,space:theme.spacing};
}
