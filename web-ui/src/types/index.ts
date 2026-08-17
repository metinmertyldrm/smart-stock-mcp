export interface Named {id:number;name:string}
export interface Product {id:number;sku:string;name:string;description?:string;stockQuantity:number;minimumStock:number;targetStock:number;warehouseInfo?:string;subcategory?:Named&{category?:Named};model?:Named&{brand?:Named}}
export interface DraftItem {id:number;product:Product;quantity:number;seller:Named;price:number;shippingFee:number;deliveryTimeDays:number}
export interface Draft {id:number;totalCost:number;status:string;createdAt?:string;items:DraftItem[]}
export interface IncomingOrder {id:number;product:Product;quantity:number;status:string;expectedDeliveryDate?:string;createdAt?:string}
export interface MarketOrder {id:number;draftId:number;totalCost:number;status:string;createdAt:string;expectedDeliveryDate?:string;items:DraftItem[]}
export interface Trace {stepId:string;tool:string;arguments:Record<string,unknown>;status:'success'|'running'|'failed';resultSummary?:string;durationMs?:number}
export interface ChatResponse {conversationId:string;permissionLevel:string;plan:{type:string;goal:string;steps:Array<{id:string;tool:string;arguments:Record<string,unknown>}>};trace:Trace[];finalAnswer:string;pendingDraftId?:number}
