import type {IncomingOrder} from '../types';

export type DeliveryKey='transit'|'ready'|'received';
export type DeliveryState={key:DeliveryKey;label:string;className:string};

export function incomingDeliveryState(order:IncomingOrder,now=new Date()):DeliveryState{
  if(order.status==='RECEIVED'){
    return {key:'received',label:'Teslim alındı',className:'bg-emerald-100 text-emerald-700'};
  }
  const expected=order.expectedDeliveryDate?new Date(order.expectedDeliveryDate):null;
  if(order.status==='PENDING'&&(!expected||expected.getTime()<=now.getTime())){
    return {key:'ready',label:'Teslim alınabilir',className:'bg-amber-100 text-amber-800'};
  }
  return {key:'transit',label:'Yolda',className:'bg-blue-100 text-blue-700'};
}
