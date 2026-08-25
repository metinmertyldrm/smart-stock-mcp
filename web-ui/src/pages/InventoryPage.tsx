import {useMemo,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Search} from 'lucide-react';
import {endpoints,keys} from '../api/queries';
import {Empty,ErrorState,Loading} from '../components/States';
import {StockBadge} from '../components/StockBadge';
import {Modal} from '../components/Modal';
import {stockState} from '../utils/format';
import type {IncomingOrder,Product} from '../types';

export function pendingIncomingQuantity(productId:number,orders:IncomingOrder[]=[]){
  return orders
    .filter(order=>order.status==='PENDING'&&order.product.id===productId)
    .reduce((total,order)=>total+order.quantity,0);
}

export function recommendedReplenishment(product:Product,orders:IncomingOrder[]=[]){
  return Math.max(
    0,
    product.targetStock-product.stockQuantity-pendingIncomingQuantity(product.id,orders),
  );
}

export function InventoryPage(){
  const productsQuery=useQuery({queryKey:keys.products,queryFn:endpoints.products});
  const incomingQuery=useQuery({queryKey:keys.incoming,queryFn:endpoints.incoming});
  const [search,setSearch]=useState('');
  const [status,setStatus]=useState('all');
  const [category,setCategory]=useState('all');
  const [brand,setBrand]=useState('all');
  const [selected,setSelected]=useState<Product>();
  const products=useMemo(()=>productsQuery.data
    ?.filter(product=>(product.name+' '+product.sku).toLocaleLowerCase('tr').includes(search.toLocaleLowerCase('tr'))
      &&(status==='all'||stockState(product)===status)
      &&(category==='all'||product.subcategory?.category?.name===category)
      &&(brand==='all'||product.model?.brand?.name===brand))
    .sort((a,b)=>a.name.localeCompare(b.name,'tr'))||[],
  [productsQuery.data,search,status,category,brand]);
  const unique=(value:(product:Product)=>string|undefined)=>[
    ...new Set(productsQuery.data?.map(value).filter(Boolean) as string[]),
  ];
  const retry=()=>{
    void productsQuery.refetch();
    void incomingQuery.refetch();
  };

  if(productsQuery.isLoading||incomingQuery.isLoading)return <Loading rows={7}/>;
  if(productsQuery.isError||incomingQuery.isError){
    return <ErrorState message="Ürün ve bekleyen ikmal bilgileri alınamadı." onRetry={retry}/>;
  }

  const orders=incomingQuery.data||[];
  const selectedPending=selected?pendingIncomingQuantity(selected.id,orders):0;
  const selectedRecommendation=selected?recommendedReplenishment(selected,orders):0;

  return <div className="space-y-5">
    <div className="card flex flex-wrap gap-3">
      <label className="relative min-w-64 flex-1">
        <Search className="absolute left-3 top-2.5 text-slate-400" size={18}/>
        <input aria-label="Ürün ara" className="field w-full pl-10" placeholder="Ürün adı veya SKU ara" value={search} onChange={event=>setSearch(event.target.value)}/>
      </label>
      <select aria-label="Stok durumu" className="field" value={status} onChange={event=>setStatus(event.target.value)}>
        <option value="all">Tüm durumlar</option>
        <option value="out">Stokta yok</option>
        <option value="critical">Kritik</option>
        <option value="low">Düşük</option>
        <option value="healthy">Sağlıklı</option>
      </select>
      <select aria-label="Kategori" className="field" value={category} onChange={event=>setCategory(event.target.value)}>
        <option value="all">Tüm kategoriler</option>
        {unique(product=>product.subcategory?.category?.name).map(value=><option key={value}>{value}</option>)}
      </select>
      <select aria-label="Marka" className="field" value={brand} onChange={event=>setBrand(event.target.value)}>
        <option value="all">Tüm markalar</option>
        {unique(product=>product.model?.brand?.name).map(value=><option key={value}>{value}</option>)}
      </select>
    </div>
    <div className="card overflow-x-auto !p-0">
      {products.length===0?<Empty title="Filtrelerle eşleşen ürün yok"/>:
        <table className="w-full min-w-[1120px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>{['SKU','Ürün adı','Kategori','Marka / model','Mevcut','Yoldaki','Kritik','Hedef','Durum','Önerilen ikmal'].map(value=><th className="px-4 py-3" key={value}>{value}</th>)}</tr>
          </thead>
          <tbody>{products.map(product=>{
            const pending=pendingIncomingQuantity(product.id,orders);
            return <tr className="cursor-pointer border-t hover:bg-blue-50/40" key={product.id} onClick={()=>setSelected(product)}>
              <td className="px-4 py-4 font-mono text-xs">{product.sku}</td>
              <td className="px-4 font-semibold">{product.name}</td>
              <td className="px-4">{product.subcategory?.category?.name||'—'}</td>
              <td className="px-4">{product.model?.brand?.name||'—'} / {product.model?.name||'—'}</td>
              <td className="px-4 font-bold">{product.stockQuantity}</td>
              <td className="px-4 font-semibold text-indigo-600">{pending}</td>
              <td className="px-4">{product.minimumStock}</td>
              <td className="px-4">{product.targetStock}</td>
              <td className="px-4"><StockBadge product={product}/></td>
              <td className="px-4 font-bold text-blue-600">{recommendedReplenishment(product,orders)}</td>
            </tr>;
          })}</tbody>
        </table>}
    </div>
    {selected&&<Modal title={selected.name} onClose={()=>setSelected(undefined)}>
      <p className="text-sm text-slate-500">{selected.sku} · {selected.description}</p>
      <div className="my-5 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div className="rounded-xl bg-slate-50 p-3"><span className="block text-slate-500">Mevcut</span><b>{selected.stockQuantity}</b></div>
        <div className="rounded-xl bg-indigo-50 p-3"><span className="block text-slate-500">Yoldaki</span><b>{selectedPending}</b></div>
        <div className="rounded-xl bg-slate-50 p-3"><span className="block text-slate-500">Hedef</span><b>{selected.targetStock}</b></div>
        <div className="rounded-xl bg-blue-50 p-3"><span className="block text-slate-500">Önerilen ikmal</span><b>{selectedRecommendation}</b></div>
      </div>
      <div className="flex justify-between"><StockBadge product={selected}/><b>{selected.stockQuantity} mevcut + {selectedPending} yolda</b></div>
      <div className="mt-4 h-3 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full bg-blue-600" style={{width:`${Math.min(100,(selected.stockQuantity+selectedPending)/Math.max(1,selected.targetStock)*100)}%`}}/>
      </div>
      <p className="mt-4">Depo konumu: <b>{selected.warehouseInfo||'Belirtilmemiş'}</b></p>
    </Modal>}
  </div>;
}
