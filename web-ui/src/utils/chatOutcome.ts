import type {ChatResponse} from '../types';

const BUSINESS_NOOP_PHRASES=[
  'sipariş edilmesi gereken ürün bulunmuyor',
  'sipariş edilecek ürün bulunmuyor',
  'uygun kalem bulunamadı',
];

export type AuthorizationBlock={
  status?:string;
  stage?:string;
  role?:string;
  stepId?:string;
  tool?:string;
};

export function getAuthorizationBlock(response?:ChatResponse):AuthorizationBlock|undefined{
  if(!response)return undefined;
  const authorization=(response.plan as ChatResponse['plan']&{authorization?:AuthorizationBlock}).authorization;
  return authorization?.status==='blocked'?authorization:undefined;
}

export function isAuthorizationBlocked(response?:ChatResponse){
  return Boolean(getAuthorizationBlock(response));
}

export function isBusinessNoOp(response?:ChatResponse){
  if(!response||response.succeeded!==false)return false;
  if(isAuthorizationBlocked(response))return false;
  if(response.pendingDraftId||(response.pendingReceiveIds?.length||0)>0)return false;
  if((response.explanation?.changes?.length||0)>0)return false;
  if(response.trace?.some(step=>step.tool==='place_order'&&step.status==='success'))return false;
  const text=[response.finalAnswer,...(response.trace||[]).flatMap(step=>[step.error,step.resultSummary,step.interpretation])]
    .filter(Boolean)
    .join(' ')
    .toLocaleLowerCase('tr-TR');
  return BUSINESS_NOOP_PHRASES.some(phrase=>text.includes(phrase));
}
