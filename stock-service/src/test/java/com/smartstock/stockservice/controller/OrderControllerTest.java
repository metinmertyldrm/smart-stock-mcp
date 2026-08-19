package com.smartstock.stockservice.controller;

import com.smartstock.stockservice.model.IncomingOrder;
import com.smartstock.stockservice.service.DeliveryNotReadyException;
import com.smartstock.stockservice.service.OrderService;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDateTime;
import java.util.List;

import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class OrderControllerTest {
    private final OrderService service = mock(OrderService.class);
    private final MockMvc mvc = MockMvcBuilders.standaloneSetup(new OrderController(service)).build();

    @Test
    void readyOnlyUsesReceivableQueryWithoutChangingDefaultPendingList() throws Exception {
        when(service.getReceivableOrders()).thenReturn(List.of());
        when(service.getPendingOrders()).thenReturn(List.of(new IncomingOrder()));

        mvc.perform(get("/api/orders/pending").param("readyOnly", "true"))
                .andExpect(status().isOk()).andExpect(content().json("[]"));
        mvc.perform(get("/api/orders/pending"))
                .andExpect(status().isOk()).andExpect(jsonPath("$[0]").exists());
    }

    @Test
    void futureDeliveryReturnsConflictWithActionableDetail() throws Exception {
        LocalDateTime expected = LocalDateTime.of(2026, 8, 23, 10, 0);
        when(service.receiveOrder(9L)).thenThrow(new DeliveryNotReadyException(9L, expected));

        mvc.perform(post("/api/orders/9/receive"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.detail").value(org.hamcrest.Matchers.containsString("#9")))
                .andExpect(jsonPath("$.expectedDeliveryDate").value("2026-08-23T10:00:00"));
    }
}
