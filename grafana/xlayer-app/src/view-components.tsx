import { ResourcePanel } from './ResourcePanel';
import React from 'react';
import { LoadingState, getValueFormat } from '@grafana/data';
import { SceneQueryRunner, VizPanel } from '@grafana/scenes';
import { Catalog } from './catalog';
import { Context, RecordRow, dashboardLink, Destination, numeric } from './context';
import { displayUiNote } from './ui-copy';
import { useSceneData as useData } from './scene-data';

export function Native({ panel }: { panel?: VizPanel }) {
  return panel ? (
    <div className="xlt-panel">
      <ResourcePanel panel={panel} />
    </div>
  ) : (
    <div className="xlt-empty">
      Loki Timeline을 사용할 수 없습니다. 완료된 duration은 Stage Correlation에서 확인하세요.
    </div>
  );
}

export function format(value: unknown, unit = "") {
  const n = numeric(value);
  if (
    n !== undefined &&
    ["s", "ms", "percent", "percentunit", "Bps", "bytes", "short"].includes(
      unit,
    )
  ) {
    const rendered = getValueFormat(unit)(n, 2);
    return `${rendered.prefix || ""}${rendered.text}${rendered.suffix || ""}`;
  }
  return n === undefined
    ? "—"
    : `${new Intl.NumberFormat("en", { maximumFractionDigits: 2, notation: Math.abs(n) >= 10000 ? "compact" : "standard" }).format(n)}${unit ? " " + unit : ""}`;
}

export function Delta({ row }: { row?: RecordRow }) {
  const delta = numeric(row?.delta_percent);
  return (
    <span
      title={
        delta === undefined
          ? "저장된 비교 구간에서 유효한 relative delta를 계산할 수 없습니다."
          : `${delta}% vs saved baseline`
      }
      className={
        delta === undefined
          ? "xlt-muted xlt-change"
          : Math.abs(delta) >= 20
            ? "xlt-attention xlt-change"
            : "xlt-change"
      }
    >
      {delta === undefined
        ? numeric(row?.baseline) === 0
          ? "Δ unavailable · baseline 0"
          : "No baseline"
        : `${delta >= 0 ? "+" : ""}${format(delta, "%")} vs baseline`}
    </span>
  );
}

export function DataStatus({ provider }: { provider?: SceneQueryRunner }) {
  const data = useData(provider);
  if (!provider)
    return (
      <p className="xlt-empty">
        Loki / diagnosis projection이 설정되지 않았습니다. Metrics와 Deep Dive는 계속 사용할 수 있습니다.
      </p>
    );
  if (data?.state === LoadingState.Error)
    return (
      <p role="alert" className="xlt-error">
        Query failure: {" "}
        {displayUiNote(data.error?.message || data.errors?.map((e) => e.message).join(";") || "상세 오류가 보고되지 않았습니다")}.
        데이터가 없다는 것은 측정값 0과 다릅니다.
      </p>
    );
  if (!data || data.state === LoadingState.Loading)
    return <p className="xlt-muted">불러오는 중입니다…</p>;
  return null;
}

export function Link({
  to,
  context,
  children,
  catalog,
  panel,
}: {
  to: Destination;
  context: Context;
  children: React.ReactNode;
  catalog: Catalog;
  panel?:number;
}) {
  return catalog[to] ? (
    <a className="xlt-link" href={dashboardLink(to, context)+(panel!==undefined?`&viewPanel=${panel}`:'')}>
      {children} ↗
    </a>
  ) : (
    <span className="xlt-muted">{children} · unavailable</span>
  );
}
