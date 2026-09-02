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

  it('keeps local-mode rules when a role is present but the mode is passed',()=>{
    expect(canApproveDraft('OPERATOR','PENDING','local')).toBe(false);
    expect(canRejectDraft('OPERATOR','PENDING','local')).toBe(false);
    expect(canDeleteDraft('MANAGER','PENDING','local')).toBe(false);
    expect(canApproveDraft('MANAGER','PENDING','local')).toBe(true);
  });

  // Anonim kipte sunucu rol atamaz; arka uc istegi kisitsiz calistirir.
  // Arayuzun dugmeyi gizlemesi, izin verilen islemi erisilemez kiliyordu.
  it('allows the pending draft actions in anonymous mode where no role exists',()=>{
    expect(canApproveDraft(undefined,'PENDING','anonymous')).toBe(true);
    expect(canRejectDraft(undefined,'PENDING','anonymous')).toBe(true);
    expect(canDeleteDraft(undefined,'PENDING','anonymous')).toBe(true);
  });

  it('still respects the draft status in anonymous mode',()=>{
    expect(canApproveDraft(undefined,'CONFIRMED','anonymous')).toBe(false);
    expect(canRejectDraft(undefined,'REJECTED','anonymous')).toBe(false);
    expect(canDeleteDraft(undefined,'CONFIRMED','anonymous')).toBe(false);
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
