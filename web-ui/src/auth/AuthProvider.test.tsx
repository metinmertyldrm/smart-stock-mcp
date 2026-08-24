import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {beforeEach,describe,expect,it,vi} from 'vitest';

const mocks=vi.hoisted(()=>({
 mode:vi.fn(),
 me:vi.fn(),
 login:vi.fn(),
 logout:vi.fn(),
}));

vi.mock('../api/client',()=>{
 class ApiError extends Error{constructor(message:string,public status:number){super(message)}}
 return {
  ApiError,
  authChangedEvent:'smart-stock-auth-changed',
  api:{auth:{mode:mocks.mode,me:mocks.me,login:mocks.login,logout:mocks.logout}},
 };
});

import {AuthProvider,useAuth} from './AuthProvider';

const admin={id:'admin-id',username:'admin1',displayName:'Admin',role:'ADMIN' as const,enabled:true,capabilities:['read','draft','confirm','metrics','users']};

function SessionView(){const auth=useAuth();return <div><span>{auth.user?.username}</span><button onClick={()=>void auth.logout()}>Çıkış yap</button></div>}

function renderProvider(queryClient:QueryClient){
 return render(<QueryClientProvider client={queryClient}><AuthProvider><SessionView/></AuthProvider></QueryClientProvider>);
}

describe('AuthProvider cookie-session cache isolation',()=>{
 beforeEach(()=>{
  vi.clearAllMocks();
  mocks.mode.mockResolvedValue('local');
  mocks.me.mockRejectedValue(new Error('no session'));
  mocks.login.mockResolvedValue(admin);
  mocks.logout.mockResolvedValue(undefined);
 });

 it('checks server session on startup instead of browser token storage',async()=>{
  mocks.me.mockResolvedValueOnce(admin);
  const queryClient=new QueryClient({defaultOptions:{queries:{retry:false}}});
  renderProvider(queryClient);
  await screen.findByText('admin1');
  expect(mocks.me).toHaveBeenCalledTimes(1);
 });

 it('clears cached user-scoped data on login and logout',async()=>{
  const queryClient=new QueryClient({defaultOptions:{queries:{retry:false}}});
  queryClient.setQueryData(['ai-conversation','previous-user'],{secret:'previous-user-data'});
  renderProvider(queryClient);

  await screen.findByRole('heading',{name:'Güvenli giriş'});
  fireEvent.change(screen.getByLabelText('Kullanıcı adı'),{target:{value:'admin1'}});
  fireEvent.change(screen.getByLabelText('Parola'),{target:{value:'very-secret-password'}});
  fireEvent.click(screen.getByRole('button',{name:'Giriş yap'}));

  await screen.findByText('admin1');
  expect(queryClient.getQueryData(['ai-conversation','previous-user'])).toBeUndefined();

  queryClient.setQueryData(['ai-conversation','current-user'],{secret:'current-user-data'});
  fireEvent.click(screen.getByRole('button',{name:'Çıkış yap'}));

  await screen.findByRole('heading',{name:'Güvenli giriş'});
  await waitFor(()=>expect(queryClient.getQueryData(['ai-conversation','current-user'])).toBeUndefined());
  expect(mocks.logout).toHaveBeenCalledTimes(1);
 });

 it('re-checks /me and clears cache when another tab logs out',async()=>{
  mocks.me.mockResolvedValueOnce(admin);
  const queryClient=new QueryClient({defaultOptions:{queries:{retry:false}}});
  renderProvider(queryClient);

  await screen.findByText('admin1');
  queryClient.setQueryData(['ai-conversation','cross-tab-user'],{secret:'cross-tab-data'});
  mocks.me.mockRejectedValueOnce(new Error('session revoked'));
  window.dispatchEvent(new StorageEvent('storage',{key:'smart-stock-auth-epoch',oldValue:'1',newValue:'2'}));

  await screen.findByRole('heading',{name:'Güvenli giriş'});
  expect(queryClient.getQueryData(['ai-conversation','cross-tab-user'])).toBeUndefined();
 });

 it('re-checks /me when another tab logs in without sharing credentials',async()=>{
  const queryClient=new QueryClient({defaultOptions:{queries:{retry:false}}});
  renderProvider(queryClient);
  await screen.findByRole('heading',{name:'Güvenli giriş'});

  mocks.me.mockResolvedValueOnce(admin);
  window.dispatchEvent(new StorageEvent('storage',{key:'smart-stock-auth-epoch',oldValue:'1',newValue:'2'}));
  await screen.findByText('admin1');
 });
});