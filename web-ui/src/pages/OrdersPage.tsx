import {useQuery} from '@tanstack/react-query';
import {endpoints,keys} from '../api/queries';
import {currency,date} from '../utils/format';
import {Empty,ErrorState,Loading} from '../components/States';
import type {IncomingOrder} from '../types';

type DeliveryState={label:string;className:string};

function incomingDeliveryState(order:IncomingOrder,now=new Date()):DeliveryState{
  if(order.status==='RECEIVED')return {label:'Teslim alındı',className:'bg-emerald-100 text-emerald-700'};
  const expected=order.expectedDeliveryDate?new Date(order.expectedDeliveryDate):null;
  if(order.status==='PENDING'&&(!expected||expected.getTime()<=now.getTime())){
    return {label:'Teslim alınabilir',className:'bg-amber-100 text-amber-800'};
  }
  return {label:'Yolda',className:'bg-blue-100 text-blue-700'};
}

export function IncomingOrderCard({order,now}:{order:IncomingOrder;now?:Date}){
  const delivery=incomingDeliveryState(order,now);
  return <article className="card">
    <div className="flex justify-between gap-3">
      <b>İkmal #{order.id} · {order.product.name}</b>
      <span className={`badge shrink-0 ${delivery.className}`}>{delivery.label}</span>
    </div>
    <div className="mt-3 flex justify-between text-sm text-slate-500">
      <span>{order.quantity} adet</span><span>{date(order.expectedDeliveryDate)}</span>
    </div>
  </article>;
}

export function OrdersPage(){
  const incoming=useQuery({queryKey:keys.incoming,queryFn:endpoints.incoming});
  const market=useQuery({queryKey:keys.marketOrders,queryFn:endpoints.marketOrders});
  return <div className="space-y-8">
    <OrderSection title="Marketplace satın alma siparişleri" loading={market.isLoading} error={market.isError}>
      {market.data?.length?market.data.map(o=><article className="card" key={o.id}>
        <div className="flex justify-between"><b>Sipariş #{o.id}</b><span className="badge bg-blue-100 text-blue-700">{o.status}</span></div>
        <p className="mt-2 text-sm text-slate-500">{o.items.map(i=>`${i.product.name} (${i.quantity})`).join(', ')}</p>
        <div className="mt-3 flex justify-between text-sm"><span>{date(o.expectedDeliveryDate)}</span><b>{currency(o.totalCost)}</b></div>
      </article>):<Empty title="Marketplace siparişi yok"/>}
    </OrderSection>
    <OrderSection title="Depoya gelen ikmal siparişleri" loading={incoming.isLoading} error={incoming.isError}>
      {incoming.data?.length?incoming.data.map(o=><IncomingOrderCard order={o} key={o.id}/>):<Empty title="Gelen ikmal siparişi yok"/>}
    </OrderSection>
  </div>;
}

function OrderSection({title,loading,error,children}:{title:string;loading:boolean;error:boolean;children:React.ReactNode}){
  return <section><h2 className="mb-4 text-lg font-bold">{title}</h2>{loading?<Loading/>:error?<ErrorState message="Sipariş verileri alınamadı."/>:<div className="grid gap-4 lg:grid-cols-2">{children}</div>}</section>;
}
