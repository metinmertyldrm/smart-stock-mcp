const backend=import.meta.env.VITE_API_BASE_URL||'http://localhost:8081';
const llm=import.meta.env.VITE_LLM_HOST_URL||'http://localhost:8000';
export class ApiError extends Error {constructor(message:string,public status:number){super(message)}}

export type AuthMode='anonymous'|'local';
export type AuthRole='VIEWER'|'OPERATOR'|'MANAGER'|'ADMIN';
export type AuthUser={id:string;username:string;displayName:string;role:AuthRole;enabled:boolean;capabilities:string[]};
type LoginResponse={token:string;expiresAt?:string;user:AuthUser};
const sessionKey='smart-stock-session-token';export const authChangedEvent='smart-stock-auth-changed';let sessionPromise:Promise<string>|null=null;let authModePromise:Promise<AuthMode>|null=null;
function notifyAuthChange(){window.dispatchEvent(new Event(authChangedEvent))}
function storeSession(token:string){localStorage.setItem(sessionKey,token);notifyAuthChange()}
function clearSession(){const had=localStorage.getItem(sessionKey);localStorage.removeItem(sessionKey);if(had)notifyAuthChange()}

async function parseError(response:Response,fallback:string){let message=fallback;try{const body=await response.json() as {detail?:string};message=body.detail||message}catch{/* response is not JSON */}return new ApiError(message,response.status)}
async function authMode():Promise<AuthMode>{if(!authModePromise)authModePromise=fetch(llm+'/api/auth/config',{headers:{Accept:'application/json'}}).then(async response=>{if(!response.ok)throw await parseError(response,'Kimlik yapılandırması alınamadı.');const body=await response.json() as {mode?:AuthMode};if(body.mode!=='anonymous'&&body.mode!=='local')throw new ApiError('Kimlik yapılandırması geçersiz.',500);return body.mode}).catch(error=>{authModePromise=null;throw error});return authModePromise}
async function issueSession():Promise<string>{let response:Response;try{response=await fetch(llm+'/api/session',{method:'POST',headers:{Accept:'application/json'}})}catch{throw new ApiError('AI servisine şu anda ulaşılamıyor. Lütfen kısa bir süre sonra tekrar deneyin.',0)}if(!response.ok)throw await parseError(response,'Güvenli oturum oluşturulamadı.');const body=await response.json() as {token?:string};if(!body.token)throw new ApiError('Güvenli oturum yanıtı geçersiz.',500);storeSession(body.token);return body.token}
async function sessionToken():Promise<string>{const existing=localStorage.getItem(sessionKey);if(existing)return existing;const mode=await authMode();if(mode==='local')throw new ApiError('Devam etmek için oturum açın.',401);if(!sessionPromise)sessionPromise=issueSession().finally(()=>{sessionPromise=null});return sessionPromise}
async function request<T>(url:string,options?:RequestInit):Promise<T>{const isLlm=url.startsWith(llm);const headers:Record<string,string>={'Content-Type':'application/json'};if(isLlm)headers.Authorization=`Bearer ${await sessionToken()}`;if(options?.headers)Object.assign(headers,options.headers);let response:Response;try{response=await fetch(url,{...options,headers})}catch{throw new ApiError('AI servisine şu anda ulaşılamıyor. Lütfen kısa bir süre sonra tekrar deneyin.',0)}if(response.status===401&&isLlm)clearSession();if(!response.ok)throw await parseError(response,response.status===401?'Oturum süresi doldu. Lütfen yeniden giriş yapın.':'İstek tamamlanamadı.');if(response.status===204)return undefined as T;return response.json() as Promise<T>}

async function login(username:string,password:string):Promise<AuthUser>{let response:Response;try{response=await fetch(llm+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify({username,password})})}catch{throw new ApiError('AI servisine şu anda ulaşılamıyor. Lütfen kısa bir süre sonra tekrar deneyin.',0)}if(!response.ok)throw await parseError(response,'Giriş yapılamadı.');const body=await response.json() as LoginResponse;if(!body.token||!body.user)throw new ApiError('Giriş yanıtı geçersiz.',500);storeSession(body.token);return body.user}
async function logout():Promise<void>{try{await request<void>(llm+'/api/auth/logout',{method:'POST'})}finally{clearSession()}}
async function me():Promise<AuthUser>{const body=await request<{user:AuthUser}>(llm+'/api/auth/me');return body.user}
function hasSession(){return Boolean(localStorage.getItem(sessionKey))}

export const api={
 get:<T>(path:string)=>request<T>(backend+path),
 post:<T>(path:string,data?:unknown)=>request<T>(backend+path,{method:'POST',body:JSON.stringify(data??{})}),
 llmGet:<T>(path:string)=>request<T>(llm+path),
 llmPost:<T>(path:string,data?:unknown)=>request<T>(llm+path,{method:'POST',body:JSON.stringify(data??{})}),
 llmDelete:<T>(path:string)=>request<T>(llm+path,{method:'DELETE'}),
 auth:{mode:authMode,login,logout,me,clearSession,hasSession},
};
