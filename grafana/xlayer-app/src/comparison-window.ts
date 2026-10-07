import { dateTime, TimeRange, TimeZone } from '@grafana/data';
import { SceneObjectBase, SceneTimeRangeLike, SceneTimeRangeState } from '@grafana/scenes';

/** Headless native-query range; no picker or URL sync, so Root context stays intact. */
export class ComparisonWindow extends SceneObjectBase<SceneTimeRangeState> implements SceneTimeRangeLike {
  constructor(start: number, end: number, timeZone: TimeZone) {
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || end-start > 3600000) {
      throw new Error('Comparison requires a bounded absolute interval');
    }
    const from=new Date(start).toISOString(),to=new Date(end).toISOString();
    super({from,to,timeZone,value:{from:dateTime(start),to:dateTime(end),raw:{from,to}}});
  }
  getTimeZone():TimeZone{return this.state.timeZone||'browser';}
  onTimeZoneChange(timeZone:TimeZone){this.setState({timeZone});}
  onTimeRangeChange(_range:TimeRange){ /* Read-only artifact bounds. */ }
  onRefresh(){this.setState({value:{...this.state.value}});}
}
