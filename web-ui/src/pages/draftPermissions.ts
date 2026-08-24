import type {AuthRole} from '../api/client';

export function canApproveDraft(role:AuthRole|undefined,status:string){
  return (role==='MANAGER'||role==='ADMIN')&&status==='PENDING';
}
