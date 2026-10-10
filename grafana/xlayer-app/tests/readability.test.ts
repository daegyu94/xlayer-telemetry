import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';

const css=readFileSync('src/style.css','utf8');
function tokens(selector:string){const block=css.match(new RegExp(selector+'\\s*\\{([^}]+)'))?.[1]||'';return Object.fromEntries([...block.matchAll(/--xl-([\w-]+):\s*(#[\da-f]+)/gi)].map(m=>[m[1],m[2]]));}
const luminance=(hex:string)=>{if(hex.length===4)hex='#'+[...hex.slice(1)].map(c=>c+c).join('');const channels=hex.slice(1).match(/.{2}/g)!.map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return .2126*channels[0]+.7152*channels[1]+.0722*channels[2];};
const contrast=(a:string,b:string)=>{const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
test('primary, secondary and muted Dashboard text meet normal-text AA on card, canvas and selection backgrounds',()=>{
 const light=tokens('\\.xlt'),dark={...light,...tokens('\\.xlt\\[data-theme=dark\\]')};
 for(const [mode,palette] of Object.entries({light,dark}))for(const foreground of ['muted','text','secondary','primary','warning','danger','teal'])for(const background of ['bg','soft','canvas','selected']){
  assert.ok(palette[foreground],`${foreground} is an explicit text role`);
  assert.ok(contrast(palette[foreground],palette[background])>=4.5,`${mode} ${foreground}/${background} ${contrast(palette[foreground],palette[background]).toFixed(2)}`);
 }
});
test('App typography does not shrink labels, scope, table or narrow-screen text below 12px',()=>{
 const sizes=[...css.matchAll(/font-size:\s*([\d.]+)px/g)].map(m=>Number(m[1]));assert.ok(sizes.length>20);assert.ok(sizes.every(size=>size>=12),`undersized declarations: ${sizes.filter(size=>size<12).join(', ')}`);
 const shorthand=[...css.matchAll(/font:\s*([\d.]+)px/g)].map(m=>Number(m[1]));assert.ok(shorthand.every(size=>size>=12));
});
