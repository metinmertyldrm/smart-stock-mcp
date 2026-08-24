import {describe,expect,it} from 'vitest';
import {canApproveDraft} from './draftPermissions';

describe('canApproveDraft',()=>{
  it('allows only managers and admins to approve pending drafts',()=>{
    expect(canApproveDraft('MANAGER','PENDING')).toBe(true);
    expect(canApproveDraft('ADMIN','PENDING')).toBe(true);
    expect(canApproveDraft('OPERATOR','PENDING')).toBe(false);
    expect(canApproveDraft('VIEWER','PENDING')).toBe(false);
  });

  it('does not allow an already confirmed draft to be approved again',()=>{
    expect(canApproveDraft('MANAGER','CONFIRMED')).toBe(false);
    expect(canApproveDraft('ADMIN','CONFIRMED')).toBe(false);
  });
});
