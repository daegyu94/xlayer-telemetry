// Options reflect datasource retention, not the validity of saved Run identity.
export function preserveIdentity(name:string,value:any,text:any,update:any){
 const values=Array.isArray(value)?value:[value];
 if(['cluster','run_id','source_node'].includes(name)&&values.length&&values.every(v=>typeof v==='string'&&v&&v!=='$__all')){
  update.value=value;update.text=text;
 }
}

export function normalizeSelection(definition:any,selected?:string[]) {
 if(definition?.type==='textbox'&&(definition.query==='.*'||definition.current?.value==='.*')&&selected?.includes('$__all'))return ['.*'];
 return definition?.includeAll&&selected?.includes('.*')?['$__all']:selected;
}
