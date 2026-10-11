import {test} from 'node:test';
import assert from 'node:assert/strict';
import {formatContextTime,formatContextRange,timeContextIssue,validAbsoluteWindow,validTimezone} from '../src/time-context';
import {readContext,sceneTime} from '../src/context';
test('out-of-range numeric URL time is rejected before Date conversion',()=>{
 for(const value of ['999999999999999999999','8640000000000001','-8640000000000001'])assert.throws(()=>sceneTime(value),error=>error instanceof Error&&!(error instanceof RangeError)&&error.message.includes('Time range'));
 assert.equal(sceneTime('-1000'),'1969-12-31T23:59:59.000Z');
 assert.equal(sceneTime('now-30m'),'now-30m');
 assert.equal(validAbsoluteWindow(8640000000000001,8640000000000002),false);
 assert.equal(validAbsoluteWindow(0,3600001,3600000),false);
 assert.equal(validAbsoluteWindow(0,3600001),true);
});
test('native parser results, errors, reversed bounds and timezone control validity',()=>{
 const context=readContext('?from=1000&to=2000&timezone=UTC');
 assert.equal(timeContextIssue(context,value=>Number(value)),undefined);
 for(const bounds of [{from:'2000',to:'1000'},{from:'1000',to:'1000'},{from:'bad',to:'2000'},{from:'8640000000000001',to:'8640000000000002'}])assert.ok(timeContextIssue({...context,...bounds},value=>Number(value)));
 assert.ok(timeContextIssue(context,()=>{throw new Error('native parser failure');}));
 assert.ok(timeContextIssue({...context,timezone:'not/a/timezone'},value=>Number(value)));
 assert.equal(validTimezone('browser'),true);assert.equal(validTimezone('utc'),true);
 const calls:any[]=[];assert.equal(timeContextIssue({...context,from:'now-30m',to:'now'},(value,roundUp)=>{calls.push([value,roundUp]);return roundUp?2000:1000;}),undefined);
 assert.deepEqual(calls,[['now-30m',false],['now',true]]);
});
test('selected timezone formats the same instant independent of browser locale, including DST and epoch zero',()=>{
 const time=Date.parse('2026-10-11T03:00:00Z');
 assert.match(formatContextTime(time,'UTC','time'),/03:00:00 UTC/);
 assert.match(formatContextTime(time,'Asia/Seoul','time'),/12:00:00 GMT\+9/);
 assert.match(formatContextTime(Date.parse('2026-03-08T06:59:00Z'),'America/New_York','time'),/01:59:00/);
 assert.match(formatContextTime(Date.parse('2026-03-08T07:01:00Z'),'America/New_York','time'),/03:01:00/);
 assert.match(formatContextTime(0,'UTC'),/1970/);
 assert.equal(formatContextTime(1e24,'UTC'),'Time unknown');assert.equal(formatContextTime(time,'bad'),'Time unknown');
});
test('visible range retains both dates across midnight in the selected timezone and labels window duration',()=>{
 const start=Date.parse('2026-10-11T23:59:40Z'),end=Date.parse('2026-10-12T00:00:20Z');
 const utc=formatContextRange(start,end,'UTC');assert.match(utc,/11 Oct/);assert.match(utc,/12 Oct/);assert.match(utc,/window 40 s/);
 const seoul=formatContextRange(start,end,'Asia/Seoul');assert.match(seoul,/08:59:40/);assert.match(seoul,/09:00:20/);
 assert.equal(formatContextRange(1e24,end,'UTC'),'Time range unknown');
});
