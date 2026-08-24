import {useEffect,useRef,useState} from 'react';
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {Bot,History,Lightbulb,Menu,PanelRight,Plus,Send,Trash2,User,X} from 'lucide-react';
import {endpoints,keys} from '../api/queries';
import {errorMessage} from '../utils/format';
import {displayConversationTitle} from '../utils/conversationTitle';
import {getAuthorizationBlock,isBusinessNoOp} from '../utils/chatOutcome';
import {Spinner} from '../components/States';
import type {ChatMessage,ConversationSummary} from '../types';
import {TracePanel} from '../components/DecisionJournal';
export {TracePanel} from '../components/DecisionJournal';

const prompts=['Stokta olmayan ürünleri bul ve en ekonomik satın alma planını hazırla.','Toplam bütçe 50.000 TL\'yi geçmeyecek şekilde eksik ürünleri tamamla.','Stokta azalan ürünler için en ucuz tekliflerden taslak sipariş oluştur.','Bekleyen siparişleri kontrol et ve teslim edilen ürünleri stoğa ekle.','En ucuz ve en hızlı planı karşılaştır.'];
const conversationKey=['ai-conversations'];
function groupLabel(date:string){const day=new Date(date);const today=new Date();const diff=Math.floor((new Date(today.getFullYear(),today.getMonth(),today.getDate()).getTime()-new Date(day.getFullYear(),day.getMonth(),day.getDate()).getTime())/86400000);return diff===0?'Bugün':diff===1?'Dün':diff<7?'Son 7 gün':'Daha eski'}

export function AiPage(){
  const queryClient=useQueryClient();
  const initial=new URLSearchParams(location.search).get('conversation');
  const [selected,setSelected]=useState<string|null>(initial);
  const [message,setMessage]=useState('');
  const [historyOpen,setHistoryOpen]=useState(false);
  const [traceOpen,setTraceOpen]=useState(false);
  const input=useRef<HTMLTextAreaElement>(null);
  const messageList=useRef<HTMLDivElement>(null);
  const submitting=useRef(false);
  const shouldFollow=useRef(true);
  const conversations=useQuery({queryKey:conversationKey,queryFn:endpoints.conversations});
  const detail=useQuery({queryKey:['ai-conversation',selected],queryFn:()=>endpoints.conversation(selected!),enabled:!!selected});
  const messages=detail.data?.messages||[];

  useEffect(()=>{const url=new URL(location.href);if(selected){url.searchParams.set('conversation',selected)}else{url.searchParams.delete('conversation')}history.replaceState({},'',url)},[selected]);
  useEffect(()=>{const node=messageList.current;if(node&&shouldFollow.current)node.scrollTo({top:node.scrollHeight})},[messages.length]);

  const refresh=(id=selected)=>{queryClient.invalidateQueries({queryKey:conversationKey});if(id)queryClient.invalidateQueries({queryKey:['ai-conversation',id]});[keys.products,keys.low,keys.out,keys.drafts,keys.incoming,keys.marketOrders].forEach(k=>queryClient.invalidateQueries({queryKey:k}))};
  const create=useMutation({mutationFn:endpoints.createConversation,onSuccess:c=>{setSelected(c.id);setHistoryOpen(false);queryClient.invalidateQueries({queryKey:conversationKey});setTimeout(()=>input.current?.focus())}});
  const remove=useMutation({mutationFn:endpoints.deleteConversation,onSuccess:()=>{setSelected(null);queryClient.invalidateQueries({queryKey:conversationKey})}});
  const mutation=useMutation({mutationFn:({id,text}:{id:string;text:string})=>endpoints.chat(id,text),onSuccess:(_,variables)=>{setMessage('');refresh(variables.id)}});
  const confirmMutation=useMutation({mutationFn:()=>endpoints.confirm(selected!),onSuccess:()=>refresh(selected)});
  const submit=async()=>{const text=message.trim();if(!text||submitting.current)return;submitting.current=true;shouldFollow.current=true;try{let id=selected;if(!id){const conversation=await create.mutateAsync();id=conversation.id}await mutation.mutateAsync({id,text})}catch{/* Mutation state renders the API error. */}finally{submitting.current=false}};
  const latest=[...messages].reverse().find(m=>m.response)?.response;

  return <div className="ai-workspace">
    <div className="flex gap-2 lg:hidden">
      <button className="btn-light" onClick={()=>setHistoryOpen(true)}><Menu size={18}/>Sohbetler</button>
      <button className="btn-light ml-auto" onClick={()=>setTraceOpen(true)}><PanelRight size={18}/>Karar günlüğü</button>
    </div>
    {(historyOpen||traceOpen)&&<button className="fixed inset-0 z-40 bg-slate-900/30 lg:hidden" aria-label="Panelleri kapat" onClick={()=>{setHistoryOpen(false);setTraceOpen(false)}}/>}
    <ConversationList items={conversations.data?.items||[]} selected={selected} loading={conversations.isLoading} error={conversations.isError} open={historyOpen} onClose={()=>setHistoryOpen(false)} onSelect={id=>{setSelected(id);setHistoryOpen(false);shouldFollow.current=true}} onNew={()=>create.mutate()} onDelete={id=>{if(confirm('Bu sohbet kalıcı olarak silinsin mi?'))remove.mutate(id)}}/>
    <section className="card flex min-h-0 min-w-0 flex-col overflow-hidden !p-0">
      <div className="border-b px-5 py-3"><h1 className="font-bold">{displayConversationTitle(detail.data?.title||'AI İşlem Merkezi')}</h1>{mutation.isPending&&<p className="mt-1 text-xs text-blue-600" role="status">Yanıt ve sohbet başlığı hazırlanıyor…</p>}</div>
      <div ref={messageList} className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-5 sm:px-6 [scrollbar-gutter:stable]" onScroll={event=>{const node=event.currentTarget;shouldFollow.current=node.scrollHeight-node.scrollTop-node.clientHeight<80}}>
        {detail.isLoading&&<div className="grid h-full min-h-64 place-items-center"><span className="flex items-center gap-2 text-sm text-slate-500"><Spinner/>Sohbet yükleniyor…</span></div>}
        {!detail.isLoading&&messages.length===0&&<EmptyChat/>}
        {messages.map(m=><MessageBubble key={m.id} message={m}/>)}
        {mutation.isPending&&<div className="flex gap-3 text-sm text-slate-500"><Spinner/>AI yanıtı hazırlanıyor…</div>}
        {(mutation.isError||detail.isError)&&<p role="alert" className="rounded-xl bg-red-50 p-4 text-red-700">{errorMessage(mutation.error||detail.error)}</p>}
      </div>
      <Composer message={message} setMessage={setMessage} input={input} submit={submit} pending={mutation.isPending||create.isPending}/>
    </section>
    <div className={`fixed inset-y-0 right-0 z-50 w-[min(90vw,21rem)] overflow-y-auto bg-white p-4 shadow-xl transition-transform lg:static lg:z-auto lg:w-auto lg:translate-x-0 lg:bg-transparent lg:p-0 lg:shadow-none ${traceOpen?'translate-x-0':'translate-x-full'}`}>
      <button className="mb-3 ml-auto block lg:hidden" onClick={()=>setTraceOpen(false)} aria-label="Karar günlüğünü kapat"><X/></button>
      <TracePanel response={latest} onConfirm={()=>confirmMutation.mutate()} confirming={confirmMutation.isPending}/>
    </div>
  </div>
}

export function Composer({message,setMessage,input,submit,pending}:{message:string;setMessage:(value:string)=>void;input:React.RefObject<HTMLTextAreaElement>;submit:()=>void;pending:boolean}){
  const [open,setOpen]=useState(false);const root=useRef<HTMLDivElement>(null);
  useEffect(()=>{const close=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')setOpen(false)};document.addEventListener('mousedown',close);document.addEventListener('keydown',escape);return()=>{document.removeEventListener('mousedown',close);document.removeEventListener('keydown',escape)}},[]);
  return <div className="sticky bottom-0 z-10 shrink-0 border-t bg-white/95 p-3 backdrop-blur sm:p-4" ref={root}>
    <div className="relative rounded-2xl border border-slate-200 bg-white shadow-sm focus-within:border-blue-400">
      {open&&<div className="absolute bottom-full left-0 right-0 z-20 mb-2 max-h-56 overflow-y-auto rounded-2xl border bg-white p-2 shadow-xl" role="menu" aria-label="Hazır komutlar">{prompts.map(p=><button role="menuitem" className="block w-full rounded-xl px-3 py-2 text-left text-sm hover:bg-blue-50 focus:bg-blue-50" key={p} onClick={()=>{setMessage(p);setOpen(false);input.current?.focus()}}>{p}</button>)}</div>}
      <textarea ref={input} aria-label="AI komutu" maxLength={4000} className="min-h-20 w-full resize-none rounded-2xl bg-transparent px-4 py-3 text-sm outline-none" placeholder="Ne yapmak istediğinizi yazın…" value={message} onChange={e=>setMessage(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit()}}}/>
      <div className="flex items-center justify-between gap-2 px-2 pb-2"><button type="button" aria-haspopup="menu" aria-expanded={open} className="btn-light !px-3 !py-1.5" onClick={()=>setOpen(value=>!value)}><Lightbulb size={16}/>Öneriler</button><span className="ml-auto hidden text-xs text-slate-400 sm:block">Enter ile gönder · Shift+Enter yeni satır</span><button aria-label="Gönder" className="btn-primary !p-2.5" disabled={pending||!message.trim()} onClick={submit}>{pending?<Spinner/>:<Send size={19}/>}</button></div>
    </div>
  </div>
}

function EmptyChat(){return <div className="grid h-full min-h-72 place-items-center text-center"><div><div className="mx-auto mb-4 w-fit rounded-2xl bg-blue-100 p-4 text-blue-600"><Bot size={34}/></div><h2 className="text-xl font-bold">Operasyon asistanınız hazır</h2><p className="mt-2 text-slate-500">Bir komut yazın veya kompakt önerilerden yararlanın.</p></div></div>}
function MessageBubble({message}:{message:ChatMessage}){const user=message.role==='user',authorizationDenied=!user&&message.status==='failed'&&Boolean(getAuthorizationBlock(message.response)),noAction=!user&&message.status==='failed'&&!authorizationDenied&&isBusinessNoOp(message.response),failed=message.status==='failed'&&!noAction&&!authorizationDenied;return <div className={`flex w-fit max-w-[92%] gap-3 rounded-2xl px-4 py-3 sm:max-w-[85%] sm:px-5 sm:py-4 ${user?'ml-auto bg-blue-600 text-white':'bg-slate-100'} ${failed?'border border-red-300':authorizationDenied?'border border-amber-300':noAction?'border border-blue-200':''}`}>{user?<User className="shrink-0" size={19}/>:<Bot className="shrink-0 text-blue-600" size={20}/>}<div className="min-w-0 overflow-hidden"><p className="message-content whitespace-pre-wrap break-words">{message.content}</p>{failed&&<small className="text-red-600">İşlem başarısız oldu</small>}{authorizationDenied&&<small className="text-amber-700">Yetki nedeniyle engellendi</small>}{noAction&&<small className="text-blue-600">İşlem gerekmiyor</small>}</div></div>}
function ConversationList({items,selected,loading,error,open,onClose,onSelect,onNew,onDelete}:{items:ConversationSummary[];selected:string|null;loading:boolean;error:boolean;open:boolean;onClose:()=>void;onSelect:(id:string)=>void;onNew:()=>void;onDelete:(id:string)=>void}){let last='';return <aside className={`fixed inset-y-0 left-0 z-50 w-[min(88vw,18rem)] overflow-auto border-r bg-white p-4 shadow-xl transition-transform lg:static lg:z-auto lg:w-auto lg:translate-x-0 lg:rounded-2xl lg:border lg:shadow-sm ${open?'translate-x-0':'-translate-x-full'}`} aria-label="Sohbet geçmişi"><div className="mb-4 flex items-center justify-between"><b className="flex items-center gap-2"><History size={18}/>Sohbetler</b><button className="lg:hidden" onClick={onClose} aria-label="Kapat"><X/></button></div><button className="btn-primary mb-4 w-full" disabled={loading} onClick={onNew}><Plus size={17}/>Yeni sohbet</button>{loading&&<div className="flex justify-center p-5"><Spinner/></div>}{error&&<p role="alert" className="text-sm text-red-600">Sohbet geçmişi yüklenemedi.</p>}{!loading&&!error&&items.length===0&&<p className="py-8 text-center text-sm text-slate-400">Henüz bir sohbet yok.</p>}<div className="space-y-1">{items.map(item=>{const group=groupLabel(item.updated_at);const heading=group!==last;last=group;const title=displayConversationTitle(item.title);return <div key={item.id}>{heading&&<p className="mb-1 mt-4 text-xs font-semibold text-slate-400">{group}</p>}<div className={`group flex rounded-xl ${selected===item.id?'bg-blue-50 text-blue-700':'hover:bg-slate-50'}`}><button className="min-w-0 flex-1 p-2.5 text-left" title={title} onClick={()=>onSelect(item.id)}><span className="block truncate text-sm font-medium">{title}</span><small className="text-slate-400">{new Date(item.updated_at).toLocaleString('tr-TR',{dateStyle:'short',timeStyle:'short'})}</small></button><button aria-label={`${title} sohbetini sil`} className="px-2 opacity-50 hover:text-red-600 group-hover:opacity-100" onClick={()=>onDelete(item.id)}><Trash2 size={16}/></button></div></div>})}</div></aside>}
