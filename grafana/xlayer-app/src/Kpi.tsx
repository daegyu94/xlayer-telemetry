import { LoadingState, FieldType } from '@grafana/data';
import { Sparkline, useTheme2, Icon, Tooltip } from '@grafana/ui';
import { SceneQueryRunner } from '@grafana/scenes';
import { samples } from './catalog';
import { Context, RecordRow, numeric, scalar } from './context';
import { resolveKpiEntity, APPLICATION_AGE_IDENTITY_KEYS } from './selection';
import { boundaryPresentation, entitySelectionHint, compactEntity } from './presentation';
import { displayUiNote } from './ui-copy';
import { useSceneData as useData } from './scene-data';
import { KpiCard } from './design-system/components';
import { KPI_SPECS } from './scene-specs';
import { format, Delta } from './view-components';

export function Kpi({
  spec,
  provider,
  comparison,
  evidence,
  history,
  ages,
  maxAge,
  reported,
  context,
  boundary,
}: {
  spec: (typeof KPI_SPECS)[number];
  provider?: SceneQueryRunner;
  context:Context;
  boundary:ReturnType<typeof boundaryPresentation>;
  comparison?: RecordRow;
  evidence?: RecordRow;
  history?:{value:number;time:number}[];
  ages?: import("./semantics").Sample[];
  maxAge?: number;
  reported?: number;
}) {
  const theme=useTheme2();
  const data = useData(provider),
    values = samples(data);
  const queryPending=!!provider&&(!data||data.state===LoadingState.Loading||data.state===LoadingState.NotStarted);
  const queryError=data?.state===LoadingState.Error;
  const application=["Reward","Step time","Worker throughput","Reported rollout"].includes(spec.name);
  const selection=resolveKpiEntity(values,{scope:application?'application':'resource',selectedRuns:context.variables.run_id,worker:application?context.variables.worker:undefined,phase:spec.phase,ages:application?ages:undefined,requireFreshness:application,maxAgeSeconds:maxAge??300,ageIdentityKeys:application?APPLICATION_AGE_IDENTITY_KEYS:undefined,evaluationTime:data?.timeRange?.to.valueOf()});
  const latest=selection.sample,age=selection.age,stale=selection.state==='stale';
  const projected = comparison || evidence;
  const trend=(reported!==undefined?[]:projected?[...(history||[])]:values.filter(v=>JSON.stringify(v.labels)===JSON.stringify(latest?.labels))).sort((a,b)=>a.time-b.time);
  let value =
    reported !== undefined
      ? reported
      : projected
        ? projected.current
        : stale
          ? undefined
          : latest?.value;
  const unit =
    reported !== undefined
      ? "s"
      : projected
        ? scalar(projected.unit, "")
        : spec.unit;
  const nativeUnit = [
    "s",
    "ms",
    "percent",
    "percentunit",
    "Bps",
    "bytes",
  ].includes(unit);
  if (spec.scale && !comparison && !evidence && numeric(value) !== undefined)
    value = Number(value) * spec.scale;
  return (
    <KpiCard>
      <span className="xlt-eyebrow xlt-kpi-label">{spec.key==='step_duration_seconds'?boundary.timeLabel:spec.name}<Tooltip content={spec.description}><button className="xlt-help" aria-label={`${spec.name} 설명`}><Icon name="info-circle" size="sm"/></button></Tooltip></span>
      <strong className={!projected&&reported===undefined&&selection.state!=='observed'?"xlt-kpi-state":undefined}>
        {!projected && reported===undefined && data?.state === LoadingState.Error ? (
          "Query error"
        ) : !projected && reported===undefined && queryPending ? (
          "Loading…"
        ) : !projected&&reported===undefined&&selection.state!=='observed'?(
          selection.state==='multiple'?'Multiple entities':selection.state==='freshness-unknown'?'Freshness unknown':selection.state==='stale'?'Stale':selection.state==='invalid'?'Invalid data':'No data'
        ) : nativeUnit ? (
          format(value, unit)
        ) : (
          <>
            {format(value)}
            {unit && <span className="xlt-kpi-unit"> {unit}</span>}
          </>
        )}
      </strong>
      {spec.key&&<Delta row={comparison} />}
      {numeric(comparison?.baseline)!==undefined&&<small>vs baseline {format(comparison?.baseline,scalar(comparison?.unit,''))}</small>}
      {(projected||selection.state==='observed')&&trend.length>1&&<div className="xlt-spark" title={projected?'저장된 Step observation이며 causal model이 아닙니다.':'표시한 entity의 Sampled 추이입니다.'}>
        <Sparkline theme={theme} width={110} height={25} sparkline={{
          x:{name:'Time',type:FieldType.time,values:trend.map(p=>p.time),config:{}},
          y:{name:spec.name,type:FieldType.number,values:trend.map(p=>p.value),config:{color:{mode:'fixed',fixedColor:'#4566d5'}},state:{range:{min:Math.min(...trend.map(p=>p.value)),max:Math.max(...trend.map(p=>p.value)),delta:Math.max(...trend.map(p=>p.value))-Math.min(...trend.map(p=>p.value))}}}
        }}/>
      </div>}
      {ages && !projected && reported===undefined && age!==undefined && (
        <small>
          {stale
            ? "Stale sample"
            : age === undefined
              ? "Freshness unknown"
              : `Sample age ${format(age, "s")}`}
        </small>
      )}
      {!projected&&reported===undefined&&!queryPending&&!queryError&&selection.state!=='observed'&&<small title={displayUiNote(selection.reason)}>{selection.state==='multiple'?`${selection.entities.length} entities · ${displayUiNote(entitySelectionHint(selection.entities.map(s=>s.labels)))}`:selection.state==='freshness-unknown'?'일치하는 producer age를 확인할 수 없습니다':selection.state==='stale'?'Producer age가 허용 범위를 초과했습니다':selection.state==='invalid'?'서로 충돌하는 source data입니다':'일치하는 observation이 없습니다'}</small>}
      {reported !== undefined && (
        <small>Selected {boundary.label} · reported duration</small>
      )}
      {projected && !unit && <small>Unit이 보고되지 않았습니다</small>}
      <small title={spec.description} className="xlt-kpi-scope">
        {comparison || evidence
          ? `${boundary.label} · ${scalar(comparison?.observation_scope || evidence?.observation_scope)}`
          : reported !== undefined
            ? `Reported · ${boundary.label}`
            : spec.name === "KV token hit"
              ? "Rolling · shared engine"
              : spec.name === "GPU utilization"
                ? "Sampled · device"
                : spec.key==='threefs_p99_latency'?'Missing · shared-service':application?'Reported · worker':'Sampled · worker'}
      </small>
      <details className="xlt-kpi-detail"><summary>Source & scope</summary><small>{spec.description}</small>
      {comparison || evidence ? (
        <small className="xlt-entity" title={scalar(comparison?.entity || evidence?.entity,'Entity not reported')}>
          {scalar(
            comparison?.entity || evidence?.entity,
            "Entity not reported",
          )}
        </small>
      ) : (
        latest && (
          <small className="xlt-entity" title={JSON.stringify(latest.labels)}>
            {compactEntity(latest.labels)}
          </small>
        )
      )}
      </details>
    </KpiCard>
  );
}
