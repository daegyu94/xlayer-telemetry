import {useEffect,useState} from 'react';
import type {PanelData} from '@grafana/data';
import type {SceneDataProvider} from '@grafana/scenes';

/** Activation can publish synchronously, including a retained terminal frame. */
export function observeSceneData(provider:SceneDataProvider,receive:(data:PanelData|undefined)=>void){
 const subscription=provider.subscribeToState(state=>receive(state.data));
 let deactivate:(()=>void)|undefined;
 try{deactivate=provider.activate();receive(provider.state.data);}
 catch(error){subscription.unsubscribe();deactivate?.();throw error;}
 return ()=>{subscription.unsubscribe();deactivate?.();};
}

export function useSceneData(provider:SceneDataProvider|undefined){
 const [snapshot,setSnapshot]=useState({provider,data:provider?.state.data});
 useEffect(()=>{
  setSnapshot({provider,data:provider?.state.data});
  return provider?observeSceneData(provider,data=>setSnapshot({provider,data})):undefined;
 },[provider]);
 return snapshot.provider===provider?snapshot.data:provider?.state.data;
}
