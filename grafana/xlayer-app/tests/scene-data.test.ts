import test from 'node:test';
import assert from 'node:assert/strict';
import type {SceneDataProvider} from '@grafana/scenes';
import {observeSceneData} from '../src/scene-data';

test('synchronous activation and cached terminal frames cannot leave a consumer Loading',()=>{
 let notify:((state:any)=>void)|undefined;
 let unsubscribed=false,deactivated=false;
 const loading:any={state:'Loading',series:[]},done={state:'Done',series:[{length:1}]};
 const provider={state:{data:loading},subscribeToState(callback:any){notify=callback;return {unsubscribe(){unsubscribed=true;notify=undefined;}};},
  activate(){this.state.data=done;notify?.(this.state);return ()=>{deactivated=true;};}};
 const received:any[]=[];
 const stop=observeSceneData(provider as unknown as SceneDataProvider,data=>received.push(data));
 assert.equal(received.at(-1),done);
 const error={state:'Error',series:[]};provider.state.data=error;notify?.(provider.state);
 assert.equal(received.at(-1),error);
 stop();assert.ok(unsubscribed&&deactivated);
 const count=received.length;notify?.(provider.state);assert.equal(received.length,count);
});

test('an inactive cached provider is reread even if activation does not publish',()=>{
 const done={state:'Done',series:[]};let subscriptionRemoved=false;
 const provider={state:{data:done},subscribeToState(){return {unsubscribe(){subscriptionRemoved=true;}};},activate(){return ()=>{};}};
 let result:unknown;
 const stop=observeSceneData(provider as unknown as SceneDataProvider,data=>{result=data;});
 assert.equal(result,done);stop();assert.ok(subscriptionRemoved);
});
