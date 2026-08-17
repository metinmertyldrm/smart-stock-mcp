import {render,screen} from '@testing-library/react';import {it,expect} from 'vitest';import {StockBadge} from './StockBadge';
it('düşük stok metnini gösterir',()=>{render(<StockBadge product={{id:1,sku:'X',name:'X',stockQuantity:6,minimumStock:5,targetStock:10}}/>);expect(screen.getByText('Düşük')).toBeInTheDocument()});
