package com.smartstock.stockservice.controller;

import com.smartstock.stockservice.dto.CreateOrderRequestDto;
import com.smartstock.stockservice.model.IncomingOrder;
import com.smartstock.stockservice.service.DeliveryNotReadyException;
import com.smartstock.stockservice.service.OrderService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/orders")
@RequiredArgsConstructor
public class OrderController {

    private final OrderService orderService;

    @GetMapping
    public ResponseEntity<List<IncomingOrder>> getAllOrders() {
        return ResponseEntity.ok(orderService.getAllOrders());
    }

    @GetMapping("/pending")
    public ResponseEntity<List<IncomingOrder>> getPendingOrders(
            @RequestParam(defaultValue = "false") boolean readyOnly) {
        return ResponseEntity.ok(readyOnly
                ? orderService.getReceivableOrders()
                : orderService.getPendingOrders());
    }

    @GetMapping("/{id}")
    public ResponseEntity<IncomingOrder> getOrderById(@PathVariable Long id) {
        return orderService.getOrderById(id)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    @PostMapping
    public ResponseEntity<IncomingOrder> createIncomingOrder(@RequestBody CreateOrderRequestDto request) {
        IncomingOrder order = orderService.createIncomingOrder(
                request.getProductId(),
                request.getQuantity(),
                request.getExpectedDeliveryDate()
        );
        return ResponseEntity.ok(order);
    }

    @PostMapping("/{id}/receive")
    public ResponseEntity<?> receiveOrder(@PathVariable Long id) {
        try {
            IncomingOrder order = orderService.receiveOrder(id);
            return ResponseEntity.ok(order);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.notFound().build();
        } catch (DeliveryNotReadyException e) {
            return ResponseEntity.status(409).body(new OrderErrorResponse(
                    e.getMessage(), e.getExpectedDeliveryDate()));
        } catch (IllegalStateException e) {
            return ResponseEntity.badRequest().body(new OrderErrorResponse(e.getMessage(), null));
        }
    }

    public record OrderErrorResponse(String detail, java.time.LocalDateTime expectedDeliveryDate) {}
}
