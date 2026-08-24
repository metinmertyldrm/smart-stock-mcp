import {render,screen} from '@testing-library/react';
import {describe,expect,it} from 'vitest';
import type {ChatResponse} from '../types';
import {getAuthorizationBlock,isBusinessNoOp} from '../utils/chatOutcome';
import {TracePanel} from './DecisionJournal';

const viewerDenied={
  conversationId:'viewer-test',
  permissionLevel:'FULL',
  succeeded:false,
  plan:{
    type:'execution_plan',
    goal:'DRAFT',
    steps:[
      {id:'step_1',tool:'calculate_replenishment',arguments:{}},
      {id:'step_2',tool:'create_procurement_plan',arguments:{}},
      {id:'step_3',tool:'create_purchase_draft',arguments:{}},
    ],
    authorization:{status:'blocked',stage:'preflight',role:'VIEWER',stepId:'step_3',tool:'create_purchase_draft'},
  },
  trace:[
    {stepId:'step_1',tool:'calculate_replenishment',arguments:{},status:'skipped' as const,error:'Önceki adım başarısız olduğu için çalıştırılmadı.'},
    {stepId:'step_2',tool:'create_procurement_plan',arguments:{},status:'skipped' as const,error:'Önceki adım başarısız olduğu için çalıştırılmadı.'},
    {stepId:'step_3',tool:'create_purchase_draft',arguments:{},status:'failed' as const,error:'RBAC preflight blocked step=step_3 tool=create_purchase_draft role=VIEWER'},
  ],
  finalAnswer:'VIEWER rolü salt okunurdur. Bu istek kalıcı veri değişikliği gerektiriyor; hiçbir araç çalıştırılmadı.',
  explanation:{
    requestSummary:'Eksik stoklar için satın alma siparişi oluştur.',
    goalTitle:'Sipariş taslağı hazırla',
    goalExplanation:'DRAFT hedefi için 3 adımlı operasyon planı uygulandı.',
    permissionExplanation:'Yazma niyeti algılandı.',
    findings:[],
    decisionSummary:'VIEWER rolü salt okunurdur. Bu istek kalıcı veri değişikliği gerektiriyor; hiçbir araç çalıştırılmadı.',
    changes:[],
    userNextAction:'Ek işlem gerekmiyor.',
    safetyChecks:[{label:'Plan doğrulama',status:'blocked' as const,detail:'Plan güvenli biçimde tamamlanamadı.'}],
    warnings:['Plan tamamlanamadı.'],
    repaired:false,
  },
} as unknown as ChatResponse;

describe('RBAC authorization outcome',()=>{
  it('authorization bilgisini business no-op ile karıştırmaz',()=>{
    expect(getAuthorizationBlock(viewerDenied)).toMatchObject({role:'VIEWER',stage:'preflight',tool:'create_purchase_draft'});
    expect(isBusinessNoOp(viewerDenied)).toBe(false);
  });

  it('preflight reddini teknik hata yerine doğrulanmış yetki sınırı olarak gösterir',()=>{
    render(<TracePanel response={viewerDenied}/>);
    expect(screen.getByText('Yetki nedeniyle engellendi')).toBeInTheDocument();
    expect(screen.getByText('Doğrulanmış rol sınırı')).toBeInTheDocument();
    expect(screen.getAllByText('Çalıştırılmadı')).toHaveLength(2);
    expect(screen.getByText(/VIEWER rolü create_purchase_draft aracını çalıştıramaz/)).toBeInTheDocument();
    expect(screen.getByText('Rol yetkilendirmesi')).toBeInTheDocument();
    expect(screen.getByText('Bu işlem için OPERATOR veya daha yetkili bir kullanıcıyla devam edin.')).toBeInTheDocument();
    expect(screen.queryByText('İşlem tamamlanamadı')).not.toBeInTheDocument();
    expect(screen.queryByText('8. Uyarılar ve belirsizlikler')).not.toBeInTheDocument();
  });
});
