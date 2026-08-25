import {describe,expect,it} from 'vitest';
import type {IncomingOrder,Product} from '../types';
import {pendingIncomingQuantity,recommendedReplenishment} from './InventoryPage';

const product:Product={
  id:2,
  sku:'DELL-LAT-5440',
  name:'Dell Latitude 5440',
  stockQuantity:1,
  minimumStock:3,
  targetStock:5,
};

const order=(id:number,quantity:number,status:string):IncomingOrder=>({
  id,
  product,
  quantity,
  status,
});

describe('pending-aware inventory recommendation',()=>{
  it('yalnızca bekleyen ikmalleri toplar',()=>{
    const orders=[order(7,1,'PENDING'),order(8,4,'RECEIVED')];
    expect(pendingIncomingQuantity(product.id,orders)).toBe(1);
  });

  it('yoldaki miktarı önerilen ikmalden düşer',()=>{
    expect(recommendedReplenishment(product,[order(7,1,'PENDING')])).toBe(3);
  });

  it('hedefi aşan yoldaki miktarda negatif öneri göstermez',()=>{
    expect(recommendedReplenishment(product,[order(7,10,'PENDING')])).toBe(0);
  });
});
