import {useEffect,useState} from 'react';
import type {PanelData} from '@grafana/data';
import type {SceneDataProvider,SceneObject} from '@grafana/scenes';

/** Subscribe before activation: static/transform providers can publish synchronously. */
export function watchSceneData(provider:SceneDataProvider,owner:SceneObject,receive:(data:PanelData|undefined)=>void){
 const subscription=provider.subscribeToState(state=>receive(state.data));
 const deactivate=owner.activate();
 receive(provider.state.data);
 return ()=>{subscription.unsubscribe();deactivate();};
}

/** Keep a panel active with its provider so native variable updates reach both. */
export function useSceneData(provider:SceneDataProvider|undefined,owner:SceneObject|undefined=provider){
 const [snapshot,setSnapshot]=useState({provider,data:provider?.state.data});
 useEffect(()=>{
  if(!provider||!owner){setSnapshot({provider,data:provider?.state.data});return;}
  return watchSceneData(provider,owner,data=>setSnapshot({provider,data}));
 },[provider,owner]);
 return snapshot.provider===provider?snapshot.data:provider?.state.data;
}
