package com.smartstock.stockservice.service;

import com.smartstock.stockservice.model.IncomingOrder;
import com.smartstock.stockservice.model.IncomingOrderStatus;
import com.smartstock.stockservice.model.Product;
import com.smartstock.stockservice.repository.IncomingOrderRepository;
import com.smartstock.stockservice.repository.ProductRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class OrderService {

    private final IncomingOrderRepository incomingOrderRepository;
    private final ProductRepository productRepository;
    private final Clock clock;

    public List<IncomingOrder> getAllOrders() {
        return incomingOrderRepository.findAll();
    }

    public List<IncomingOrder> getPendingOrders() {
        return incomingOrderRepository.findByStatus(IncomingOrderStatus.PENDING);
    }

    public List<IncomingOrder> getReceivableOrders() {
        LocalDateTime now = LocalDateTime.now(clock);
        return getPendingOrders().stream()
                .filter(order -> isReceivable(order, now))
                .toList();
    }

    public Optional<IncomingOrder> getOrderById(Long id) {
        return incomingOrderRepository.findById(id);
    }

    /**
     * Creates an incoming replenishment order.
     */
    @Transactional
    public IncomingOrder createIncomingOrder(Long productId, Integer quantity, LocalDateTime expectedDeliveryDate) {
        Product product = productRepository.findById(productId)
                .orElseThrow(() -> new IllegalArgumentException("Product not found with id: " + productId));

        if (quantity <= 0) {
            throw new IllegalArgumentException("Quantity must be greater than 0");
        }

        IncomingOrder order = IncomingOrder.builder()
                .product(product)
                .quantity(quantity)
                .status(IncomingOrderStatus.PENDING)
                .expectedDeliveryDate(expectedDeliveryDate != null ? expectedDeliveryDate : LocalDateTime.now(clock).plusDays(3))
                .createdAt(LocalDateTime.now(clock))
                .build();

        return incomingOrderRepository.save(order);
    }

    /**
     * Marks an order as received, moving its quantity to the product's actual stock.
     */
    @Transactional
    public IncomingOrder receiveOrder(Long orderId) {
        IncomingOrder order = incomingOrderRepository.findById(orderId)
                .orElseThrow(() -> new IllegalArgumentException("Order not found with id: " + orderId));

        if (order.getStatus() == IncomingOrderStatus.RECEIVED) {
            throw new IllegalStateException("Order has already been received");
        }

        LocalDateTime now = LocalDateTime.now(clock);
        if (!isReceivable(order, now)) {
            throw new DeliveryNotReadyException(orderId, order.getExpectedDeliveryDate());
        }

        // Update product stock
        Product product = order.getProduct();
        product.setStockQuantity(product.getStockQuantity() + order.getQuantity());
        productRepository.save(product);

        // Update order status
        order.setStatus(IncomingOrderStatus.RECEIVED);
        return incomingOrderRepository.save(order);
    }

    private boolean isReceivable(IncomingOrder order, LocalDateTime now) {
        // Legacy rows may not have an expected date; preserving their receivability
        // avoids permanently stranding otherwise valid pending stock movements.
        return order.getExpectedDeliveryDate() == null || !order.getExpectedDeliveryDate().isAfter(now);
    }
}
