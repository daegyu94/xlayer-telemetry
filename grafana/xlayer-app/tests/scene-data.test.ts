import {test} from 'node:test';
import assert from 'node:assert/strict';
import {watchSceneData} from '../src/scene-data';

test('owning panel is active during native variable completion and synchronous data is observed',()=>{
 let subscribed=false,active=false,unsubscribed=0,stopped=0;
 const received:any[]=[];let listener:any;
 const provider:any={state:{data:{state:'Loading'}},subscribeToState:(fn:any)=>{subscribed=true;listener=fn;return {unsubscribe:()=>{unsubscribed++;}};},activate:()=>{throw Error('provider must not bypass its owner');}};
 const panel:any={activate:()=>{assert.ok(subscribed);active=true;provider.state.data={state:'Done',series:[]};listener(provider.state);return ()=>{active=false;stopped++;};}};
 const stop=watchSceneData(provider,panel,data=>received.push(data));
 assert.ok(active);assert.equal(received.at(-1).state,'Done');stop();
 assert.equal(active,false);assert.equal(stopped,1);assert.equal(unsubscribed,1);
});

test('remount reads cached terminal data and leaves query errors intact',()=>{
 let subscriptions=0,activations=0;
 const provider:any={state:{data:{state:'Error',errors:[{message:'source failed'}]}},subscribeToState:()=>{subscriptions++;return {unsubscribe:()=>subscriptions--};}};
 const owner:any={activate:()=>{activations++;return ()=>activations--;}};
 for(let i=0;i<2;i++){
  let value:any;const stop=watchSceneData(provider,owner,data=>value=data);
  assert.equal(value.state,'Error');assert.equal(value.errors[0].message,'source failed');stop();
 }
 assert.equal(subscriptions,0);assert.equal(activations,0);
});
