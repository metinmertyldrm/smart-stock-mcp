package com.smartstock.stockservice.service;

import com.smartstock.stockservice.dto.CreateDraftRequestDto;
import com.smartstock.stockservice.dto.MarketplaceComparisonResponseDto;
import com.smartstock.stockservice.dto.MarketplaceOfferDto;
import com.smartstock.stockservice.model.*;
import com.smartstock.stockservice.repository.*;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class MarketplaceService {

    private final MarketplaceSellerRepository sellerRepository;
    private final MarketplaceOfferRepository offerRepository;
    private final MarketplacePurchaseDraftRepository draftRepository;
    private final MarketplaceOrderRepository orderRepository;

    public List<MarketplaceSeller> getAllSellers() {
        return sellerRepository.findAll();
    }

    public List<MarketplacePurchaseDraft> getAllDrafts() {
        return draftRepository.findAll();
    }

    public Optional<MarketplacePurchaseDraft> getDraft(Long id) {
        return draftRepository.findById(id);
    }

    public List<MarketplaceOrder> getAllOrders() {
        return orderRepository.findAll();
    }

    public List<MarketplaceOffer> searchOffers(String query) {
        if (query == null || query.trim().isEmpty()) {
            return offerRepository.findAll();
        }
        String[] terms = query.trim().split("\\s+");
        List<MarketplaceOffer> all = offerRepository.findAll();
        return all.stream().filter(o -> {
            for (String term : terms) {
                String lowerTerm = term.toLowerCase();
                Product p = o.getProduct();
                MarketplaceSeller s = o.getSeller();
                boolean match = (p != null && (
                                    (p.getName() != null && p.getName().toLowerCase().contains(lowerTerm)) ||
                                    (p.getSku() != null && p.getSku().toLowerCase().contains(lowerTerm)) ||
                                    (p.getDescription() != null && p.getDescription().toLowerCase().contains(lowerTerm)) ||
                                    (p.getSubcategory() != null && p.getSubcategory().getName() != null && p.getSubcategory().getName().toLowerCase().contains(lowerTerm)) ||
                                    (p.getSubcategory() != null && p.getSubcategory().getCategory() != null && p.getSubcategory().getCategory().getName() != null && p.getSubcategory().getCategory().getName().toLowerCase().contains(lowerTerm)) ||
                                    (p.getModel() != null && p.getModel().getName() != null && p.getModel().getName().toLowerCase().contains(lowerTerm)) ||
                                    (p.getModel() != null && p.getModel().getBrand() != null && p.getModel().getBrand().getName() != null && p.getModel().getBrand().getName().toLowerCase().contains(lowerTerm))
                                )) ||
                                (s != null && s.getName() != null && s.getName().toLowerCase().contains(lowerTerm));
                if (!match) {
                    return false;
                }
            }
            return true;
        }).collect(java.util.stream.Collectors.toList());
    }

    public List<MarketplaceOffer> getOffersByProductId(Long productId) {
        if (productId == null) {
            return new ArrayList<>();
        }
        return offerRepository.findByProductId(productId);
    }

    public MarketplaceComparisonResponseDto compareOffers(Long productId) {
        List<MarketplaceOffer> offers = offerRepository.findByProductId(productId);
        if (offers.isEmpty()) {
            return MarketplaceComparisonResponseDto.builder()
                    .offers(new ArrayList<>())
                    .bestChoice(null)
                    .build();
        }

        int m = offers.size();
        int n = 3; // Total Cost, Delivery Time, Seller Rating
        double[][] x = new double[m][n];

        // Fill criteria values
        // Criteria 0: Total Cost (Price + Shipping) - Minimize (Cost)
        // Criteria 1: Delivery Time (Days) - Minimize (Cost)
        // Criteria 2: Seller Rating - Maximize (Benefit)
        for (int i = 0; i < m; i++) {
            MarketplaceOffer offer = offers.get(i);
            x[i][0] = offer.getPrice() + offer.getShippingFee();
            x[i][1] = offer.getDeliveryTimeDays();
            x[i][2] = offer.getSeller().getRating();
        }

        // 1. Calculate norm for normalization
        double[] norm = new double[n];
        for (int j = 0; j < n; j++) {
            double sumSquare = 0;
            for (int i = 0; i < m; i++) {
                sumSquare += x[i][j] * x[i][j];
            }
            norm[j] = Math.sqrt(sumSquare);
        }

        // Weights
        double[] w = {0.4, 0.3, 0.3}; // 40% Cost, 30% Delivery Speed, 30% Rating

        // 2. Normalized and Weighted matrix
        double[][] v = new double[m][n];
        for (int i = 0; i < m; i++) {
            for (int j = 0; j < n; j++) {
                if (norm[j] > 0) {
                    v[i][j] = w[j] * (x[i][j] / norm[j]);
                } else {
                    v[i][j] = 0.0;
                }
            }
        }

        // 3. Positive and Negative Ideal Solutions (PIS / NIS)
        double[] pis = new double[n];
        double[] nis = new double[n];

        // Criteria 0: Cost (Total Cost) - Minimize
        pis[0] = Double.MAX_VALUE;
        nis[0] = -Double.MAX_VALUE;
        for (int i = 0; i < m; i++) {
            if (v[i][0] < pis[0]) pis[0] = v[i][0];
            if (v[i][0] > nis[0]) nis[0] = v[i][0];
        }

        // Criteria 1: Cost (Delivery Time) - Minimize
        pis[1] = Double.MAX_VALUE;
        nis[1] = -Double.MAX_VALUE;
        for (int i = 0; i < m; i++) {
            if (v[i][1] < pis[1]) pis[1] = v[i][1];
            if (v[i][1] > nis[1]) nis[1] = v[i][1];
        }

        // Criteria 2: Benefit (Seller Rating) - Maximize
        pis[2] = -Double.MAX_VALUE;
        nis[2] = Double.MAX_VALUE;
        for (int i = 0; i < m; i++) {
            if (v[i][2] > pis[2]) pis[2] = v[i][2];
            if (v[i][2] < nis[2]) nis[2] = v[i][2];
        }

        // 4. Calculate separation distances and relative closeness
        double[] closeness = new double[m];
        for (int i = 0; i < m; i++) {
            double dPlus = 0;
            double dMinus = 0;
            for (int j = 0; j < n; j++) {
                dPlus += Math.pow(v[i][j] - pis[j], 2);
                dMinus += Math.pow(v[i][j] - nis[j], 2);
            }
            dPlus = Math.sqrt(dPlus);
            dMinus = Math.sqrt(dMinus);

            if (dPlus + dMinus > 0) {
                closeness[i] = dMinus / (dPlus + dMinus);
            } else {
                closeness[i] = 1.0;
            }
        }

        // Map to DTOs
        List<MarketplaceOfferDto> dtos = new ArrayList<>();
        for (int i = 0; i < m; i++) {
            MarketplaceOffer offer = offers.get(i);
            dtos.add(MarketplaceOfferDto.builder()
                    .id(offer.getId())
                    .productSku(offer.getProduct().getSku())
                    .productName(offer.getProduct().getName())
                    .seller(offer.getSeller())
                    .price(offer.getPrice())
                    .stockQuantity(offer.getStockQuantity())
                    .shippingFee(offer.getShippingFee())
                    .deliveryTimeDays(offer.getDeliveryTimeDays())
                    .topsisScore(closeness[i])
                    .build());
        }

        // Sort by:
        // 1. TOPSIS score descending
        // 2. Total Cost ascending (cheaper is better)
        // 3. Delivery Time days ascending (faster is better)
        // 4. Seller Rating descending (higher is better)
        dtos.sort((a, b) -> {
            int scoreCompare = Double.compare(b.getTopsisScore(), a.getTopsisScore());
            if (scoreCompare != 0) {
                return scoreCompare;
            }
            double costA = a.getPrice() + a.getShippingFee();
            double costB = b.getPrice() + b.getShippingFee();
            int costCompare = Double.compare(costA, costB);
            if (costCompare != 0) {
                return costCompare;
            }
            int deliveryCompare = Integer.compare(a.getDeliveryTimeDays(), b.getDeliveryTimeDays());
            if (deliveryCompare != 0) {
                return deliveryCompare;
            }
            return Double.compare(b.getSeller().getRating(), a.getSeller().getRating());
        });

        MarketplaceOfferDto bestChoice = dtos.get(0);

        return MarketplaceComparisonResponseDto.builder()
                .offers(dtos)
                .bestChoice(bestChoice)
                .build();
    }

    public List<MarketplaceOffer> getOffersForSku(String sku) {
        return offerRepository.findByProductSku(sku);
    }

    public boolean checkAvailability(Long offerId, Integer quantity) {
        Optional<MarketplaceOffer> offerOpt = offerRepository.findById(offerId);
        return offerOpt.map(offer -> offer.getStockQuantity() >= quantity).orElse(false);
    }

    @Transactional
    public MarketplacePurchaseDraft createDraft(CreateDraftRequestDto request) {
        if (request.getItems() == null || request.getItems().isEmpty()) {
            throw new IllegalArgumentException("Items list cannot be empty.");
        }

        double totalCost = 0.0;
        List<MarketplacePurchaseDraftItem> draftItems = new ArrayList<>();

        MarketplacePurchaseDraft draft = MarketplacePurchaseDraft.builder()
                .status(MarketplacePurchaseDraftStatus.PENDING)
                .totalCost(0.0)
                .build();

        for (CreateDraftRequestDto.Item item : request.getItems()) {
            MarketplaceOffer offer = offerRepository.findById(item.getOfferId())
                    .orElseThrow(() -> new IllegalArgumentException("No offer found with ID " + item.getOfferId()));

            if (offer.getStockQuantity() < item.getQuantity()) {
                throw new IllegalStateException("Insufficient stock for Offer ID " + item.getOfferId() + ". Available: " + offer.getStockQuantity());
            }

            double itemCost = (offer.getPrice() * item.getQuantity()) + offer.getShippingFee();
            totalCost += itemCost;

            MarketplacePurchaseDraftItem draftItem = MarketplacePurchaseDraftItem.builder()
                    .draft(draft)
                    .product(offer.getProduct())
                    .quantity(item.getQuantity())
                    .seller(offer.getSeller())
                    .price(offer.getPrice())
                    .shippingFee(offer.getShippingFee())
                    .deliveryTimeDays(offer.getDeliveryTimeDays())
                    .build();

            draftItems.add(draftItem);
        }

        draft.setTotalCost(totalCost);
        draft.setItems(draftItems);

        return draftRepository.save(draft);
    }

    @Transactional
    public MarketplaceOrder placeOrder(Long draftId) {
        MarketplacePurchaseDraft draft = draftRepository.findById(draftId)
                .orElseThrow(() -> new IllegalArgumentException("Draft ID " + draftId + " not found."));

        if (draft.getStatus() != MarketplacePurchaseDraftStatus.PENDING) {
            throw new IllegalStateException("Draft ID " + draftId + " is already in '" + draft.getStatus() + "' status.");
        }

        int maxDeliveryDays = 0;
        List<MarketplaceOrderItem> orderItems = new ArrayList<>();

        MarketplaceOrder order = MarketplaceOrder.builder()
                .draftId(draftId)
                .totalCost(draft.getTotalCost())
                .status(MarketplaceOrderStatus.PENDING)
                .createdAt(LocalDateTime.now())
                .expectedDeliveryDate(LocalDateTime.now()) // Placeholder, will set below
                .build();

        for (MarketplacePurchaseDraftItem draftItem : draft.getItems()) {
            MarketplaceOffer offer = offerRepository.findByProductSkuAndSellerId(draftItem.getProduct().getSku(), draftItem.getSeller().getId())
                    .orElseThrow(() -> new IllegalArgumentException("Offer for SKU '" + draftItem.getProduct().getSku() + "' not found on order placement."));

            if (offer.getStockQuantity() < draftItem.getQuantity()) {
                throw new IllegalStateException("Insufficient stock for SKU '" + draftItem.getProduct().getSku() + "' from Seller ID " + draftItem.getSeller().getId() + " on order placement.");
            }

            // Decrement Stock
            offer.setStockQuantity(offer.getStockQuantity() - draftItem.getQuantity());
            offerRepository.save(offer);

            if (draftItem.getDeliveryTimeDays() > maxDeliveryDays) {
                maxDeliveryDays = draftItem.getDeliveryTimeDays();
            }

            MarketplaceOrderItem orderItem = MarketplaceOrderItem.builder()
                    .order(order)
                    .product(draftItem.getProduct())
                    .quantity(draftItem.getQuantity())
                    .seller(draftItem.getSeller())
                    .price(draftItem.getPrice())
                    .shippingFee(draftItem.getShippingFee())
                    .deliveryTimeDays(draftItem.getDeliveryTimeDays())
                    .build();

            orderItems.add(orderItem);
        }

        order.setExpectedDeliveryDate(LocalDateTime.now().plusDays(maxDeliveryDays));
        order.setItems(orderItems);

        draft.setStatus(MarketplacePurchaseDraftStatus.CONFIRMED);
        draftRepository.save(draft);

        return orderRepository.save(order);
    }

    @Transactional
    public Optional<MarketplaceOrder> getOrderDetails(Long orderId) {
        Optional<MarketplaceOrder> orderOpt = orderRepository.findById(orderId);
        if (orderOpt.isEmpty()) {
            return Optional.empty();
        }

        MarketplaceOrder order = orderOpt.get();
        LocalDateTime now = LocalDateTime.now();
        Duration elapsed = Duration.between(order.getCreatedAt(), now);
        long seconds = elapsed.getSeconds();

        MarketplaceOrderStatus currentStatus = order.getStatus();
        MarketplaceOrderStatus newStatus = currentStatus;

        if (MarketplaceOrderStatus.PENDING.equals(currentStatus) && seconds > 30) {
            newStatus = MarketplaceOrderStatus.SHIPPED;
        }
        if (MarketplaceOrderStatus.SHIPPED.equals(newStatus) && seconds > 60) {
            newStatus = MarketplaceOrderStatus.DELIVERED;
        }

        if (!newStatus.equals(currentStatus)) {
            order.setStatus(newStatus);
            order = orderRepository.save(order);
        }

        return Optional.of(order);
    }
}
