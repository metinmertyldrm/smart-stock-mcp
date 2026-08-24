import {useState} from 'react';
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {Link} from 'react-router-dom';
import {ShieldCheck} from 'lucide-react';
import {endpoints,keys} from '../api/queries';
import {useAuth} from '../auth/AuthProvider';
import {currency,date,errorMessage} from '../utils/format';
import {Empty,ErrorState,Loading,Spinner} from '../components/States';
import {Modal} from '../components/Modal';
import type {Draft} from '../types';
import {canApproveDraft} from './draftPermissions';

export function DraftsPage(){
  const queryClient=useQueryClient();
  const {user}=useAuth();
  const q=useQuery({queryKey:keys.drafts,queryFn:endpoints.drafts});
  const [filter,setFilter]=useState('all');
  const [selected,setSelected]=useState<Draft>();
  const [notice,setNotice]=useState<{kind:'success'|'error';text:string}>();
  const approval=useMutation({
    mutationFn:endpoints.approveDraft,
    onSuccess:response=>{
      setNotice({kind:'success',text:`Taslak #${response.draftId} onaylandı; sipariş #${response.order.id} ve beklenen stok kayıtları oluşturuldu.`});
      setSelected(undefined);
    },
    onError:error=>setNotice({kind:'error',text:errorMessage(error)}),
    onSettled:async()=>{
      await Promise.all([
        queryClient.invalidateQueries({queryKey:keys.drafts}),
        queryClient.invalidateQueries({queryKey:keys.marketOrders}),
        queryClient.invalidateQueries({queryKey:keys.incoming}),
      ]);
    },
  });

  if(q.isLoading)return <Loading/>;
  if(q.isError)return <ErrorState message="Taslaklar alınamadı."/>;
  const data=q.data?.filter(d=>filter==='all'||d.status===filter)||[];
  const approve=(draft:Draft)=>{
    if(approval.isPending)return;
    const accepted=window.confirm(
      `Taslak #${draft.id}, ${currency(draft.totalCost)} tutarla siparişe dönüştürülecek. Onaylıyor musunuz?`,
    );
    if(accepted){setNotice(undefined);approval.mutate(draft.id)}
  };

  return <div className="space-y-5">
    <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
      <b>Merkezi onay akışı:</b> OPERATOR taslağı AI İşlem Merkezi'nde oluşturur; MANAGER veya ADMIN bekleyen taslağı burada onaylar. Sipariş ve beklenen stok kayıtları güvenli host üzerinden MCP araçlarıyla oluşturulur.{' '}
      <Link className="font-semibold underline" to="/ai">Yeni taslak oluştur</Link>
    </div>
    {notice&&<div role="status" className={`rounded-xl border p-4 text-sm ${notice.kind==='success'?'border-emerald-200 bg-emerald-50 text-emerald-800':'border-red-200 bg-red-50 text-red-700'}`}>{notice.text}</div>}
    <div className="flex justify-between gap-4">
      <p className="text-slate-500">Satın alma taslaklarını inceleyin ve rolünüz uygunsa bekleyen taslağı onaylayın.</p>
      <select className="field" value={filter} onChange={e=>setFilter(e.target.value)}>
        <option value="all">Tüm durumlar</option>
        <option value="PENDING">Bekliyor</option>
        <option value="CONFIRMED">Onaylandı</option>
      </select>
    </div>
    {data.length===0?<div className="card"><Empty title="Satın alma taslağı bulunmuyor"/></div>:<div className="grid gap-4 lg:grid-cols-2">
      {data.map(d=><article className="card" key={d.id}>
        <div className="flex justify-between gap-3">
          <div>
            <span className={`badge ${d.status==='PENDING'?'bg-amber-100 text-amber-800':'bg-emerald-100 text-emerald-700'}`}>{d.status==='PENDING'?'Bekliyor':'Onaylandı'}</span>
            <h2 className="mt-3 text-lg font-bold">Taslak #{d.id}</h2>
            <p className="text-sm text-slate-500">{date(d.createdAt)} · {d.items.length} kalem</p>
          </div>
          <strong className="text-lg">{currency(d.totalCost)}</strong>
        </div>
        <div className="mt-4"><button className="btn-light w-full" onClick={()=>setSelected(d)}>Detayları gör</button></div>
      </article>)}
    </div>}
    {selected&&<DraftDetail
      draft={selected}
      close={()=>setSelected(undefined)}
      approve={()=>approve(selected)}
      allowed={canApproveDraft(user?.role,selected.status)}
      approving={approval.isPending}
    />}
  </div>;
}

function DraftDetail({draft,close,approve,allowed,approving}:{draft:Draft;close:()=>void;approve:()=>void;allowed:boolean;approving:boolean}){
  const audit=useQuery({queryKey:[...keys.drafts,draft.id,'audit'],queryFn:()=>endpoints.draftAudit(draft.id)});
  return <Modal title={`Taslak #${draft.id}`} onClose={close}>
    <div className="space-y-3">{draft.items.map(i=><div className="rounded-xl border p-4" key={i.id}>
      <div className="flex justify-between gap-3"><b>{i.product.name}</b><b>{currency(i.price*i.quantity+i.shippingFee)}</b></div>
      <p className="text-sm text-slate-500">{i.seller.name} · {i.quantity} adet × {currency(i.price)}</p>
      <p className="text-xs text-slate-500">Kargo: {currency(i.shippingFee)} · Tahmini {i.deliveryTimeDays} gün</p>
    </div>)}</div>
    <div className="mt-5 flex justify-between border-t pt-4 text-lg font-bold"><span>Toplam</span><span>{currency(draft.totalCost)}</span></div>
    {audit.data&&<div className="mt-4 rounded-xl bg-slate-50 p-3 text-xs text-slate-600">
      <b>İşlem denetimi</b>
      <p className="mt-1">Oluşturan: {audit.data.createdBy?`${audit.data.createdBy.username} (${audit.data.createdBy.role})`:'Bu eski taslak için kayıt bulunmuyor.'}</p>
      <p>Onaylayan: {audit.data.approvedBy?`${audit.data.approvedBy.username} (${audit.data.approvedBy.role})`:'Henüz onaylanmadı.'}</p>
    </div>}
    {draft.status==='PENDING'&&<div className="mt-5 border-t pt-5">
      {allowed?<>
        <p className="mb-3 text-sm text-slate-500">Bu işlem marketplace siparişini ve depoya beklenen ikmal kayıtlarını oluşturur.</p>
        <button className="btn-primary w-full justify-center" disabled={approving} onClick={approve}>{approving?<Spinner/>:<ShieldCheck size={18}/>}Taslağı onayla ve siparişi ver</button>
      </>:<p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">Bu taslağı onaylamak için MANAGER veya ADMIN rolüyle giriş yapın.</p>}
    </div>}
  </Modal>;
}
