import {render,screen} from '@testing-library/react';
import {describe,expect,it} from 'vitest';
import {IncomingOrderCard} from './OrdersPage';
import type {IncomingOrder} from '../types';

const now=new Date('2026-08-19T10:00:00');
const product={id:1,sku:'SKU-1',name:'Telefon',stockQuantity:1,minimumStock:2,targetStock:5};
function order(status:string,expectedDeliveryDate?:string):IncomingOrder{
  return {id:7,product,quantity:3,status,expectedDeliveryDate};
}

describe('incoming order delivery state',()=>{
  it('gelecek tarihli pending siparişi Yolda olarak etiketler',()=>{
    render(<IncomingOrderCard order={order('PENDING','2026-08-20T10:00:00')} now={now}/>);
    expect(screen.getByText('Yolda')).toBeInTheDocument();
  });
  it('tarihi gelmiş pending siparişi Teslim alınabilir olarak etiketler',()=>{
    render(<IncomingOrderCard order={order('PENDING','2026-08-19T09:59:00')} now={now}/>);
    expect(screen.getByText('Teslim alınabilir')).toBeInTheDocument();
  });
  it('teslim alınmış siparişi açık metinle gösterir',()=>{
    render(<IncomingOrderCard order={order('RECEIVED','2026-08-20T10:00:00')} now={now}/>);
    expect(screen.getByText('Teslim alındı')).toBeInTheDocument();
  });
});
