import type {Product} from '../types';
export const currency=(value:number)=>new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY'}).format(value);
export const date=(value?:string)=>value?new Intl.DateTimeFormat('tr-TR',{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'—';
export type StockState='out'|'critical'|'low'|'healthy';
export function stockState(p:Product):StockState{return p.stockQuantity===0?'out':p.stockQuantity<=p.minimumStock?'critical':p.stockQuantity<p.targetStock?'low':'healthy'}
export const stockLabel:Record<StockState,string>={out:'Stokta yok',critical:'Kritik',low:'Düşük',healthy:'Sağlıklı'};
export const errorMessage=(e:unknown)=>e instanceof Error?e.message:'Beklenmeyen bir hata oluştu.';
