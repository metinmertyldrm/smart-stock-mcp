package com.smartstock.stockservice.model;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import java.util.ArrayList;
import java.util.List;
import java.time.LocalDateTime;

@Data
@NoArgsConstructor
@AllArgsConstructor
@Builder
@Entity
@Table(name = "marketplace_purchase_drafts")
public class MarketplacePurchaseDraft {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "total_cost", nullable = false)
    private Double totalCost;

    @Builder.Default
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private MarketplacePurchaseDraftStatus status = MarketplacePurchaseDraftStatus.PENDING;

    @Builder.Default
    @Column(name = "created_at")
    private LocalDateTime createdAt = LocalDateTime.now();

    @PrePersist
    void onCreate() {
        if (createdAt == null) createdAt = LocalDateTime.now();
    }

    @Builder.Default
    @OneToMany(mappedBy = "draft", cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.EAGER)
    private List<MarketplacePurchaseDraftItem> items = new ArrayList<>();
}
