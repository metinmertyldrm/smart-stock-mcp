import type {Draft} from '../types';

export function draftQuantitySummary(draft:Draft){
  const productCount=new Set(draft.items.map(item=>item.product.id)).size;
  const totalQuantity=draft.items.reduce((total,item)=>total+item.quantity,0);
  return `${productCount} ürün · ${totalQuantity} adet`;
}
