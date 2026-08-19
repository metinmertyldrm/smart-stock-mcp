package com.smartstock.stockservice.service;

import java.time.LocalDateTime;

public class DeliveryNotReadyException extends IllegalStateException {
    private final LocalDateTime expectedDeliveryDate;

    public DeliveryNotReadyException(Long orderId, LocalDateTime expectedDeliveryDate) {
        super("İkmal siparişi #" + orderId + " henüz teslim alınamaz. Beklenen teslim tarihi: "
                + expectedDeliveryDate);
        this.expectedDeliveryDate = expectedDeliveryDate;
    }

    public LocalDateTime getExpectedDeliveryDate() {
        return expectedDeliveryDate;
    }
}
