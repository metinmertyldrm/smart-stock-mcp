import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {endpoints,keys} from '../api/queries';
import {currency,date} from '../utils/format';
import {Empty,ErrorState,Loading} from '../components/States';
import type {IncomingOrder,MarketOrder} from '../types';

type OrdersTab='purchase'|'incoming';
type DeliveryState={label:string;className:string};

const marketStatusLabel:Record<string,string>={PENDING:'Bekliyor'};
const marketStatusStyle:Record<string,string>={PENDING:'bg-amber-100 text-amber-800'};

export function marketOrderStatus(status:string):DeliveryState{
  return {
    label:marketStatusLabel[status]||status,
    className:marketStatusStyle[status]||'bg-blue-100 text-blue-700',
  };
}

export function purchaseItemSummary(order:MarketOrder):string{
  return order.items.map(item=>`${item.product.name} · ${item.quantity} adet`).join(', ');
}

export function incomingDeliveryState(order:IncomingOrder,now=new Date()):DeliveryState{
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
      <div className="min-w-0">
        <h3 className="font-bold">{order.product.name}</h3>
        <p className="mt-1 text-sm text-slate-500">İkmal #{order.id}</p>
      </div>
      <span className={`badge shrink-0 ${delivery.className}`}>{delivery.label}</span>
    </div>
    <div className="mt-3 flex justify-between text-sm text-slate-500">
      <span>{order.quantity} adet</span>
      <span>{date(order.expectedDeliveryDate)}</span>
    </div>
  </article>;
}

export function MarketOrderCard({order}:{order:MarketOrder}){
  const status=marketOrderStatus(order.status);
  return <article className="card">
    <div className="flex justify-between gap-3">
      <div className="min-w-0">
        <h3 className="font-bold">{purchaseItemSummary(order)}</h3>
        <p className="mt-1 text-sm text-slate-500">Sipariş #{order.id} · Taslak #{order.draftId}</p>
      </div>
      <span className={`badge shrink-0 ${status.className}`}>{status.label}</span>
    </div>
    <div className="mt-3 flex justify-between text-sm">
      <span className="text-slate-500">{date(order.expectedDeliveryDate)}</span>
      <b>{currency(order.totalCost)}</b>
    </div>
  </article>;
}

export function OrdersPage(){
  const [tab,setTab]=useState<OrdersTab>('purchase');
  const incoming=useQuery({queryKey:keys.incoming,queryFn:endpoints.incoming});
  const market=useQuery({queryKey:keys.marketOrders,queryFn:endpoints.marketOrders});
  const marketOrders=market.data||[];
  const incomingOrders=incoming.data||[];
  const marketTotal=marketOrders.reduce((sum,order)=>sum+order.totalCost,0);
  const incomingCounts=incomingOrders.reduce(
    (counts,order)=>{
      const label=incomingDeliveryState(order).label;
      if(label==='Teslim alındı')counts.received+=1;
      else if(label==='Teslim alınabilir')counts.ready+=1;
      else counts.transit+=1;
      return counts;
    },
    {transit:0,ready:0,received:0},
  );

  return <div className="space-y-5">
    <div className="flex flex-wrap gap-2" role="tablist" aria-label="Sipariş türü">
      <TabButton active={tab==='purchase'} onClick={()=>setTab('purchase')}>Satın alma</TabButton>
      <TabButton active={tab==='incoming'} onClick={()=>setTab('incoming')}>Depo ikmali</TabButton>
    </div>

    {tab==='purchase'?(
      <section aria-label="Satın alma siparişleri" className="space-y-4">
        <SummaryRow>
          <span>{market.isLoading?'…':`${marketOrders.length} sipariş`}</span>
          <span>{market.isLoading?'…':`Toplam ${currency(marketTotal)}`}</span>
        </SummaryRow>
        {market.isLoading?<Loading/>:market.isError?<ErrorState message="Satın alma siparişleri alınamadı."/>:
          marketOrders.length===0?<div className="card"><Empty title="Satın alma siparişi yok"/></div>:
          <div className="grid gap-4 lg:grid-cols-2">{marketOrders.map(order=><MarketOrderCard order={order} key={order.id}/>)}</div>}
      </section>
    ):(
      <section aria-label="Depo ikmal siparişleri" className="space-y-4">
        <SummaryRow>
          <span>{incoming.isLoading?'…':`${incomingCounts.transit} yolda`}</span>
          <span>{incoming.isLoading?'…':`${incomingCounts.ready} teslim alınabilir`}</span>
          <span>{incoming.isLoading?'…':`${incomingCounts.received} teslim alındı`}</span>
        </SummaryRow>
        {incoming.isLoading?<Loading/>:incoming.isError?<ErrorState message="Depo ikmal siparişleri alınamadı."/>:
          incomingOrders.length===0?<div className="card"><Empty title="Depo ikmal siparişi yok"/></div>:
          <div className="grid gap-4 lg:grid-cols-2">{incomingOrders.map(order=><IncomingOrderCard order={order} key={order.id}/>)}</div>}
      </section>
    )}
  </div>;
}

function TabButton({active,onClick,children}:{active:boolean;onClick:()=>void;children:React.ReactNode}){
  return <button
    type="button"
    role="tab"
    aria-selected={active}
    className={`rounded-xl px-4 py-2 text-sm font-semibold transition ${active?'bg-blue-600 text-white':'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}`}
    onClick={onClick}
  >{children}</button>;
}

function SummaryRow({children}:{children:React.ReactNode}){
  return <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-500">{children}</div>;
}
