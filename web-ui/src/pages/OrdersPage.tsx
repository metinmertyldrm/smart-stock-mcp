import {useMemo,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {ChevronDown,CircleDollarSign,PackageCheck,Search,ShoppingBag,Truck} from 'lucide-react';
import {endpoints,keys} from '../api/queries';
import {currency,date} from '../utils/format';
import {incomingDeliveryState} from '../utils/orders';
import {Empty,ErrorState,Loading} from '../components/States';
import type {IncomingOrder,MarketOrder} from '../types';

type OrderView='marketplace'|'incoming';

const marketplaceStatus:Record<string,{label:string;className:string}>={
  PENDING:{label:'Hazırlanıyor',className:'bg-amber-100 text-amber-800'},
  SHIPPED:{label:'Kargoda',className:'bg-blue-100 text-blue-700'},
  DELIVERED:{label:'Teslim edildi',className:'bg-emerald-100 text-emerald-700'},
  CANCELLED:{label:'İptal edildi',className:'bg-red-100 text-red-700'},
};

function normalize(value:string){return value.toLocaleLowerCase('tr-TR')}

function productNames(order:MarketOrder){
  return [...new Set(order.items.map(item=>item.product.name))];
}

function sellerNames(order:MarketOrder){
  return [...new Set(order.items.map(item=>item.seller.name))];
}

function orderQuantitySummary(order:MarketOrder){
  const productCount=productNames(order).length;
  const quantity=order.items.reduce((total,item)=>total+item.quantity,0);
  return `${productCount} ürün · ${quantity} adet`;
}

function orderProductPreview(order:MarketOrder){
  const names=productNames(order);
  return names.length<=2?names.join(', '):`${names.slice(0,2).join(', ')} +${names.length-2} ürün`;
}

export function IncomingOrderCard({order,now}:{order:IncomingOrder;now?:Date}){
  const delivery=incomingDeliveryState(order,now);
  return <article className="grid gap-4 border-b border-slate-100 p-4 last:border-b-0 sm:p-5 md:grid-cols-[minmax(0,1fr)_190px_160px] md:items-center">
    <div className="flex min-w-0 items-center gap-3">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-600"><Truck size={19}/></span>
      <div className="min-w-0">
        <b className="block truncate">{order.product.name}</b>
        <p className="mt-0.5 text-sm text-slate-500">İkmal #{order.id} · {order.quantity} adet</p>
      </div>
    </div>
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Tahmini teslim</p>
      <p className="mt-1 text-sm font-medium text-slate-700">{date(order.expectedDeliveryDate)}</p>
    </div>
    <div className="md:text-right"><span className={`badge ${delivery.className}`}>{delivery.label}</span></div>
  </article>;
}

export function MarketplaceOrderRow({order}:{order:MarketOrder}){
  const status=marketplaceStatus[order.status]||{label:order.status,className:'bg-slate-100 text-slate-700'};
  return <article className="border-b border-slate-100 last:border-b-0">
    <div className="grid gap-4 p-4 sm:p-5 md:grid-cols-[minmax(0,1fr)_190px_160px] md:items-center">
      <div className="flex min-w-0 items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><ShoppingBag size={19}/></span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2"><b>Sipariş #{order.id}</b><span className={`badge ${status.className}`}>{status.label}</span></div>
          <p className="mt-1 truncate text-sm text-slate-600">{orderProductPreview(order)}</p>
          <p className="mt-0.5 text-xs text-slate-400">{orderQuantitySummary(order)}</p>
        </div>
      </div>
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Tahmini teslim</p>
        <p className="mt-1 text-sm font-medium text-slate-700">{date(order.expectedDeliveryDate)}</p>
      </div>
      <div className="md:text-right">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Toplam</p>
        <b className="mt-1 block text-base">{currency(order.totalCost)}</b>
      </div>
    </div>
    <details className="group border-t border-slate-100 bg-slate-50/70">
      <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-100 sm:px-5">
        <span>Ürün ve satıcı detayları <span className="font-normal text-slate-400">· Taslak #{order.draftId}</span></span><ChevronDown size={17} className="transition group-open:rotate-180"/>
      </summary>
      <div className="space-y-2 border-t border-slate-100 px-4 py-3 sm:px-5">
        {order.items.map(item=><div className="grid gap-1 rounded-xl bg-white px-3 py-2.5 text-sm sm:grid-cols-[minmax(0,1fr)_auto]" key={item.id}>
          <div><b>{item.product.name}</b><p className="text-xs text-slate-500">{item.seller.name} · {item.quantity} adet × {currency(item.price)}</p></div>
          <b>{currency(item.price*item.quantity+item.shippingFee)}</b>
        </div>)}
      </div>
    </details>
  </article>;
}

export function OrdersPage(){
  const incoming=useQuery({queryKey:keys.incoming,queryFn:endpoints.incoming});
  const market=useQuery({queryKey:keys.marketOrders,queryFn:endpoints.marketOrders});
  const [view,setView]=useState<OrderView>('marketplace');
  const [search,setSearch]=useState('');
  const [statusFilter,setStatusFilter]=useState('all');

  const marketOrders=useMemo(()=>market.data||[],[market.data]);
  const incomingOrders=useMemo(()=>incoming.data||[],[incoming.data]);
  const marketTotal=marketOrders.reduce((total,order)=>total+order.totalCost,0);
  const waitingIncoming=incomingOrders.filter(order=>order.status!=='RECEIVED');
  const waitingQuantity=waitingIncoming.reduce((total,order)=>total+order.quantity,0);
  const readyCount=waitingIncoming.filter(order=>incomingDeliveryState(order).key==='ready').length;
  const marketplaceStatuses=[...new Set(marketOrders.map(order=>order.status))];

  const filteredMarket=useMemo(()=>marketOrders.filter(order=>{
    const matchesStatus=statusFilter==='all'||order.status===statusFilter;
    const term=normalize(search.trim());
    const matchesSearch=!term
      ||String(order.id).includes(term)
      ||productNames(order).some(name=>normalize(name).includes(term))
      ||sellerNames(order).some(name=>normalize(name).includes(term));
    return matchesStatus&&matchesSearch;
  }),[marketOrders,search,statusFilter]);

  const filteredIncoming=useMemo(()=>incomingOrders.filter(order=>{
    const state=incomingDeliveryState(order);
    const matchesStatus=statusFilter==='all'||state.key===statusFilter;
    const term=normalize(search.trim());
    const matchesSearch=!term||String(order.id).includes(term)||normalize(order.product.name).includes(term);
    return matchesStatus&&matchesSearch;
  }),[incomingOrders,search,statusFilter]);

  const changeView=(next:OrderView)=>{setView(next);setSearch('');setStatusFilter('all')};
  const activeLoading=view==='marketplace'?market.isLoading:incoming.isLoading;
  const activeError=view==='marketplace'?market.isError:incoming.isError;

  return <div className="space-y-5">
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 p-5">
        <h2 className="font-bold">Sipariş özeti</h2>
        <p className="mt-1 text-sm text-slate-500">Verilen siparişleri ve depoya gelecek ürünleri tek noktadan takip edin.</p>
      </div>
      <div className="grid grid-cols-2 divide-x divide-y divide-slate-100 lg:grid-cols-4 lg:divide-y-0">
        <SummaryMetric icon={ShoppingBag} label="Satın alma siparişi" value={market.isLoading?'—':marketOrders.length}/>
        <SummaryMetric icon={Truck} label="Beklenen ürün" value={incoming.isLoading?'—':`${waitingQuantity} adet`}/>
        <SummaryMetric icon={PackageCheck} label="Teslim alınabilir" value={incoming.isLoading?'—':readyCount}/>
        <SummaryMetric icon={CircleDollarSign} label="Sipariş toplamı" value={market.isLoading?'—':currency(marketTotal)}/>
      </div>
    </section>

    <div className="grid grid-cols-2 rounded-xl bg-slate-200/70 p-1" role="tablist" aria-label="Sipariş türü">
      <button id="marketplace-orders-tab" type="button" role="tab" aria-controls="orders-panel" aria-selected={view==='marketplace'} className={`min-w-0 rounded-lg px-2 py-2.5 text-sm font-semibold transition sm:px-3 ${view==='marketplace'?'bg-white text-blue-700 shadow-sm':'text-slate-600 hover:text-slate-900'}`} onClick={()=>changeView('marketplace')}><span className="hidden sm:inline">Satın alma </span>Siparişleri <span className="text-xs opacity-70">({marketOrders.length})</span></button>
      <button id="incoming-orders-tab" type="button" role="tab" aria-controls="orders-panel" aria-selected={view==='incoming'} className={`min-w-0 rounded-lg px-2 py-2.5 text-sm font-semibold transition sm:px-3 ${view==='incoming'?'bg-white text-blue-700 shadow-sm':'text-slate-600 hover:text-slate-900'}`} onClick={()=>changeView('incoming')}>Depo ikmalleri <span className="text-xs opacity-70">({incomingOrders.length})</span></button>
    </div>

    <section id="orders-panel" role="tabpanel" aria-labelledby={view==='marketplace'?'marketplace-orders-tab':'incoming-orders-tab'} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-col gap-4 border-b border-slate-100 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
        <div>
          <h2 className="font-bold">{view==='marketplace'?'Satın alma siparişleri':'Depo ikmalleri'}</h2>
          <p className="mt-1 text-sm text-slate-500">{view==='marketplace'?'Sipariş tutarlarını ve ürün dağılımını inceleyin.':'Yoldaki ve teslim alınmayı bekleyen ürünleri takip edin.'}</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <label className="relative min-w-0 sm:w-64"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={17}/><input className="field w-full pl-9" value={search} onChange={event=>setSearch(event.target.value)} placeholder={view==='marketplace'?'Ürün, satıcı veya no ara':'Ürün veya ikmal no ara'} aria-label="Siparişlerde ara"/></label>
          <select className="field" value={statusFilter} onChange={event=>setStatusFilter(event.target.value)} aria-label="Duruma göre filtrele">
            <option value="all">Tüm durumlar</option>
            {view==='marketplace'?marketplaceStatuses.map(status=><option value={status} key={status}>{marketplaceStatus[status]?.label||status}</option>):<>
              <option value="transit">Yolda</option><option value="ready">Teslim alınabilir</option><option value="received">Teslim alındı</option>
            </>}
          </select>
        </div>
      </div>
      {activeLoading?<div className="p-6"><Loading/></div>:activeError?<div className="p-6"><ErrorState message="Sipariş verileri alınamadı."/></div>:view==='marketplace'?
        filteredMarket.length?filteredMarket.map(order=><MarketplaceOrderRow order={order} key={order.id}/>):<div className="p-6"><Empty title="Aramanızla eşleşen satın alma siparişi yok"/></div>
        :filteredIncoming.length?filteredIncoming.map(order=><IncomingOrderCard order={order} key={order.id}/>):<div className="p-6"><Empty title="Aramanızla eşleşen depo ikmali yok"/></div>}
    </section>
  </div>;
}

function SummaryMetric({icon:Icon,label,value}:{icon:typeof ShoppingBag;label:string;value:string|number}){
  return <div className="flex items-center gap-3 p-4 sm:p-5"><span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><Icon size={19}/></span><div className="min-w-0"><p className="truncate text-xs text-slate-500">{label}</p><b className="mt-0.5 block truncate">{value}</b></div></div>;
}
