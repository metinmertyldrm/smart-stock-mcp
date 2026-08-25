import type {AuthRole} from '../api/client';
import type {DraftStatus} from '../types';

export function canApproveDraft(role:AuthRole|undefined,status:DraftStatus){
  return (role==='MANAGER'||role==='ADMIN')&&status==='PENDING';
}

export function canRejectDraft(role:AuthRole|undefined,status:DraftStatus){
  return (role==='MANAGER'||role==='ADMIN')&&status==='PENDING';
}

export function canDeleteDraft(role:AuthRole|undefined,status:DraftStatus){
  return role==='ADMIN'&&(status==='PENDING'||status==='REJECTED');
}
