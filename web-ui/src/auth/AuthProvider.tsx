import {createContext,useContext,useEffect,useMemo,useState} from 'react';
import {LockKeyhole,LogIn,ShieldCheck} from 'lucide-react';
import {api,ApiError,type AuthMode,type AuthUser} from '../api/client';
import {Spinner} from '../components/States';

type AuthContextValue={mode:AuthMode;user:AuthUser|null;login:(username:string,password:string)=>Promise<void>;logout:()=>Promise<void>};
const AuthContext=createContext<AuthContextValue|null>(null);

export function useAuth(){const value=useContext(AuthContext);if(!value)throw new Error('useAuth must be used inside AuthProvider');return value}

export function AuthProvider({children}:{children:React.ReactNode}){
 const [mode,setMode]=useState<AuthMode|null>(null);const [user,setUser]=useState<AuthUser|null>(null);const [loading,setLoading]=useState(true);
 useEffect(()=>{let alive=true;(async()=>{try{const nextMode=await api.auth.mode();if(!alive)return;setMode(nextMode);if(nextMode==='local'&&api.auth.hasSession()){try{const current=await api.auth.me();if(alive)setUser(current)}catch{api.auth.clearSession()}}}finally{if(alive)setLoading(false)}})();return()=>{alive=false}},[]);
 const value=useMemo<AuthContextValue|null>(()=>mode?{mode,user,login:async(username,password)=>{const current=await api.auth.login(username,password);setUser(current)},logout:async()=>{await api.auth.logout();setUser(null)}}:null,[mode,user]);
 if(loading||!mode)return <AuthLoading/>;
 if(mode==='local'&&!user)return <LoginPage onLogin={async(username,password)=>{const current=await api.auth.login(username,password);setUser(current)}}/>;
 return <AuthContext.Provider value={value!}>{children}</AuthContext.Provider>;
}

function AuthLoading(){return <div className="grid min-h-screen place-items-center bg-slate-50"><div className="flex items-center gap-3 rounded-2xl border bg-white px-5 py-4 text-sm text-slate-600 shadow-sm"><Spinner/>Güvenli oturum hazırlanıyor…</div></div>}

function LoginPage({onLogin}:{onLogin:(username:string,password:string)=>Promise<void>}){
 const [username,setUsername]=useState('');const [password,setPassword]=useState('');const [pending,setPending]=useState(false);const [error,setError]=useState('');
 const submit=async(event:React.FormEvent)=>{event.preventDefault();if(pending||!username.trim()||!password)return;setPending(true);setError('');try{await onLogin(username.trim(),password)}catch(exc){setError(exc instanceof ApiError?exc.message:'Giriş yapılamadı.')}finally{setPending(false)}};
 return <main className="grid min-h-screen place-items-center bg-slate-50 p-4"><section className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-7 shadow-xl shadow-slate-200/50"><div className="mb-7 flex items-center gap-3"><div className="rounded-2xl bg-blue-600 p-3 text-white"><ShieldCheck size={28}/></div><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-blue-600">Smart Stock</p><h1 className="text-2xl font-bold text-slate-900">Güvenli giriş</h1></div></div><p className="mb-6 text-sm leading-6 text-slate-500">AI işlemleri ve karar günlüğü kullanıcı kimliğinize ve rolünüze bağlıdır.</p><form className="space-y-4" onSubmit={submit}><label className="block text-sm font-medium text-slate-700">Kullanıcı adı<input autoComplete="username" autoFocus value={username} onChange={event=>setUsername(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none focus:border-blue-500" placeholder="kullanici.adi"/></label><label className="block text-sm font-medium text-slate-700">Parola<input type="password" autoComplete="current-password" value={password} onChange={event=>setPassword(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none focus:border-blue-500" placeholder="••••••••••••"/></label>{error&&<p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}<button className="btn-primary w-full justify-center" disabled={pending||!username.trim()||!password}>{pending?<Spinner/>:<LogIn size={18}/>}Giriş yap</button></form><div className="mt-6 flex items-start gap-2 rounded-xl bg-slate-50 p-3 text-xs leading-5 text-slate-500"><LockKeyhole className="mt-0.5 shrink-0" size={15}/>Yetkiler sunucu tarafından uygulanır; tarayıcıdaki rol bilgisi yalnızca görünürlük sağlar.</div></section></main>
}
