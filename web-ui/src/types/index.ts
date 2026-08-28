export interface Named {id:number;name:string}
export interface Product {id:number;sku:string;name:string;description?:string;stockQuantity:number;minimumStock:number;targetStock:number;warehouseInfo?:string;subcategory?:Named&{category?:Named};model?:Named&{brand?:Named}}
export interface DraftItem {id:number;product:Product;quantity:number;seller:Named;price:number;shippingFee:number;deliveryTimeDays:number}
export type DraftStatus='PENDING'|'CONFIRMED'|'REJECTED'
export interface Draft {id:number;totalCost:number;status:DraftStatus;createdAt?:string;items:DraftItem[]}
export interface IncomingOrder {id:number;product:Product;quantity:number;status:string;expectedDeliveryDate?:string;createdAt?:string}
export interface MarketOrder {id:number;draftId:number;totalCost:number;status:string;createdAt:string;expectedDeliveryDate?:string;items:DraftItem[]}
export interface DraftApprovalAudit {draftId:number;createdBy?:AuditActor|null;creatorRecordedAt?:string|null;approvedBy?:AuditActor|null;approvedAt?:string|null;orderId?:number|null}
export interface DraftApprovalResponse {success:true;draftId:number;order:MarketOrder;incoming:unknown;audit:DraftApprovalAudit}
export interface DraftMutationResponse {success:true;draftId:number;status?:DraftStatus;deleted?:boolean;draft?:Draft}
export type SafetyStatus='passed'|'warning'|'blocked'|'pending'
export interface SafetyCheck {label:string;status:SafetyStatus;detail:string;policyId?:string;checkedAt?:string}
export interface Explanation {requestSummary:string;originalRequest?:string;detectedIntent?:string;entities?:string[];missingInformation?:string[];ambiguities?:string[];assumptions?:string[];goalTitle:string;goalExplanation:string;goalReasons?:string[];alternativeExplanation?:string;confidence?:string;permissionExplanation:string;permissionSource?:string;permissionReason?:string;allowedActions?:string[];blockedActions?:string[];approvalExplanation?:string;riskLevel?:string;findings:string[];decisionSummary:string;changes?:string[];userNextAction?:string;rollback?:string;safetyChecks:SafetyCheck[];warnings:string[];repaired:boolean;repairSummary?:string}
export interface Trace {stepId:string;tool:string;toolVersion?:string;mcpServer?:string;mcpRequestId?:string;toolCallId?:string;parentStepId?:string;title?:string;purpose?:string;trigger?:string;dependency?:string;arguments:Record<string,unknown>;rawRequest?:unknown;inputSummary?:string;status:'success'|'running'|'failed'|'skipped';resultSummary?:string;rawResponse?:unknown;findings?:string[];interpretation?:string;impactOnDecision?:string;nextAction?:string;startedAt?:string;finishedAt?:string;durationMs?:number;retryCount?:number;timeoutMs?:number;errorCode?:string;error?:string}
export interface AuditActor {userId:string;username:string;role:'VIEWER'|'OPERATOR'|'MANAGER'|'ADMIN'}
export interface Telemetry {executionId?:string;traceId?:string;requestId?:string;planId?:string;model?:string;promptVersion?:string;fastRoute?:string|null;applicationVersion?:string;environment?:string;startedAt?:string;finishedAt?:string;durationMs?:number;missingFields?:string[];actor?:AuditActor}
export interface ChatResponse {conversationId:string;permissionLevel:string;plan:{type:string;goal:string;id?:string;steps:Array<{id:string;tool:string;arguments:Record<string,unknown>}>;detail?:string};trace:Trace[];finalAnswer:string;explanation?:Explanation;telemetry?:Telemetry;pendingDraftId?:number;pendingReceiveIds?:number[];succeeded?:boolean}
export type ChatProgressStatus='running'|'completed'|'failed'|'cancelled'
export type ChatProgressStage='interpreting'|'executing'|'responding'|'completed'|'failed'|'cancelled'
export interface ChatProgress {requestId:string;conversationId:string;status:ChatProgressStatus;stage:ChatProgressStage;message:string;cancellable:boolean;createdAt:string;updatedAt:string;error?:string|null}
export interface ConversationSummary {id:string;owner_id:string;title:string;created_at:string;updated_at:string}
export interface ChatMessage {id:string;conversationId:string;role:'user'|'assistant';content:string;status:'success'|'failed';createdAt:string;response?:ChatResponse}
export interface Conversation extends ConversationSummary {messages:ChatMessage[]}
