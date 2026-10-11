import {GrafanaTheme2,colorManipulator} from '@grafana/data';

/** Semantic roles come from Grafana. Verify text against every XLayer surface. */
export function designTokens(theme:GrafanaTheme2){
 const c=theme.colors;
 const surfaces={bg:c.background.primary,soft:c.background.secondary,canvas:c.background.canvas,selected:c.background.secondary};
 const readable=(color:string)=>Object.values(surfaces).every(bg=>colorManipulator.getContrastRatio(color,bg,c.background.canvas)>=4.5)?color:c.text.primary;
 return {...surfaces,text:readable(c.text.primary),secondary:readable(c.text.secondary),muted:readable(c.text.secondary),
  primary:readable(c.primary.text),warning:readable(c.warning.text),danger:readable(c.error.text),teal:readable(c.success.text),
  border:c.border.weak,focus:c.primary.main,small:Math.max(12,theme.typography.fontSize-2),body:theme.typography.fontSize,
  radius:theme.shape.radius.default,space:theme.spacing};
}
