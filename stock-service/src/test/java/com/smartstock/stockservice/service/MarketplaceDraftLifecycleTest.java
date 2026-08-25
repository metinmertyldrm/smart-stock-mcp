package com.smartstock.stockservice.service;

import com.smartstock.stockservice.model.MarketplacePurchaseDraft;
import com.smartstock.stockservice.model.MarketplacePurchaseDraftStatus;
import com.smartstock.stockservice.repository.MarketplaceOfferRepository;
import com.smartstock.stockservice.repository.MarketplaceOrderRepository;
import com.smartstock.stockservice.repository.MarketplacePurchaseDraftRepository;
import com.smartstock.stockservice.repository.MarketplaceSellerRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class MarketplaceDraftLifecycleTest {

    @Mock MarketplaceSellerRepository sellerRepository;
    @Mock MarketplaceOfferRepository offerRepository;
    @Mock MarketplacePurchaseDraftRepository draftRepository;
    @Mock MarketplaceOrderRepository orderRepository;

    private MarketplaceService service;

    @BeforeEach
    void setUp() {
        service = new MarketplaceService(
                sellerRepository,
                offerRepository,
                draftRepository,
                orderRepository);
    }

    @Test
    void pendingDraftCanBeRejected() {
        MarketplacePurchaseDraft draft = draft(12L, MarketplacePurchaseDraftStatus.PENDING);
        when(draftRepository.findById(12L)).thenReturn(Optional.of(draft));
        when(draftRepository.save(draft)).thenReturn(draft);

        MarketplacePurchaseDraft result = service.rejectDraft(12L);

        assertEquals(MarketplacePurchaseDraftStatus.REJECTED, result.getStatus());
        verify(draftRepository).save(draft);
    }

    @Test
    void confirmedDraftCannotBeRejected() {
        MarketplacePurchaseDraft draft = draft(13L, MarketplacePurchaseDraftStatus.CONFIRMED);
        when(draftRepository.findById(13L)).thenReturn(Optional.of(draft));

        assertThrows(IllegalStateException.class, () -> service.rejectDraft(13L));
        verify(draftRepository, never()).save(draft);
    }

    @Test
    void pendingOrRejectedDraftCanBeDeleted() {
        MarketplacePurchaseDraft pending = draft(14L, MarketplacePurchaseDraftStatus.PENDING);
        MarketplacePurchaseDraft rejected = draft(15L, MarketplacePurchaseDraftStatus.REJECTED);
        when(draftRepository.findById(14L)).thenReturn(Optional.of(pending));
        when(draftRepository.findById(15L)).thenReturn(Optional.of(rejected));

        service.deleteDraft(14L);
        service.deleteDraft(15L);

        verify(draftRepository).delete(pending);
        verify(draftRepository).delete(rejected);
    }

    @Test
    void confirmedDraftCannotBeDeleted() {
        MarketplacePurchaseDraft draft = draft(16L, MarketplacePurchaseDraftStatus.CONFIRMED);
        when(draftRepository.findById(16L)).thenReturn(Optional.of(draft));

        assertThrows(IllegalStateException.class, () -> service.deleteDraft(16L));
        verify(draftRepository, never()).delete(draft);
    }

    private MarketplacePurchaseDraft draft(Long id, MarketplacePurchaseDraftStatus status) {
        return MarketplacePurchaseDraft.builder().id(id).totalCost(100.0).status(status).build();
    }
}
