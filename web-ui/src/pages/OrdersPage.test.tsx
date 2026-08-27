import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {IncomingOrderCard,MarketplaceOrderRow,OrdersPage} from './OrdersPage';
import {incomingDeliveryState} from '../utils/orders';
import {endpoints} from '../api/queries';
import type {IncomingOrder,MarketOrder} from '../types';

const now=new Date('2026-08-19T10:00:00');
const product={id:1,sku:'SKU-1',name:'Telefon',stockQuantity:1,minimumStock:2,targetStock:5};
function order(status:string,expectedDeliveryDate?:string):IncomingOrder{
  return {id:7,product,quantity:3,status,expectedDeliveryDate};
}

const marketplaceOrder:MarketOrder={
  id:4,
  draftId:5,
  totalCost:37850,
  status:'SHIPPED',
  createdAt:'2026-08-19T10:00:00',
  expectedDeliveryDate:'2026-08-21T10:00:00',
  items:[
    {id:1,product,quantity:1,seller:{id:1,name:'ElectroShop'},price:28500,shippingFee:0,deliveryTimeDays:2},
    {id:2,product:{...product,id:2,name:'Kablosuz Klavye'},quantity:3,seller:{id:2,name:'TeknoMarket'},price:3000,shippingFee:350,deliveryTimeDays:2},
  ],
};

afterEach(()=>vi.restoreAllMocks());

describe('incoming order delivery state',()=>{
  it('gelecek tarihli pending siparişi Yolda olarak etiketler',()=>{
    expect(incomingDeliveryState(order('PENDING','2026-08-20T10:00:00'),now).key).toBe('transit');
    render(<IncomingOrderCard order={order('PENDING','2026-08-20T10:00:00')} now={now}/>);
    expect(screen.getByText('Yolda')).toBeInTheDocument();
    expect(screen.getByText('Telefon')).toBeInTheDocument();
    expect(screen.getByText('İkmal #7 · 3 adet')).toBeInTheDocument();
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

describe('marketplace order presentation',()=>{
  it('durumu Türkçe ve siparişi kısa özetle gösterir',()=>{
    render(<MarketplaceOrderRow order={marketplaceOrder}/>);
    expect(screen.getByText('Kargoda')).toBeInTheDocument();
    expect(screen.getByText('2 ürün · 4 adet')).toBeInTheDocument();
    expect(screen.getByText('Telefon, Kablosuz Klavye')).toBeInTheDocument();
    expect(screen.getByText(/Taslak #5/)).toBeInTheDocument();
  });

  it('bekleyen siparişi teknik kod yerine Hazırlanıyor olarak gösterir',()=>{
    render(<MarketplaceOrderRow order={{...marketplaceOrder,status:'PENDING'}}/>);
    expect(screen.getByText('Hazırlanıyor')).toBeInTheDocument();
    expect(screen.queryByText('PENDING')).not.toBeInTheDocument();
  });
});

describe('orders page navigation and filters',()=>{
  it('iki süreci sekmelerle ayırır ve sekme değişince filtreyi sıfırlar',async()=>{
    vi.spyOn(endpoints,'marketOrders').mockResolvedValue([marketplaceOrder]);
    vi.spyOn(endpoints,'incoming').mockResolvedValue([order('PENDING','2026-08-20T10:00:00')]);
    const queryClient=new QueryClient({defaultOptions:{queries:{retry:false}}});
    render(<QueryClientProvider client={queryClient}><OrdersPage/></QueryClientProvider>);

    expect(await screen.findByText('Sipariş #4')).toBeInTheDocument();
    expect(screen.getByRole('tab',{name:/Siparişleri/})).toHaveAttribute('aria-selected','true');
    fireEvent.change(screen.getByLabelText('Siparişlerde ara'),{target:{value:'ElectroShop'}});
    expect(screen.getByText('Sipariş #4')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab',{name:/Depo ikmalleri/}));
    expect(screen.getByLabelText('Siparişlerde ara')).toHaveValue('');
    await waitFor(()=>expect(screen.getByText('İkmal #7 · 3 adet')).toBeInTheDocument());
    expect(screen.queryByText('Sipariş #4')).not.toBeInTheDocument();
  });
});
