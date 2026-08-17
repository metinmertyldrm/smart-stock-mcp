import {describe,expect,it} from 'vitest';import {currency,date,stockState} from './format';
const product={id:1,sku:'A',name:'Ürün',stockQuantity:0,minimumStock:5,targetStock:10};
describe('format helpers',()=>{it('Türk Lirası biçimler',()=>expect(currency(1234.5)).toContain('1.234,50'));it('Türkçe tarih biçimler',()=>expect(date('2025-01-02T12:00:00Z')).toContain('Oca'));it('stokta olmayanı hesaplar',()=>expect(stockState(product)).toBe('out'));it('düşük stoku hesaplar',()=>expect(stockState({...product,stockQuantity:7})).toBe('low'))});
