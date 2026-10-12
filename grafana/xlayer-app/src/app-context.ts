import { createContext } from 'react';
import { dateTimeParse } from '@grafana/data';
import { Context, sceneTime } from './context';
import { timeContextIssue } from './time-context';

export const CatalogRetryContext=createContext({retry:()=>{},refreshing:false,error:''});

export function contextIssue(context:Context){
 return timeContextIssue(context,(value,roundUp)=>dateTimeParse(sceneTime(value),{timeZone:context.timezone,roundUp}).valueOf());
}
