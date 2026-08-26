import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {fireEvent,render,screen} from '@testing-library/react';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import type {IncomingOrder,MarketOrder} from '../types';
import {
  IncomingOrderCard,
  MarketOrderCard,
  OrdersPage,
  marketOrderStatus,
} from './OrdersPage';

vi.mock('../api/queries',()=>({
  keys:{incoming:['incoming'],marketOrders:['market-orders']},
  endpoints:{
    incoming:vi.fn(),
    marketOrders:vi.fn(),
  },
}));

import {endpoints} from '../api/queries';

const now=new Date('2026-08-19T10:00:00');
const product={id:1,sku:'SKU-1',name:'Telefon',stockQuantity:1,minimumStock:2,targetStock:5};
function order(status:string,expectedDeliveryDate?:string):IncomingOrder{
  return {id:7,product,quantity:3,status,expectedDeliveryDate};
}

const marketOrder:MarketOrder={
  id:3,
  draftId:12,
  totalCost:28500,
  status:'PENDING',
  createdAt:'2026-08-19T08:00:00',
  expectedDeliveryDate:'2026-08-23T10:00:00',
  items:[{
    id:1,
    product:{...product,name:'Dell Latitude 5440'},
    quantity:1,
    seller:{id:1,name:'ElectroShop'},
    price:28500,
    shippingFee:0,
    deliveryTimeDays:4,
  }],
};

function renderPage(){
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  return render(<QueryClientProvider client={client}><OrdersPage/></QueryClientProvider>);
}

describe('incoming order delivery state',()=>{
  it('gelecek tarihli pending siparişi Yolda olarak etiketler',()=>{
    render(<IncomingOrderCard order={order('PENDING','2026-08-20T10:00:00')} now={now}/>);
    expect(screen.getByText('Yolda')).toBeInTheDocument();
    expect(screen.getByText('Telefon')).toBeInTheDocument();
    expect(screen.getByText('İkmal #7')).toBeInTheDocument();
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

describe('market order status',()=>{
  it('PENDING durumunu Bekliyor olarak etiketler',()=>{
    expect(marketOrderStatus('PENDING').label).toBe('Bekliyor');
    render(<MarketOrderCard order={marketOrder}/>);
    expect(screen.getByText('Bekliyor')).toBeInTheDocument();
    expect(screen.getByText('Dell Latitude 5440 · 1 adet')).toBeInTheDocument();
    expect(screen.getByText('Sipariş #3 · Taslak #12')).toBeInTheDocument();
  });
});

describe('OrdersPage tabs',()=>{
  beforeEach(()=>{
    vi.mocked(endpoints.marketOrders).mockResolvedValue([marketOrder]);
    vi.mocked(endpoints.incoming).mockResolvedValue([order('PENDING','2026-08-20T10:00:00')]);
  });

  it('varsayılan olarak satın alma sekmesini gösterir',async()=>{
    renderPage();
    expect(await screen.findByText('Dell Latitude 5440 · 1 adet')).toBeInTheDocument();
    expect(screen.getByRole('tab',{name:'Satın alma'})).toHaveAttribute('aria-selected','true');
    expect(screen.queryByText('Telefon')).not.toBeInTheDocument();
  });

  it('Depo ikmali sekmesine geçince yalnızca ikmal listesini gösterir',async()=>{
    renderPage();
    await screen.findByText('Dell Latitude 5440 · 1 adet');
    fireEvent.click(screen.getByRole('tab',{name:'Depo ikmali'}));
    expect(await screen.findByText('Telefon')).toBeInTheDocument();
    expect(screen.getByText('İkmal #7')).toBeInTheDocument();
    expect(screen.queryByText('Dell Latitude 5440 · 1 adet')).not.toBeInTheDocument();
  });
});
