package com.smartstock.stockservice.service;

import com.smartstock.stockservice.model.IncomingOrder;
import com.smartstock.stockservice.model.IncomingOrderStatus;
import com.smartstock.stockservice.model.Product;
import com.smartstock.stockservice.repository.IncomingOrderRepository;
import com.smartstock.stockservice.repository.ProductRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Clock;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class OrderServiceTest {
    private static final LocalDateTime NOW = LocalDateTime.of(2026, 8, 19, 10, 0);

    @Mock IncomingOrderRepository orderRepository;
    @Mock ProductRepository productRepository;
    private OrderService service;

    @BeforeEach
    void setUp() {
        Clock clock = Clock.fixed(NOW.toInstant(ZoneOffset.UTC), ZoneOffset.UTC);
        service = new OrderService(orderRepository, productRepository, clock);
    }

    @Test
    void pastAndBoundaryDeliveriesAreReceivableAndIncreaseStock() {
        for (LocalDateTime delivery : List.of(NOW.minusMinutes(1), NOW)) {
            Product product = Product.builder().id(1L).stockQuantity(5).build();
            IncomingOrder order = pending(product, delivery);
            when(orderRepository.findById(7L)).thenReturn(Optional.of(order));
            when(orderRepository.save(order)).thenReturn(order);

            IncomingOrder received = service.receiveOrder(7L);

            assertEquals(8, product.getStockQuantity());
            assertEquals(IncomingOrderStatus.RECEIVED, received.getStatus());
            clearInvocations(productRepository, orderRepository);
        }
    }

    @Test
    void futureDeliveryIsRejectedWithoutChangingStockOrStatus() {
        Product product = Product.builder().id(1L).stockQuantity(5).build();
        IncomingOrder order = pending(product, NOW.plusMinutes(1));
        when(orderRepository.findById(7L)).thenReturn(Optional.of(order));

        DeliveryNotReadyException error = assertThrows(
                DeliveryNotReadyException.class, () -> service.receiveOrder(7L));

        assertEquals(NOW.plusMinutes(1), error.getExpectedDeliveryDate());
        assertEquals(5, product.getStockQuantity());
        assertEquals(IncomingOrderStatus.PENDING, order.getStatus());
        verifyNoInteractions(productRepository);
        verify(orderRepository, never()).save(any());
    }

    @Test
    void legacyNullDeliveryDateRemainsReceivable() {
        Product product = Product.builder().id(1L).stockQuantity(5).build();
        IncomingOrder order = pending(product, null);
        when(orderRepository.findById(7L)).thenReturn(Optional.of(order));
        when(orderRepository.save(order)).thenReturn(order);

        service.receiveOrder(7L);

        assertEquals(8, product.getStockQuantity());
        assertEquals(IncomingOrderStatus.RECEIVED, order.getStatus());
    }

    @Test
    void alreadyReceivedOrderCannotBeReceivedAgain() {
        IncomingOrder order = pending(Product.builder().stockQuantity(5).build(), NOW.minusDays(1));
        order.setStatus(IncomingOrderStatus.RECEIVED);
        when(orderRepository.findById(7L)).thenReturn(Optional.of(order));

        assertThrows(IllegalStateException.class, () -> service.receiveOrder(7L));
        verifyNoInteractions(productRepository);
    }

    @Test
    void receivableListContainsOnlyDuePendingOrders() {
        IncomingOrder past = pending(Product.builder().build(), NOW.minusSeconds(1));
        IncomingOrder boundary = pending(Product.builder().build(), NOW);
        IncomingOrder legacy = pending(Product.builder().build(), null);
        IncomingOrder future = pending(Product.builder().build(), NOW.plusSeconds(1));
        when(orderRepository.findByStatus(IncomingOrderStatus.PENDING))
                .thenReturn(List.of(past, boundary, legacy, future));

        assertEquals(List.of(past, boundary, legacy), service.getReceivableOrders());
    }

    private IncomingOrder pending(Product product, LocalDateTime expected) {
        return IncomingOrder.builder().id(7L).product(product).quantity(3)
                .status(IncomingOrderStatus.PENDING).expectedDeliveryDate(expected).build();
    }
}
