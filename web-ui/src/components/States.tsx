import {AlertTriangle,Inbox,LoaderCircle} from 'lucide-react';
export function Loading({rows=3}:{rows?:number}){return <div aria-label="Yükleniyor" className="space-y-3">{Array.from({length:rows}).map((_,i)=><div key={i} className="h-16 animate-pulse rounded-xl bg-slate-100"/>)}</div>}
export function ErrorState({message,onRetry}:{message:string;onRetry?:()=>void}){return <div role="alert" className="card text-center"><AlertTriangle className="mx-auto text-red-500"/><p className="mt-2 font-semibold">Veriler alınamadı</p><p className="text-sm text-slate-500">{message}</p>{onRetry&&<button className="btn-light mt-3" onClick={onRetry}>Tekrar dene</button>}</div>}
export function Empty({title='Henüz veri yok'}:{title?:string}){return <div className="py-10 text-center text-slate-500"><Inbox className="mx-auto mb-2"/><p>{title}</p></div>}
export function Spinner(){return <LoaderCircle className="animate-spin" size={18}/>}
