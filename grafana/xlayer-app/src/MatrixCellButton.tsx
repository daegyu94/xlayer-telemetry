import React from 'react';
import type {PhaseComparison} from './semantics';

export function matrixDeltaLabel(comparison: PhaseComparison): string | undefined {
  if (!comparison.comparable) return undefined;
  if (comparison.delta === undefined) return 'Δ unavailable · baseline 0';
  if (comparison.delta === 0) return 'No change vs baseline';
  return `${comparison.delta > 0 ? '↑ +' : '↓ '}${Math.abs(comparison.delta).toFixed(1)}% vs baseline`;
}

export function MatrixCellButton({identity,label,quality,comparison,explanation,comparisonReason,
  observations,linkedTool,stepEvidence,loading,onClick}:{identity:string;label:string;quality:string;
  comparison:PhaseComparison;explanation:string;comparisonReason?:string;observations?:number;
  linkedTool?:boolean;stepEvidence?:boolean;loading?:boolean;onClick:()=>void}) {
  // Visible and accessible results share already-formatted values; never
  // re-read a raw metric or calculate another delta for assistive technology.
  const delta = matrixDeltaLabel(comparison);
  const count = observations === undefined ? undefined : `${observations} query observations · mean`;
  const linked = linkedTool ? 'Linked tool call' : undefined;
  const evidence = stepEvidence ? 'Step evidence →' : undefined;
  const name = [identity,label,delta,quality,count,linked,evidence].filter(Boolean).join(' · ');
  return <button className="xlt-cell" data-matrix-cell={identity} disabled={loading} aria-busy={loading}
    aria-label={name} aria-description={[explanation,comparisonReason].filter(Boolean).join(' ')}
    title={explanation} onClick={onClick}>
    <b>{label}</b>
    {delta && <span className={`xlt-matrix-delta ${comparison.delta===0?'xlt-delta-flat':comparison.delta!==undefined&&comparison.delta>0?'xlt-delta-up':'xlt-delta-down'}`} title={comparisonReason}>{delta}</span>}
    <small>{quality}</small>
    {count && <small>{count}</small>}
    {linked && <small>{linked}</small>}
    {evidence && <span className="xlt-step-evidence">{evidence}</span>}
  </button>;
}
