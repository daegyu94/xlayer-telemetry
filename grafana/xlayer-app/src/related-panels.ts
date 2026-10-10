/** Keep labels coupled to optional panels while parenting Scenes arrays. */
export function relatedPanels<T>(pairs:{label:string;panel:T|undefined}[]){
 const available=pairs.filter((row):row is {label:string;panel:T}=>row.panel!==undefined);
 return {related:available.map(row=>row.panel),relatedLabels:available.map(row=>row.label)};
}
