import type {AuthMode,AuthRole} from '../api/client';
import type {DraftStatus} from '../types';

// Anonim kipte sunucu rol atamaz ve rbac.py rolsuz istegi kisitsiz sayar;
// secure_api.py onay yetkisini yalnizca local kipte denetler. Arayuz de ayni
// siniri uygulamali, yoksa arka ucun izin verdigi islem gizlenmis olur.
function unrestricted(mode:AuthMode|undefined){return mode==='anonymous'}

export function canApproveDraft(role:AuthRole|undefined,status:DraftStatus,mode?:AuthMode){
  if(status!=='PENDING')return false;
  return unrestricted(mode)||role==='MANAGER'||role==='ADMIN';
}

export function canRejectDraft(role:AuthRole|undefined,status:DraftStatus,mode?:AuthMode){
  if(status!=='PENDING')return false;
  return unrestricted(mode)||role==='MANAGER'||role==='ADMIN';
}

export function canDeleteDraft(role:AuthRole|undefined,status:DraftStatus,mode?:AuthMode){
  if(status!=='PENDING'&&status!=='REJECTED')return false;
  return unrestricted(mode)||role==='ADMIN';
}
