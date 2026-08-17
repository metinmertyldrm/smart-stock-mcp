package com.smartstock.stockservice.dto;

import com.smartstock.stockservice.model.MarketplacePurchaseDraftStatus;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import java.util.List;
import java.time.LocalDateTime;

@Data
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class MarketplacePurchaseDraftResponseDto {
    private Long id;
    private Double totalCost;
    private MarketplacePurchaseDraftStatus status;
    private LocalDateTime createdAt;
    private List<MarketplacePurchaseDraftItemResponseDto> items;
}
