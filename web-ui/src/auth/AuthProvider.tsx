import {createContext,useContext,useEffect,useMemo,useState} from 'react';
import {useQueryClient} from '@tanstack/react-query';
import {LockKeyhole,LogIn,RefreshCw,ShieldCheck} from 'lucide-react';
import {api,ApiError,authChangedEvent,type AuthMode,type AuthUser} from '../api/client';
import {Spinner} from '../components/States';

type AuthContextValue={mode:AuthMode;user:AuthUser|null;login:(username:string,password:string)=>Promise<void>;logout:()=>Promise<void>};
const AuthContext=createContext<AuthContextValue|null>(null);

export function useAuth(){const value=useContext(AuthContext);if(!value)throw new Error('useAuth must be used inside AuthProvider');return value}

export function AuthProvider({children}:{children:React.ReactNode}){
 const queryClient=useQueryClient();
 const [mode,setMode]=useState<AuthMode|null>(null);const [user,setUser]=useState<AuthUser|null>(null);const [loading,setLoading]=useState(true);const [startupError,setStartupError]=useState('');
 const acceptUser=(current:AuthUser)=>{queryClient.clear();setUser(current)};
 const forgetUser=()=>{queryClient.clear();setUser(null)};
 const initialize=async()=>{setLoading(true);setStartupError('');try{const nextMode=await api.auth.mode();setMode(nextMode);if(nextMode==='local'){if(api.auth.hasSession()){try{acceptUser(await api.auth.me())}catch{api.auth.clearSession();forgetUser()}}else forgetUser()}}catch(exc){setStartupError(exc instanceof ApiError?exc.message:'Kimlik servisine ulaşılamadı.')}finally{setLoading(false)}};
 useEffect(()=>{void initialize()},[]);
 useEffect(()=>{const changed=()=>{if(mode==='local'&&!api.auth.hasSession())forgetUser()};window.addEventListener(authChangedEvent,changed);window.addEventListener('storage',changed);return()=>{window.removeEventListener(authChangedEvent,changed);window.removeEventListener('storage',changed)}},[mode,queryClient]);
 const value=useMemo<AuthContextValue|null>(()=>mode?{mode,user,login:async(username,password)=>{acceptUser(await api.auth.login(username,password))},logout:async()=>{try{await api.auth.logout()}finally{forgetUser()}}}:null,[mode,user,queryClient]);
 if(loading)return <AuthLoading/>;
 if(startupError||!mode)return <AuthStartupError message={startupError||'Kimlik yapılandırması alınamadı.'} retry={initialize}/>;
 if(mode==='local'&&!user)return <LoginPage onLogin={async(username,password)=>{acceptUser(await api.auth.login(username,password))}}/>;
 return <AuthContext.Provider value={value!}>{children}</AuthContext.Provider>;
}

function AuthLoading(){return <div className="grid min-h-screen place-items-center bg-slate-50"><div className="flex items-center gap-3 rounded-2xl border bg-white px-5 py-4 text-sm text-slate-600 shadow-sm"><Spinner/>Güvenli oturum hazırlanıyor…</div></div>}
function AuthStartupError({message,retry}:{message:string;retry:()=>Promise<void>}){return <div className="grid min-h-screen place-items-center bg-slate-50 p-4"><div className="w-full max-w-md rounded-2xl border bg-white p-6 text-center shadow-sm"><h1 className="font-bold">Kimlik servisine ulaşılamadı</h1><p className="mt-2 text-sm text-slate-500">{message}</p><button className="btn-primary mx-auto mt-4" onClick={()=>void retry()}><RefreshCw size={17}/>Tekrar dene</button></div></div>}

function LoginPage({onLogin}:{onLogin:(username:string,password:string)=>Promise<void>}){
 const [username,setUsername]=useState('');const [password,setPassword]=useState('');const [pending,setPending]=useState(false);const [error,setError]=useState('');
 const submit=async(event:React.FormEvent)=>{event.preventDefault();if(pending||!username.trim()||!password)return;setPending(true);setError('');try{await onLogin(username.trim(),password)}catch(exc){setError(exc instanceof ApiError?exc.message:'Giriş yapılamadı.')}finally{setPending(false)}};
 return <main className="grid min-h-screen place-items-center bg-slate-50 p-4"><section className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-7 shadow-xl shadow-slate-200/50"><div className="mb-7 flex items-center gap-3"><div className="rounded-2xl bg-blue-600 p-3 text-white"><ShieldCheck size={28}/></div><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-blue-600">Smart Stock</p><h1 className="text-2xl font-bold text-slate-900">Güvenli giriş</h1></div></div><p className="mb-6 text-sm leading-6 text-slate-500">AI işlemleri ve karar günlüğü kullanıcı kimliğinize ve rolünüze bağlıdır.</p><form className="space-y-4" onSubmit={submit}><label className="block text-sm font-medium text-slate-700">Kullanıcı adı<input autoComplete="username" autoFocus value={username} onChange={event=>setUsername(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none focus:border-blue-500" placeholder="kullanici.adi"/></label><label className="block text-sm font-medium text-slate-700">Parola<input type="password" autoComplete="current-password" value={password} onChange={event=>setPassword(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none focus:border-blue-500" placeholder="••••••••••••"/></label>{error&&<p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}<button className="btn-primary w-full justify-center" disabled={pending||!username.trim()||!password}>{pending?<Spinner/>:<LogIn size={18}/>}Giriş yap</button></form><div className="mt-6 flex items-start gap-2 rounded-xl bg-slate-50 p-3 text-xs leading-5 text-slate-500"><LockKeyhole className="mt-0.5 shrink-0" size={15}/>Yetkiler sunucu tarafından uygulanır; tarayıcıdaki rol bilgisi yalnızca görünürlük sağlar.</div></section></main>
}
