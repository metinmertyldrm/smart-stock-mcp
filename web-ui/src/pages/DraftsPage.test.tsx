import {describe,expect,it} from 'vitest';
import type {Draft} from '../types';
import {draftQuantitySummary} from '../utils/drafts';
import {canApproveDraft,canDeleteDraft,canRejectDraft} from './draftPermissions';

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

  it('allows managers and admins to reject only pending drafts',()=>{
    expect(canRejectDraft('MANAGER','PENDING')).toBe(true);
    expect(canRejectDraft('ADMIN','PENDING')).toBe(true);
    expect(canRejectDraft('OPERATOR','PENDING')).toBe(false);
    expect(canRejectDraft('ADMIN','REJECTED')).toBe(false);
  });

  it('allows only admins to delete non-confirmed drafts',()=>{
    expect(canDeleteDraft('ADMIN','PENDING')).toBe(true);
    expect(canDeleteDraft('ADMIN','REJECTED')).toBe(true);
    expect(canDeleteDraft('MANAGER','PENDING')).toBe(false);
    expect(canDeleteDraft('ADMIN','CONFIRMED')).toBe(false);
  });

  it('shows product and quantity counts instead of the ambiguous item label',()=>{
    const draft={items:[
      {product:{id:1},quantity:6},
      {product:{id:1},quantity:2},
      {product:{id:2},quantity:30},
    ]} as unknown as Draft;

    expect(draftQuantitySummary(draft)).toBe('2 ürün · 38 adet');
  });
});
