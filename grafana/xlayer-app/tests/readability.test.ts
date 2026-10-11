import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';

test('theme-derived semantic text meets AA on every product surface in both modes',async()=>{
 // Theme assertions need no rendering; Grafana's package index initializes storage/style listeners.
 (globalThis as any).window=Object.assign(new EventTarget(),{history:{pushState(){},replaceState(){}}});
 (globalThis as any).document={getElementsByTagName:()=>[{appendChild(){}}],head:{appendChild(){}},createElement:()=>({setAttribute(){},sheet:{insertRule(){},cssRules:[]}})};
 const {createTheme,colorManipulator}=require('@grafana/data') as typeof import('@grafana/data');
 const {designTokens}=require('../src/design-system/tokens') as typeof import('../src/design-system/tokens');
 for(const mode of ['light','dark'] as const){
  const theme=createTheme({colors:{mode}}),tokens=designTokens(theme);
  for(const foreground of ['muted','text','secondary','primary','warning','danger','teal'] as const){
   for(const background of ['bg','soft','canvas','selected'] as const){
    const ratio=colorManipulator.getContrastRatio(tokens[foreground],tokens[background],theme.colors.background.canvas);
    assert.ok(ratio>=4.5,`${mode} ${foreground}/${background}: ${ratio}`);
   }
  }
  assert.ok(tokens.small>=12);
 }
});
test('scoped panel compatibility keeps native geometry without undersized text or global stylesheet',()=>{
 const compatibility=readFileSync('src/design-system/panel-compat.ts','utf8');
 const sizes=[...compatibility.matchAll(/font-size:\s*([\d.]+)px/g)].map(m=>Number(m[1]));
 assert.ok(sizes.length>0);assert.ok(sizes.every(size=>size>=12));
 assert.ok(!readFileSync('src/module.tsx','utf8').includes('import "./style.css"'));
 const product=readFileSync('src/design-system/styles.ts','utf8')+readFileSync('src/design-system/product-layout.ts','utf8');
 assert.ok(!product.includes('!important'));
});
