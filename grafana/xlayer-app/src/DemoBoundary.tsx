import React,{useEffect,useState} from 'react';
import {getBackendSrv} from '@grafana/runtime';
import {Alert} from '@grafana/ui';
import {appLink,readContext} from './context';
import {demoLandingContext} from './demo-scenarios';

/** A replay URL cannot silently become a window into another backend store. */
export function DemoBoundary({generation,children}:React.PropsWithChildren<{generation?:string}>){
 const[state,setState]=useState<{generation?:string;status:string}>({status:'checking'});
 useEffect(()=>{
  if(!generation)return;
  let active=true,timer:ReturnType<typeof setTimeout>;
  const verify=()=>getBackendSrv().get('/api/plugin-proxy/xlayer-telemetry-app/demo-control/status',undefined,undefined,{showErrorAlert:false})
   .then(value=>{if(active){setState({generation,status:String(value.generation)===generation&&['collecting','ready'].includes(value.status)?'current':'expired'});timer=setTimeout(verify,2500);}})
   .catch(()=>{if(active){setState({generation,status:'unavailable'});timer=setTimeout(verify,2500);}});
  verify();return()=>{active=false;clearTimeout(timer)};
 },[generation]);
 if(!generation)return <>{children}</>;
 if(state.generation!==generation||state.status==='checking')return <p role="status">Synthetic replay를 확인하고 있습니다…</p>;
 if(state.status!=='current')return <Alert title={state.status==='expired'?'Replay expired':'Replay verification unavailable'} severity="info"><p>{state.status==='expired'?'이 URL의 synthetic 실행은 현재 활성 replay가 아닙니다. 다른 실행의 telemetry로 대체하지 않습니다.':'Live Demo 연결을 확인할 수 없어 telemetry 표시를 보류했습니다. 자동으로 다시 확인하며, 연결이 복구되면 같은 실행을 다시 엽니다.'}</p><a href={appLink('demo',demoLandingContext(readContext(window.location.search)))}>Open Scenario Explorer →</a></Alert>;
 return <>{children}</>;
}
