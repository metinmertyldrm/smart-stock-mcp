package com.smartstock.stockservice.controller;

import com.smartstock.stockservice.dto.*;
import com.smartstock.stockservice.model.*;
import com.smartstock.stockservice.service.MarketplaceService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/marketplace")
@RequiredArgsConstructor
public class MarketplaceController {

    private final MarketplaceService marketplaceService;

    @GetMapping("/sellers")
    public ResponseEntity<List<MarketplaceSeller>> getAllSellers() {
        return ResponseEntity.ok(marketplaceService.getAllSellers());
    }

    @GetMapping("/offers")
    public ResponseEntity<List<MarketplaceOffer>> searchOffers(
            @RequestParam(required = false) String query,
            @RequestParam(required = false) Long productId) {
        if (productId != null) {
            return ResponseEntity.ok(marketplaceService.getOffersByProductId(productId));
        }
        return ResponseEntity.ok(marketplaceService.searchOffers(query));
    }

    @GetMapping("/offers/compare")
    public ResponseEntity<MarketplaceComparisonResponseDto> getOffersForProduct(@RequestParam Long productId) {
        return ResponseEntity.ok(marketplaceService.compareOffers(productId));
    }

    @GetMapping("/check-availability")
    public ResponseEntity<Boolean> checkAvailability(
            @RequestParam Long offerId,
            @RequestParam Integer quantity) {
        return ResponseEntity.ok(marketplaceService.checkAvailability(offerId, quantity));
    }

    @PostMapping("/drafts")
    public ResponseEntity<?> createDraft(@RequestBody CreateDraftRequestDto request) {
        try {
            MarketplacePurchaseDraft draft = marketplaceService.createDraft(request);
            return ResponseEntity.status(HttpStatus.CREATED).body(mapToDraftDto(draft));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(e.getMessage());
        } catch (IllegalStateException e) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body(e.getMessage());
        }
    }

    @GetMapping("/drafts")
    public ResponseEntity<List<MarketplacePurchaseDraftResponseDto>> getDrafts() {
        return ResponseEntity.ok(marketplaceService.getAllDrafts().stream().map(this::mapToDraftDto).toList());
    }

    @GetMapping("/drafts/{draftId}")
    public ResponseEntity<MarketplacePurchaseDraftResponseDto> getDraft(@PathVariable Long draftId) {
        return marketplaceService.getDraft(draftId).map(this::mapToDraftDto)
                .map(ResponseEntity::ok).orElse(ResponseEntity.notFound().build());
    }

    @PostMapping("/drafts/{draftId}/reject")
    public ResponseEntity<?> rejectDraft(@PathVariable Long draftId) {
        try {
            return ResponseEntity.ok(mapToDraftDto(marketplaceService.rejectDraft(draftId)));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(e.getMessage());
        } catch (IllegalStateException e) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body(e.getMessage());
        }
    }

    @DeleteMapping("/drafts/{draftId}")
    public ResponseEntity<?> deleteDraft(@PathVariable Long draftId) {
        try {
            marketplaceService.deleteDraft(draftId);
            return ResponseEntity.noContent().build();
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(e.getMessage());
        } catch (IllegalStateException e) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body(e.getMessage());
        }
    }

    @PostMapping("/orders")
    public ResponseEntity<?> placeOrder(@RequestBody PlaceOrderRequestDto request) {
        try {
            MarketplaceOrder order = marketplaceService.placeOrder(request.getDraftId());
            return ResponseEntity.status(HttpStatus.CREATED).body(mapToOrderDto(order));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(e.getMessage());
        } catch (IllegalStateException e) {
            return ResponseEntity.badRequest().body(e.getMessage());
        }
    }

    @GetMapping("/orders/{orderId}")
    public ResponseEntity<MarketplaceOrderResponseDto> getOrderDetails(@PathVariable Long orderId) {
        return marketplaceService.getOrderDetails(orderId)
                .map(order -> ResponseEntity.ok(mapToOrderDto(order)))
                .orElse(ResponseEntity.notFound().build());
    }

    @GetMapping("/orders")
    public ResponseEntity<List<MarketplaceOrderResponseDto>> getOrders() {
        return ResponseEntity.ok(marketplaceService.getAllOrders().stream().map(this::mapToOrderDto).toList());
    }

    private MarketplacePurchaseDraftResponseDto mapToDraftDto(MarketplacePurchaseDraft draft) {
        List<MarketplacePurchaseDraftItemResponseDto> items = draft.getItems().stream().map(item -> 
            MarketplacePurchaseDraftItemResponseDto.builder()
                .id(item.getId())
                .product(item.getProduct())
                .quantity(item.getQuantity())
                .seller(item.getSeller())
                .price(item.getPrice())
                .shippingFee(item.getShippingFee())
                .deliveryTimeDays(item.getDeliveryTimeDays())
                .build()
        ).collect(Collectors.toList());

        return MarketplacePurchaseDraftResponseDto.builder()
                .id(draft.getId())
                .totalCost(draft.getTotalCost())
                .status(draft.getStatus())
                .createdAt(draft.getCreatedAt())
                .items(items)
                .build();
    }

    private MarketplaceOrderResponseDto mapToOrderDto(MarketplaceOrder order) {
        List<MarketplaceOrderItemResponseDto> items = order.getItems().stream().map(item -> 
            MarketplaceOrderItemResponseDto.builder()
                .id(item.getId())
                .product(item.getProduct())
                .quantity(item.getQuantity())
                .seller(item.getSeller())
                .price(item.getPrice())
                .shippingFee(item.getShippingFee())
                .deliveryTimeDays(item.getDeliveryTimeDays())
                .build()
        ).collect(Collectors.toList());

        return MarketplaceOrderResponseDto.builder()
                .id(order.getId())
                .draftId(order.getDraftId())
                .totalCost(order.getTotalCost())
                .status(order.getStatus())
                .createdAt(order.getCreatedAt())
                .expectedDeliveryDate(order.getExpectedDeliveryDate())
                .items(items)
                .build();
    }
}
