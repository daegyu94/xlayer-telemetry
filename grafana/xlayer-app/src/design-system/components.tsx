import React from 'react';
import {Button,useStyles2} from '@grafana/ui';
import {css,cx} from '@emotion/css';
import {GrafanaTheme2} from '@grafana/data';
import {designTokens} from './tokens';

export function KpiCard({className,children}:React.PropsWithChildren<{className?:string}>){
 const styles=useStyles2(componentStyles);
 return <article className={cx('xlt-card xlt-kpi',styles.kpi,className)}>{children}</article>;
}
export function PrimaryAction({children,onClick}:React.PropsWithChildren<{onClick:()=>void}>){
 return <Button className="xlt-primary-action" variant="primary" onClick={onClick}>{children}</Button>;
}
export function StatusLabel({tone='neutral',children}:React.PropsWithChildren<{tone?:'neutral'|'attention'|'error'}>){
 const styles=useStyles2(componentStyles);
 return <span className={cx('xlt-badge',styles.status,tone==='attention'&&styles.attention,tone==='error'&&styles.error)}>{children}</span>;
}
const componentStyles=(theme:GrafanaTheme2)=>{
 const t=designTokens(theme);
 return {kpi:css({display:'flex',flexDirection:'column',gap:t.space(.5),padding:t.space(2),minWidth:0,background:t.bg,
  border:`1px solid ${t.border}`,borderRadius:t.radius,boxShadow:'none',
  '& strong':{fontSize:theme.typography.h2.fontSize,fontWeight:theme.typography.fontWeightBold,lineHeight:1.25,letterSpacing:'-.025em'},
  '& strong.xlt-kpi-state':{fontSize:theme.typography.h5.fontSize,letterSpacing:0},
  '& small':{fontSize:t.supporting,lineHeight:1.5,color:t.muted}}),
 status:css({display:'inline-flex',alignItems:'center',gap:t.space(.5),padding:t.space(.25,.75),borderRadius:t.radius,fontSize:t.small,color:t.muted,background:t.soft,border:`1px solid ${t.border}`}),
 attention:css({color:t.warning,borderColor:theme.colors.warning.border,background:theme.colors.warning.transparent}),
 error:css({color:t.danger,borderColor:theme.colors.error.border,background:theme.colors.error.transparent})};
};
