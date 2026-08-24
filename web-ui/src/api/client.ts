const backend=import.meta.env.VITE_API_BASE_URL||'http://localhost:8081';
const llm=import.meta.env.VITE_LLM_HOST_URL||'http://localhost:8000';
export class ApiError extends Error {constructor(message:string,public status:number){super(message)}}

export type AuthMode='anonymous'|'local';
export type AuthRole='VIEWER'|'OPERATOR'|'MANAGER'|'ADMIN';
export type AuthUser={id:string;username:string;displayName:string;role:AuthRole;enabled:boolean;capabilities:string[]};
type AuthConfig={mode:AuthMode;sessionTransport?:'bearer'|'cookie';csrfHeader?:string|null};
type LoginResponse={expiresAt?:string;user:AuthUser};
const sessionKey='smart-stock-session-token';
const authEpochKey='smart-stock-auth-epoch';
const csrfCookieName='smart_stock_csrf';
export const authChangedEvent='smart-stock-auth-changed';
let sessionPromise:Promise<string>|null=null;
let authConfigPromise:Promise<AuthConfig>|null=null;
const stockUsesSameOriginGateway=backend==='/stock'||backend.startsWith('/stock/');

function notifyAuthChange(){
  try{localStorage.setItem(authEpochKey,`${Date.now()}-${Math.random()}`)}catch{/* storage may be unavailable */}
  window.dispatchEvent(new Event(authChangedEvent));
}
function storeAnonymousSession(token:string){localStorage.setItem(sessionKey,token);notifyAuthChange()}
function clearClientSession(){const had=localStorage.getItem(sessionKey);localStorage.removeItem(sessionKey);if(had)notifyAuthChange()}
function cookieValue(name:string){const prefix=`${encodeURIComponent(name)}=`;for(const part of document.cookie.split(';')){const item=part.trim();if(item.startsWith(prefix))return decodeURIComponent(item.slice(prefix.length))}return null}

async function parseError(response:Response,fallback:string){let message=fallback;try{const body=await response.json() as {detail?:string};message=body.detail||message}catch{/* response is not JSON */}return new ApiError(message,response.status)}
async function authConfig():Promise<AuthConfig>{if(!authConfigPromise)authConfigPromise=fetch(llm+'/api/auth/config',{headers:{Accept:'application/json'},credentials:'include'}).then(async response=>{if(!response.ok)throw await parseError(response,'Kimlik yapılandırması alınamadı.');const body=await response.json() as AuthConfig;if(body.mode!=='anonymous'&&body.mode!=='local')throw new ApiError('Kimlik yapılandırması geçersiz.',500);return body}).catch(error=>{authConfigPromise=null;throw error});return authConfigPromise}
async function authMode():Promise<AuthMode>{return (await authConfig()).mode}
async function issueSession():Promise<string>{let response:Response;try{response=await fetch(llm+'/api/session',{method:'POST',headers:{Accept:'application/json'}})}catch{throw new ApiError('AI servisine şu anda ulaşılamıyor. Lütfen kısa bir süre sonra tekrar deneyin.',0)}if(!response.ok)throw await parseError(response,'Güvenli oturum oluşturulamadı.');const body=await response.json() as {token?:string};if(!body.token)throw new ApiError('Güvenli oturum yanıtı geçersiz.',500);storeAnonymousSession(body.token);return body.token}
async function anonymousSessionToken():Promise<string>{const existing=localStorage.getItem(sessionKey);if(existing)return existing;const mode=await authMode();if(mode==='local')throw new ApiError('Devam etmek için oturum açın.',401);if(!sessionPromise)sessionPromise=issueSession().finally(()=>{sessionPromise=null});return sessionPromise}
function isMutation(method?:string){return ['POST','PUT','PATCH','DELETE'].includes((method||'GET').toUpperCase())}
async function request<T>(url:string,options?:RequestInit):Promise<T>{const isLlm=url.startsWith(llm);const isBackend=url.startsWith(backend);const config=(isLlm||isBackend&&stockUsesSameOriginGateway)?await authConfig():null;const localGatewayBackend=isBackend&&stockUsesSameOriginGateway&&config?.mode==='local';const protectedRequest=isLlm||localGatewayBackend;const headers:Record<string,string>={'Content-Type':'application/json'};let credentials:RequestCredentials|undefined;
if(protectedRequest&&config?.mode==='local'){credentials='include';if(isLlm&&isMutation(options?.method)){const csrf=cookieValue(csrfCookieName);if(csrf)headers[config.csrfHeader||'X-CSRF-Token']=csrf}}
else if(protectedRequest){headers.Authorization=`Bearer ${await anonymousSessionToken()}`}
if(options?.headers)Object.assign(headers,options.headers);let response:Response;try{response=await fetch(url,{...options,headers,credentials})}catch{throw new ApiError('AI servisine şu anda ulaşılamıyor. Lütfen kısa bir süre sonra tekrar deneyin.',0)}if(response.status===401&&protectedRequest){if(config?.mode==='anonymous')clearClientSession();else notifyAuthChange()}if(!response.ok)throw await parseError(response,response.status===401?'Oturum süresi doldu. Lütfen yeniden giriş yapın.':'İstek tamamlanamadı.');if(response.status===204)return undefined as T;return response.json() as Promise<T>}

async function login(username:string,password:string):Promise<AuthUser>{let response:Response;try{response=await fetch(llm+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json'},credentials:'include',body:JSON.stringify({username,password})})}catch{throw new ApiError('AI servisine şu anda ulaşılamıyor. Lütfen kısa bir süre sonra tekrar deneyin.',0)}if(!response.ok)throw await parseError(response,'Giriş yapılamadı.');const body=await response.json() as LoginResponse;if(!body.user)throw new ApiError('Giriş yanıtı geçersiz.',500);localStorage.removeItem(sessionKey);notifyAuthChange();return body.user}
async function logout():Promise<void>{try{await request<void>(llm+'/api/auth/logout',{method:'POST'})}finally{clearClientSession();notifyAuthChange()}}
async function me():Promise<AuthUser>{const body=await request<{user:AuthUser}>(llm+'/api/auth/me');return body.user}
function hasAnonymousSession(){return Boolean(localStorage.getItem(sessionKey))}

export const api={
 get:<T>(path:string)=>request<T>(backend+path),
 post:<T>(path:string,data?:unknown)=>request<T>(backend+path,{method:'POST',body:JSON.stringify(data??{})}),
 llmGet:<T>(path:string)=>request<T>(llm+path),
 llmPost:<T>(path:string,data?:unknown)=>request<T>(llm+path,{method:'POST',body:JSON.stringify(data??{})}),
 llmDelete:<T>(path:string)=>request<T>(llm+path,{method:'DELETE'}),
 auth:{mode:authMode,login,logout,me,clearSession:clearClientSession,hasSession:hasAnonymousSession},
};